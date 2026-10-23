"""Wires the graph to its real dependencies (Groq, Pinecone, the bundled knowledge graph).

Two kinds of run share the same knowledge graph and search:
  - the owner's runs (demo code), on the owner's Groq key and per-role models
  - visitor runs, on the visitor's own provider, model and key, built per request
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache, partial

from .config import Settings
from .graph import Deps, build_graph
from .kb import KnowledgeGraph, get_kb
from .llm import GroqLLM, ProviderLLM, prompt_guard_score
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
def get_base() -> tuple[Settings, KnowledgeGraph, HybridRetriever]:
    """Settings, knowledge graph and search. Needs no model key."""
    settings = Settings.from_env()
    kb = get_kb()
    return settings, kb, HybridRetriever(kb, settings)


@lru_cache(maxsize=1)
def get_service() -> Service:
    settings, kb, retriever = get_base()
    if not settings.groq_api_key:
        raise RuntimeError("GROQ_API_KEY is not set")
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


def visitor_graph(provider, api_key: str, model: str) -> tuple[object, ProviderLLM]:
    """A graph that runs every agent on the visitor's model with the visitor's key.
    Returns the LLM too, so the caller can close its connection when the run ends."""
    settings, kb, retriever = get_base()
    llm = ProviderLLM(provider, api_key, model)
    # The prompt-injection guard model only exists on Groq.
    guard = partial(prompt_guard_score, api_key, settings.guard_model) if provider.has_guard and settings.guard_model else None
    deps = Deps(kb=kb, retriever=retriever, llm=llm, max_revisions=settings.max_revisions, guard=guard, candidates=settings.candidates)
    return build_graph(deps), llm
