"""Offline retrieval study: replay the search step for every evaluation alert from
cached triage answers (see export_triage.py) and compare retrieval variants by
candidate recall@k. No LLM calls; only Pinecone queries.

These numbers come from the same 30 alerts used to score the system, so a variant
chosen here should be confirmed by a fresh run of run_eval.py.

    python eval/tune_retrieval.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from alert2playbook.config import Settings  # noqa: E402
from alert2playbook.kb import get_kb  # noqa: E402
from alert2playbook.retrieval import HybridRetriever, rrf  # noqa: E402

KS = (10, 15, 20, 25, 30)


def main() -> None:
    kb = get_kb()
    retriever = HybridRetriever(kb, Settings.from_env())
    alerts = {json.loads(l)["id"]: json.loads(l) for l in (ROOT / "eval" / "alerts.jsonl").read_text(encoding="utf-8").splitlines()}
    cache = json.loads((ROOT / "eval" / "results" / "triage-cache.json").read_text(encoding="utf-8"))

    dense_cache: dict[str, list[str]] = {}

    def dense(q: str) -> list[str]:
        if q not in dense_cache:
            dense_cache[q] = retriever.dense(q, 30)
        return dense_cache[q]

    def rank(aid: str, use_bm25=True, use_dense=True, pin_hints=True, fuse_hints=False, raw_dense=True) -> tuple[list[str], list[str]]:
        t, alert = cache[aid], alerts[aid]["alert"]
        hints = t.get("technique_hints", [])
        raw = alert[:600]
        queries = [t["summary"], *t["behaviors"], *hints, raw]
        rankings = {}
        for i, q in enumerate(queries):
            if use_dense and (raw_dense or q is not raw):
                rankings[f"d{i}"] = dense(q)
            if use_bm25:
                rankings[f"b{i}"] = [tid for tid, _ in kb.bm25(q, 30)]
        if fuse_hints:
            named = [tid for h in hints for tid in kb.by_name(h)]
            if named:
                rankings["hints"] = named
                rankings["hints2"] = named  # counted twice: an exact name is strong evidence
        pinned = list(kb.explicit_ids(" ".join(queries)))
        if pin_hints:
            for h in hints:
                pinned += [tid for tid in kb.by_name(h) if tid not in pinned]
        fused = [tid for tid, _, _ in rrf(rankings) if tid not in pinned]
        return pinned, fused

    variants = {
        "BM25 only, hints pinned (current BM25 mode)": dict(use_dense=False),
        "hybrid, hints pinned (current hybrid mode)": dict(),
        "dense only, hints pinned": dict(use_bm25=False),
        "hybrid, raw alert keyword-only": dict(raw_dense=False),
        "hybrid, hints fused not pinned": dict(pin_hints=False, fuse_hints=True),
        "hybrid, no hints at all": dict(pin_hints=False),
    }
    ids = sorted(cache)
    print(f"{len(ids)} alerts with cached triage\n")
    print(f"{'variant':<48}" + "".join(f"  R@{k:<3}" for k in KS))
    results = {}
    for name, kw in variants.items():
        ranks = {aid: rank(aid, **kw) for aid in ids}
        row = {}
        for k in KS:
            hits = 0
            for aid in ids:
                pinned, fused = ranks[aid]
                pinned = pinned[: k // 2]  # same cap as HybridRetriever.search
                candidates = (pinned + [t for t in fused if t not in pinned])[:k]
                hits += alerts[aid]["technique_id"] in candidates
            row[k] = round(100 * hits / len(ids), 1)
        results[name] = row
        print(f"{name:<48}" + "".join(f"  {row[k]:>5}" for k in KS))
    (ROOT / "eval" / "results" / "retrieval-study.json").write_text(json.dumps({"alerts": len(ids), "recall": results}, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
