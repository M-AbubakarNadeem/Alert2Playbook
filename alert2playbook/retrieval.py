"""Hybrid retrieval over ATT&CK techniques.

Dense search runs on a Pinecone index with integrated embeddings (the text is
embedded by Pinecone, so no embedding model ships with the function). Lexical
search is BM25 over the same knowledge graph. The two rankings are merged with
reciprocal rank fusion, and IDs written literally in the alert go first.
If Pinecone is not configured or fails, BM25 alone keeps the pipeline working.
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

from .config import Settings
from .kb import KnowledgeGraph

log = logging.getLogger(__name__)


@dataclass
class Candidate:
    id: str
    name: str
    tactics: list[str]
    score: float
    sources: list[str] = field(default_factory=list)


def rrf(rankings: dict[str, list[str]], k: int = 60) -> list[tuple[str, float, list[str]]]:
    """Reciprocal rank fusion: score(d) = sum over rankings of 1 / (k + rank)."""
    scores: dict[str, float] = {}
    sources: dict[str, list[str]] = {}
    for name, ranking in rankings.items():
        for rank, doc_id in enumerate(ranking, start=1):
            scores[doc_id] = scores.get(doc_id, 0.0) + 1.0 / (k + rank)
            sources.setdefault(doc_id, []).append(name)
    fused = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    return [(doc_id, score, sources[doc_id]) for doc_id, score in fused]


class HybridRetriever:
    def __init__(self, kb: KnowledgeGraph, settings: Settings):
        self.kb = kb
        self.settings = settings
        self._index = None
        if settings.pinecone_api_key:
            try:
                from pinecone import Pinecone

                pc = Pinecone(api_key=settings.pinecone_api_key)
                self._index = pc.Index(host=settings.pinecone_host) if settings.pinecone_host else pc.Index(settings.pinecone_index)
            except Exception as exc:  # pragma: no cover - depends on network
                log.warning("Pinecone unavailable, falling back to BM25 only: %s", exc)

    @property
    def mode(self) -> str:
        return "hybrid (Pinecone dense + BM25, RRF)" if self._index is not None else "BM25 only"

    def dense(self, query: str, k: int) -> list[str]:
        if self._index is None:
            return []
        try:
            resp = self._index.search(
                namespace=self.settings.pinecone_namespace,
                top_k=k,
                inputs={"text": query},
                fields=["name"],
            )
        except Exception as exc:  # pragma: no cover - depends on network
            log.warning("Pinecone search failed, using BM25 only: %s", exc)
            return []
        hits = resp["result"]["hits"] if isinstance(resp, dict) else resp.result.hits
        return [h["_id"] if isinstance(h, dict) else h.id for h in hits]

    def search(self, queries: list[str], k: int = 12, name_hints: list[str] | None = None) -> list[Candidate]:
        """Run every query through both retrievers and fuse all rankings. IDs written
        in the alert and techniques whose exact name the triage agent suggested are
        placed first; the mapper still has to justify each one with evidence."""
        queries = [q for q in queries if q.strip()]
        rankings: dict[str, list[str]] = {}
        # Dense queries are network calls, so they run in parallel: the step costs
        # about one Pinecone round trip instead of one per query.
        with ThreadPoolExecutor(max_workers=8) as pool:
            dense_results = list(pool.map(lambda q: self.dense(q, 25), queries))
        for i, q in enumerate(queries):
            if dense_results[i]:
                rankings[f"dense:{i}"] = dense_results[i]
            if lexical := [tid for tid, _ in self.kb.bm25(q, 25)]:
                rankings[f"bm25:{i}"] = lexical

        pinned = [(tid, 1.0, ["explicit"]) for tid in self.kb.explicit_ids(" ".join(queries))]
        for hint in name_hints or []:
            for tid in self.kb.by_name(hint):
                if tid not in {p[0] for p in pinned}:
                    pinned.append((tid, 1.0, ["name"]))
        pinned = pinned[: k // 2]  # always leave room for what retrieval found on its own
        pinned_ids = {p[0] for p in pinned}
        ordered = pinned + [f for f in rrf(rankings) if f[0] not in pinned_ids]

        out = []
        for tid, score, srcs in ordered[:k]:
            tech = self.kb.techniques[tid]
            kinds = sorted({s.split(":")[0] for s in srcs})
            out.append(Candidate(id=tid, name=self.kb.label(tid), tactics=tech["tactics"], score=round(score, 4), sources=kinds))
        return out
