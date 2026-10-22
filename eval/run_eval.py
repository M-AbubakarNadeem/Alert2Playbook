"""Score ATT&CK mapping on the labelled Atomic Red Team alerts.

Runs screen-free triage -> retrieve -> map_attack on every alert and reports:
  - candidate recall@k:  the true technique is among the k retrieved candidates (A2P_CANDIDATES, default 25)
  - exact@1 / exact@3:   the true technique (sub-technique level) is the first / any mapped technique
  - family@3:            a mapped technique shares the true technique's parent (T1059.001 vs T1059.003)
plus latency and tokens.

    python eval/run_eval.py                    # retrieval as configured (hybrid if Pinecone is set)
    python eval/run_eval.py --bm25-only        # ablation without the dense retriever
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from alert2playbook.config import Settings  # noqa: E402
from alert2playbook.graph import Deps, build_graph, new_state, stream_run  # noqa: E402
from alert2playbook.kb import get_kb  # noqa: E402
from alert2playbook.llm import GroqLLM, scrub_error  # noqa: E402
from alert2playbook.retrieval import HybridRetriever  # noqa: E402

RESULTS = ROOT / "eval" / "results"


def family(tid: str) -> str:
    return tid.split(".")[0]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--bm25-only", action="store_true")
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    settings = Settings.from_env()
    if args.bm25_only:
        settings = replace(settings, pinecone_api_key=None)
    kb = get_kb()
    retriever = HybridRetriever(kb, settings)
    models = {"triage": settings.triage_model, "mapper": settings.mapper_model,
              "writer": settings.writer_model, "critic": settings.critic_model}
    llm = GroqLLM(settings.groq_api_key, models, settings.fallback_model)
    graph = build_graph(Deps(kb=kb, retriever=retriever, llm=llm, candidates=settings.candidates), mapping_only=True)

    rows = [json.loads(line) for line in (ROOT / "eval" / "alerts.jsonl").read_text(encoding="utf-8").splitlines()]
    if args.limit:
        rows = rows[: args.limit]

    # Triage answers quote the alert's command lines, so they go to a gitignored cache
    # (used by tune_retrieval.py) and never into the published results.
    cache_path = RESULTS / "triage-cache.json"
    triage_cache = json.loads(cache_path.read_text(encoding="utf-8")) if cache_path.exists() else {}

    per_alert = []
    for row in rows:
        gold = row["technique_id"]
        start = time.perf_counter()
        final = None
        try:
            for _, _, merged in stream_run(graph, new_state(row["alert"]), source="eval"):
                final = merged
        except Exception as exc:
            message = scrub_error(str(exc))
            print(f"{row['id']} ERROR {message[:120]}")
            per_alert.append({"id": row["id"], "gold": gold, "error": message[:300]})
            continue
        triage_cache[row["id"]] = final["triage"]
        mapped = [m["technique_id"] for m in final["mapping"]]
        cands = [c["id"] for c in final["candidates"]]
        result = {
            "id": row["id"], "gold": gold, "mapped": mapped,
            "recall": gold in cands,
            "exact1": bool(mapped) and mapped[0] == gold,
            "exact3": gold in mapped,
            "family3": family(gold) in {family(m) for m in mapped},
            "seconds": round(time.perf_counter() - start, 2),
            "tokens": sum(s.get("input_tokens", 0) + s.get("output_tokens", 0) for s in final["steps"]),
        }
        per_alert.append(result)
        print(f"{row['id']} gold={gold:<10} mapped={','.join(mapped):<32} "
              f"recall={'Y' if result['recall'] else '-'} exact3={'Y' if result['exact3'] else '-'} "
              f"family3={'Y' if result['family3'] else '-'} {result['seconds']}s")

    ok = [r for r in per_alert if "error" not in r]
    pct = lambda key: round(100 * sum(r[key] for r in ok) / len(ok), 1)  # noqa: E731
    summary = {
        "retrieval": retriever.mode,
        "models": {"triage": settings.triage_model, "mapper": settings.mapper_model},
        "alerts": len(rows), "errors": len(rows) - len(ok),
        "candidates": settings.candidates,
        f"candidate_recall@{settings.candidates}": pct("recall"),
        "exact@1": pct("exact1"), "exact@3": pct("exact3"), "family@3": pct("family3"),
        "median_seconds": statistics.median(r["seconds"] for r in ok),
        "mean_tokens": round(statistics.mean(r["tokens"] for r in ok)),
        "date": time.strftime("%Y-%m-%d"),
    }
    RESULTS.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(triage_cache, indent=1), encoding="utf-8")
    name ="bm25" if args.bm25_only or retriever.mode == "BM25 only" else "hybrid"
    (RESULTS / f"mapping-{name}-k{settings.candidates}.json").write_text(json.dumps({"summary": summary, "alerts": per_alert}, indent=1), encoding="utf-8")
    print(json.dumps(summary, indent=1))


if __name__ == "__main__":
    main()
