"""Collect the measured numbers into public/results.json for the "How good is it" page,
so the website never shows a number that is not in a results file.

    python scripts/export_results.py
"""

import json
import statistics
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from alert2playbook.kb import get_kb  # noqa: E402
from alert2playbook.schemas import PHASES  # noqa: E402


def load(path: str) -> dict:
    return json.loads((ROOT / path).read_text(encoding="utf-8"))


def eval_summary(path: str) -> dict:
    s = load(path)["summary"]
    k = s.get("candidates", 15)
    recall = s.get(f"candidate_recall@{k}", s.get("candidate_recall@15"))
    return {"alerts": s["alerts"], "candidates": k, "recall": recall, "exact1": s["exact@1"], "exact3": s["exact@3"],
            "family3": s["family@3"], "median_seconds": s["median_seconds"], "date": s["date"]}


def grounding() -> dict:
    kb = get_kb()
    cited = valid = 0
    for f in sorted((ROOT / "data" / "sample_runs").glob("*.json")):
        run = json.loads(f.read_text(encoding="utf-8"))
        allowed = set(run["context"]["allowed_refs"])
        refs = [r for p in PHASES for s in run["playbook"][p] for r in s["references"]]
        cited += len(refs)
        valid += sum(1 for r in refs if r in allowed and kb.reference_url(r))
    return {"cited": cited, "valid": valid}


def benchmark() -> dict:
    same = load("benchmarks/results-same-caps.json")
    raised = load("benchmarks/results-raised-caps.json")
    lg, cs, cr = same["summary"]["langgraph"], same["summary"]["crewai"], raised["summary"]["crewai"]
    lg_map = {r["id"]: set(r["langgraph"].get("mapping", [])) for r in same["runs"] if "error" not in r["langgraph"]}
    agree = [len(lg_map[r["id"]] & set(r["crewai"]["mapping"])) / len(lg_map[r["id"]] | set(r["crewai"]["mapping"]))
             for r in raised["runs"] if "error" not in r["crewai"] and r["id"] in lg_map]
    n = same["summary"]["alerts"]

    def finished(a):
        return f"{a['runs']} of {a['runs'] + a['failures']}"

    return {
        "alerts": n,
        "columns": ["LangGraph (this project)", "CrewAI, same reply limits", "CrewAI, bigger reply limits"],
        "rows": [
            ["Finished the job", finished(lg), finished(cs), finished(cr)],
            ["Typical time per alert", f"{lg['median_seconds']:.1f} s", f"{cs['median_seconds']:.1f} s" if cs.get("runs") else "-", f"{cr['median_seconds']:.1f} s"],
            ["Tokens per alert", f"~{lg['mean_tokens']:,}", f"~{cs['mean_tokens']:,}" if cs.get("runs") else "-", f"~{cr['mean_tokens']:,}"],
        ],
        "mapping_agreement": round(statistics.mean(agree), 2) if agree else None,
        "note": ("CrewAI wraps every AI call in its own role, goal and 'Thought / Final Answer' text. With Groq's models that "
                 "caused empty replies, answers cut off mid-JSON, and one model calling a tool when CrewAI had said not to. "
                 "Bigger reply limits fixed most runs but doubled the tokens. Small sample: read it as a direction, not a verdict."),
    }


def main() -> None:
    current = eval_summary("eval/results/mapping-hybrid-k25.json")
    tests = subprocess.run([sys.executable, "-m", "pytest", "--collect-only", "-q"], cwd=ROOT, capture_output=True, text=True).stdout
    n_tests = sum(1 for line in tests.splitlines() if "::" in line)
    study = load("eval/results/retrieval-study.json")
    names = {
        "BM25 only, hints pinned (current BM25 mode)": "Keyword search only",
        "dense only, hints pinned": "Meaning search only (Pinecone)",
        "hybrid, hints pinned (current hybrid mode)": "Keyword + meaning together (used now)",
    }
    ks = [10, 15, 20, 25, 30]
    lg = load("benchmarks/results-same-caps.json")["summary"]["langgraph"]
    out = {
        "date": current["date"],
        "tests": n_tests,
        # Median end-to-end time of the full pipeline in the benchmark runs.
        "live": {"seconds": round(lg["median_seconds"])},
        "eval": {"current": current, "baseline": eval_summary("eval/results/mapping-bm25-k15.json")},
        "grounding": grounding(),
        "retrieval": {"ks": ks, "rows": [{"name": label, "values": [study["recall"][key][str(k)] for k in ks]} for key, label in names.items()]},
        "benchmark": benchmark(),
    }
    (ROOT / "public" / "results.json").write_text(json.dumps(out, indent=1), encoding="utf-8")
    print(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
