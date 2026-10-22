"""The multi-agent workflow, as a LangGraph state machine.

    screen -> triage -> retrieve -> map_attack -> enrich -> write_playbook -> critic
                                                              ^                 |
                                                              +---- revise -----+
                                                                                v
                                                                            finalize -> analyst

`screen` scores the untrusted alert with Llama Prompt Guard 2. `retrieve` is hybrid
RAG over ATT&CK; `enrich` is the knowledge-graph walk (ATT&CK mitigations and
detection strategies, D3FEND countermeasures) that grounds the writer. The critic
combines deterministic checks with an LLM review and can send the draft back once.
The run ends waiting for a human analyst, whose feedback re-enters at `write_playbook`.
"""

from __future__ import annotations

import operator
import time
from collections.abc import Callable, Iterator
from dataclasses import asdict, dataclass
from typing import Annotated, Any, TypedDict

from langgraph.graph import END, START, StateGraph

from . import prompts
from .kb import KnowledgeGraph
from .llm import StructuredLLM
from .retrieval import HybridRetriever
from .schemas import PHASES, Mapping, Playbook, Review, Triage

GUARD_THRESHOLD = 0.8


class State(TypedDict, total=False):
    alert: str
    analyst_feedback: str | None
    feedback_history: list[str]
    guard: dict
    triage: dict
    candidates: list[dict]
    mapping: list[dict]
    context: dict
    playbook: dict
    review: dict
    revisions: int
    status: str
    steps: Annotated[list[dict], operator.add]


@dataclass
class Deps:
    kb: KnowledgeGraph
    retriever: HybridRetriever
    llm: StructuredLLM
    max_revisions: int = 1
    guard: Callable[[str], float | None] | None = None
    # How many candidate techniques the mapper sees. eval/tune_retrieval.py shows
    # hybrid recall still rising between 15 (71%) and 25 (79%) while BM25 alone
    # flattens at 71%, for about 450 extra mapper input tokens.
    candidates: int = 25


def _ms(start: float) -> int:
    return round((time.perf_counter() - start) * 1000)


def allowed_references(context: dict) -> set[str]:
    refs: set[str] = set()
    for t in context["techniques"]:
        refs.add(t["id"])
        if t["parent"]:
            refs.add(t["parent"])
        refs.update(m["id"] for m in t["mitigations"])
        refs.update(d["id"] for d in t["detections"])
        refs.update(c["id"] for items in t["d3fend"].values() for c in items)
    return refs


def check_playbook(playbook: dict, allowed: set[str]) -> list[str]:
    """Deterministic checks the critic always runs before the LLM review."""
    issues = []
    for phase in PHASES:
        if not playbook.get(phase):
            issues.append(f"The {phase.replace('_', ' ')} phase has no steps.")
        for step in playbook.get(phase, []):
            unknown = [r for r in step.get("references", []) if r not in allowed]
            if unknown:
                issues.append(
                    f"Step '{step['action']}' cites IDs that are not in the knowledge-graph context: {', '.join(unknown)}."
                )
    return issues


def validate_mapping(matches: list, candidates: list[dict], kb: KnowledgeGraph) -> tuple[list[dict], list[str]]:
    """Keep only mapper picks that are real, de-duplicated retrieval candidates.
    Falls back to the top candidate (low confidence) when nothing valid is left."""
    allowed = {c["id"] for c in candidates}
    mapping, dropped = [], []
    for m in matches:
        tid = kb.resolve(m.technique_id)
        if tid in allowed and tid not in {x["technique_id"] for x in mapping}:
            mapping.append({**m.model_dump(), "technique_id": tid, "name": kb.label(tid)})
        else:
            dropped.append(m.technique_id)
    if not mapping and candidates:
        top = candidates[0]
        mapping = [{
            "technique_id": top["id"],
            "name": top["name"],
            "confidence": "low",
            "evidence": "No candidate was confirmed by the mapper; the top retrieval result is used.",
        }]
    return mapping[:3], dropped


def strip_unknown_references(playbook: dict, allowed: set[str]) -> dict:
    for phase in PHASES:
        for step in playbook.get(phase, []):
            step["references"] = [r for r in step.get("references", []) if r in allowed]
    return playbook


