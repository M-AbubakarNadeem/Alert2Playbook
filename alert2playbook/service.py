"""Wires the graph to its real dependencies (Groq, Pinecone, the bundled knowledge graph)."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache, partial

from .config import Settings
from .graph import Deps, build_graph
from .kb import KnowledgeGraph, get_kb
from .llm import GroqLLM, prompt_guard_score
from .retrieval import HybridRetriever


@dataclass
class Service:
    settings: Settings
    kb: KnowledgeGraph
    retriever: HybridRetriever
    graph: object

    @property
    def models(self) -> dict[str, str]:
        s = self.settings
        return {"triage": s.triage_model, "mapper": s.mapper_model, "writer": s.writer_model, "critic": s.critic_model}


@lru_cache(maxsize=1)
def get_service() -> Service:
    settings = Settings.from_env()
    if not settings.groq_api_key:
        raise RuntimeError("GROQ_API_KEY is not set")
    kb = get_kb()
    retriever = HybridRetriever(kb, settings)
    models = {
        "triage": settings.triage_model,
        "mapper": settings.mapper_model,
        "writer": settings.writer_model,
        "critic": settings.critic_model,
    }
    guard = partial(prompt_guard_score, settings.groq_api_key, settings.guard_model) if settings.guard_model else None
    deps = Deps(
        kb=kb,
        retriever=retriever,
        llm=GroqLLM(settings.groq_api_key, models, fallback_model=settings.fallback_model),
        max_revisions=settings.max_revisions,
        guard=guard,
        candidates=settings.candidates,
    )
    return Service(settings=settings, kb=kb, retriever=retriever, graph=build_graph(deps))
