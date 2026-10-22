"""Run the same alerts through the LangGraph pipeline and its CrewAI port, and compare
latency, tokens, LLM requests, failures and how often the two agree on the mapping.

Needs the separate benchmark environment (CrewAI pulls in many dependencies):

    python -m venv .venv-bench
    .venv-bench/Scripts/pip install crewai -r requirements.txt python-dotenv
    .venv-bench/Scripts/python benchmarks/compare.py --limit 4

Both sides use identical models, prompts, retrieval and checks; only the
orchestration framework differs. Runs alternate between frameworks so per-minute
rate limits hit both equally.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "benchmarks"))

from alert2playbook.config import Settings  # noqa: E402
from alert2playbook.graph import Deps, build_graph, new_state, stream_run  # noqa: E402
from alert2playbook.kb import get_kb  # noqa: E402
from alert2playbook.llm import GroqLLM, scrub_error  # noqa: E402
from alert2playbook.retrieval import HybridRetriever  # noqa: E402
from alert2playbook.schemas import PHASES  # noqa: E402

from crewai_flow import AlertFlow  # noqa: E402


def refs(playbook: dict) -> int:
    return sum(len(s.get("references", [])) for p in PHASES for s in playbook.get(p, []))


def run_langgraph(graph, alert: str) -> dict:
    t0 = time.perf_counter()
    final = None
    for _, _, merged in stream_run(graph, new_state(alert), source="benchmark"):
        final = merged
    llm_steps = [s for s in final["steps"] if "model" in s]
    return {
        "seconds": round(time.perf_counter() - t0, 2),
        "tokens": sum(s["input_tokens"] + s["output_tokens"] for s in llm_steps),
        "llm_steps": len(llm_steps),
        "mapping": [m["technique_id"] for m in final["mapping"]],
        "verdict": final["review"]["verdict"],
        "revisions": final.get("revisions", 0),
        "references": refs(final["playbook"]),
    }


def run_crewai(make_flow, alert: str) -> dict:
    flow = make_flow()
    t0 = time.perf_counter()
    flow.kickoff(inputs={"alert": alert})
    s = flow.state
    return {
        "seconds": round(time.perf_counter() - t0, 2),
        "tokens": sum(c["tokens"] for c in s.calls),
        "llm_steps": len(s.calls),
        "llm_requests": sum(c["requests"] for c in s.calls),
        "mapping": [m["technique_id"] for m in s.mapping],
        "verdict": s.review.get("verdict"),
        "revisions": s.revisions,
        "references": refs(s.playbook),
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=4)
    ap.add_argument("--only", choices=["both", "crewai", "langgraph"], default="both")
    ap.add_argument("--raised-caps", action="store_true",
                    help="give CrewAI larger reply-length caps: its Thought/Final Answer format needs more output tokens")
    args = ap.parse_args()

    settings = Settings.from_env()
    kb = get_kb()
    retriever = HybridRetriever(kb, settings)
    models = {"triage": settings.triage_model, "mapper": settings.mapper_model,
              "writer": settings.writer_model, "critic": settings.critic_model}
    graph = build_graph(Deps(kb=kb, retriever=retriever, llm=GroqLLM(settings.groq_api_key, models, settings.fallback_model),
                             max_revisions=settings.max_revisions, candidates=settings.candidates))

    # Raised caps: Qwen's free tier refuses any request that could exceed 1,000 output
    # tokens per minute, so Qwen roles stop at 1,000; gpt-oss roles get triple.
    caps = {"triage": 1000, "mapper": 1800, "writer": 6000, "critic": 1000} if args.raised_caps else None

    def make_flow():
        return AlertFlow(kb, retriever, models, settings.groq_api_key, settings.max_revisions, settings.candidates, caps)

    samples = json.loads((ROOT / "data" / "samples.json").read_text(encoding="utf-8"))[: args.limit]
    rows = []
    for i, sample in enumerate(samples):
        order = [("langgraph", lambda: run_langgraph(graph, sample["alert"])), ("crewai", lambda: run_crewai(make_flow, sample["alert"]))]
        if i % 2:
            order.reverse()
        if args.only != "both":
            order = [o for o in order if o[0] == args.only]
        row = {"id": sample["id"]}
        for name, fn in order:
            try:
                row[name] = fn()
            except Exception as exc:
                row[name] = {"error": f"{type(exc).__name__}: {scrub_error(str(exc))[:300]}"}
            time.sleep(20)  # let the per-minute token window recover before the other framework runs
        rows.append(row)
        print(json.dumps(row))

    def agg(name: str) -> dict | None:
        ran = [r[name] for r in rows if name in r]
        if not ran:
            return None
        ok = [x for x in ran if "error" not in x]
        if not ok:
            return {"runs": 0, "failures": len(ran)}
        return {
            "runs": len(ok),
            "failures": len(ran) - len(ok),
            "median_seconds": statistics.median(r["seconds"] for r in ok),
            "mean_tokens": round(statistics.mean(r["tokens"] for r in ok)),
            "mean_llm_steps": round(statistics.mean(r["llm_steps"] for r in ok), 2),
            "approved": sum(r["verdict"] == "approve" for r in ok),
            "mean_references": round(statistics.mean(r["references"] for r in ok), 1),
        }

    both = [r for r in rows if "error" not in r.get("langgraph", {"error": 1}) and "error" not in r.get("crewai", {"error": 1})]
    agreement = [len(set(r["langgraph"]["mapping"]) & set(r["crewai"]["mapping"])) / len(set(r["langgraph"]["mapping"]) | set(r["crewai"]["mapping"]))
                 for r in both]
    summary = {
        "date": time.strftime("%Y-%m-%d"),
        "alerts": len(rows),
        "models": models,
        "crewai_caps": caps or "same as LangGraph (alert2playbook.llm.MAX_TOKENS)",
        "langgraph": agg("langgraph"),
        "crewai": agg("crewai"),
        "mapping_jaccard": round(statistics.mean(agreement), 2) if agreement else None,
    }
    out = ROOT / "benchmarks" / ("results-raised-caps.json" if args.raised_caps else "results-same-caps.json")
    out.write_text(json.dumps({"summary": summary, "runs": rows}, indent=1), encoding="utf-8")
    print(json.dumps(summary, indent=1))


if __name__ == "__main__":
    main()