def build_graph(deps: Deps, mapping_only: bool = False):
    """`mapping_only` stops after map_attack; the evaluation uses it to score
    ATT&CK mapping without spending tokens on playbooks."""
    kb, llm = deps.kb, deps.llm

    def screen(state: State) -> dict:
        start = time.perf_counter()
        score = None
        if deps.guard is not None:
            try:
                score = deps.guard(state["alert"])
            except Exception:
                score = None
        flagged = score is not None and score >= GUARD_THRESHOLD
        return {
            "guard": {"score": score, "flagged": flagged},
            "steps": [{"node": "screen", "ms": _ms(start), "score": score, "flagged": flagged}],
        }

    def triage(state: State) -> dict:
        parsed, usage = llm.generate("triage", Triage, prompts.TRIAGE, prompts.triage_input(state["alert"]))
        return {"triage": parsed.model_dump(), "steps": [{"node": "triage", **usage}]}

    def retrieve(state: State) -> dict:
        start = time.perf_counter()
        t = state["triage"]
        hints = t.get("technique_hints", [])
        queries = [t["summary"], *t["behaviors"], *hints, state["alert"][:600]]
        candidates = deps.retriever.search(queries, k=deps.candidates, name_hints=hints)
        return {
            "candidates": [asdict(c) for c in candidates],
            "steps": [{"node": "retrieve", "ms": _ms(start), "mode": deps.retriever.mode, "queries": queries}],
        }

    def map_attack(state: State) -> dict:
        candidates = state["candidates"]
        parsed, usage = llm.generate(
            "mapper", Mapping, prompts.MAPPER, prompts.mapper_input(state["alert"], state["triage"], candidates, kb)
        )
        mapping, dropped = validate_mapping(parsed.matches, candidates, kb)
        return {"mapping": mapping, "steps": [{"node": "map_attack", **usage, "rejected_ids": dropped}]}

    def enrich(state: State) -> dict:
        start = time.perf_counter()
        techniques = [kb.neighborhood(m["technique_id"]) for m in state["mapping"]]
        context = {"techniques": [t for t in techniques if t]}
        refs = allowed_references(context)
        return {
            "context": {**context, "allowed_refs": sorted(refs)},
            "steps": [{"node": "enrich", "ms": _ms(start), "graph_nodes": len(refs)}],
        }

    def write_playbook(state: State) -> dict:
        parsed, usage = llm.generate("writer", Playbook, prompts.WRITER, prompts.writer_input(state))
        update: dict[str, Any] = {"playbook": parsed.model_dump(), "steps": [{"node": "write_playbook", **usage}]}
        review = state.get("review")
        if review and review.get("verdict") == "revise":
            update["revisions"] = state.get("revisions", 0) + 1
        if feedback := state.get("analyst_feedback"):
            update["analyst_feedback"] = None
            update["feedback_history"] = [*state.get("feedback_history", []), feedback]
        return update

    def critic(state: State) -> dict:
        allowed = set(state["context"]["allowed_refs"])
        auto = check_playbook(state["playbook"], allowed)
        parsed, usage = llm.generate("critic", Review, prompts.CRITIC, prompts.critic_input(state, auto))
        issues = auto + [i for i in parsed.issues if i not in auto]
        verdict = "revise" if auto else parsed.verdict
        review = {"verdict": verdict, "issues": issues[:6] if verdict == "revise" else [], "auto_issues": auto}
        return {"review": review, "steps": [{"node": "critic", **usage, "verdict": verdict}]}

    def finalize(state: State) -> dict:
        # Anything still citing an ID outside the context after the last revision is
        # stripped, so the analyst never sees a reference the graph cannot back.
        playbook = strip_unknown_references(state["playbook"], set(state["context"]["allowed_refs"]))
        open_issues = state["review"]["verdict"] == "revise"
        return {
            "playbook": playbook,
            "status": "awaiting_analyst_with_open_issues" if open_issues else "awaiting_analyst",
        }

    def route_start(state: State) -> str:
        return "write_playbook" if state.get("playbook") and state.get("analyst_feedback") else "screen"

    def route_review(state: State) -> str:
        if state["review"]["verdict"] == "revise" and state.get("revisions", 0) < deps.max_revisions:
            return "write_playbook"
        return "finalize"

    g = StateGraph(State)
    for name, fn in [
        ("screen", screen),
        ("triage", triage),
        ("retrieve", retrieve),
        ("map_attack", map_attack),
        ("enrich", enrich),
        ("write_playbook", write_playbook),
        ("critic", critic),
        ("finalize", finalize),
    ]:
        g.add_node(name, fn)
    g.add_conditional_edges(START, route_start, ["screen", "write_playbook"])
    g.add_edge("screen", "triage")
    g.add_edge("triage", "retrieve")
    g.add_edge("retrieve", "map_attack")
    if mapping_only:
        g.add_edge("map_attack", END)
        return g.compile()
    g.add_edge("map_attack", "enrich")
    g.add_edge("enrich", "write_playbook")
    g.add_edge("write_playbook", "critic")
    g.add_conditional_edges("critic", route_review, ["write_playbook", "finalize"])
    g.add_edge("finalize", END)
    return g.compile()


def stream_run(graph, state: dict, source: str = "api") -> Iterator[tuple[str, dict, dict]]:
    """Yield (node, update, merged_state) after every node, for server-sent events.
    `source` tags the LangSmith trace (api, a2a, mcp, eval, samples)."""
    merged = dict(state)
    merged.setdefault("steps", [])
    kind = "analyst_feedback" if state.get("analyst_feedback") else "new_alert"
    config = {"run_name": "alert2playbook", "tags": [source, kind], "metadata": {"source": source, "kind": kind}}
    for chunk in graph.stream(state, config=config, stream_mode="updates"):
        for node, update in chunk.items():
            update = update or {}
            for key, value in update.items():
                merged[key] = merged.get(key, []) + value if key == "steps" else value
            yield node, update, merged


def new_state(alert: str) -> dict:
    return {"alert": alert, "revisions": 0, "steps": []}


def feedback_state(previous: dict, feedback: str) -> dict:
    """Re-enter the graph at the writer with the analyst's requested changes."""
    keep = ("alert", "guard", "triage", "candidates", "mapping", "context", "playbook", "feedback_history")
    state = {k: previous[k] for k in keep if k in previous}
    return {**state, "analyst_feedback": feedback, "revisions": 0, "review": None, "steps": []}
