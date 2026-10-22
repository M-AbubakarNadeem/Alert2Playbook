"""The Alert2Playbook pipeline rebuilt with CrewAI, for a like-for-like comparison.

Same steps, prompts, schemas, models, retrieval, knowledge graph and deterministic
checks as alert2playbook/graph.py. Only the orchestration differs: a CrewAI Flow
moves the state between steps, and every LLM step is a one-agent, one-task Crew
whose output is parsed into the same Pydantic schema (`output_pydantic`).

The prompt-injection guard is left out of both sides of the benchmark.
"""

from __future__ import annotations

import os
import time
from dataclasses import asdict

os.environ.setdefault("CREWAI_DISABLE_TELEMETRY", "true")
os.environ.setdefault("OTEL_SDK_DISABLED", "true")

from crewai import LLM, Agent, Crew, Task  # noqa: E402
from crewai.flow.flow import Flow, listen, or_, router, start  # noqa: E402
from pydantic import BaseModel, Field  # noqa: E402

from alert2playbook import prompts  # noqa: E402
from alert2playbook.graph import allowed_references, check_playbook, strip_unknown_references, validate_mapping  # noqa: E402
from alert2playbook.kb import KnowledgeGraph  # noqa: E402
from alert2playbook.llm import MAX_TOKENS  # noqa: E402
from alert2playbook.retrieval import HybridRetriever  # noqa: E402
from alert2playbook.schemas import Mapping, Playbook, Review, Triage  # noqa: E402

GROQ_OPENAI_URL = "https://api.groq.com/openai/v1"

ROLES = {
    "triage": ("SOC triage analyst", "Turn a raw alert into an accurate triage record", prompts.TRIAGE, Triage),
    "mapper": ("MITRE ATT&CK mapper", "Map the alert to the right ATT&CK techniques", prompts.MAPPER, Mapping),
    "writer": ("Senior incident responder", "Write a grounded, actionable response playbook", prompts.WRITER, Playbook),
    "critic": ("Playbook reviewer", "Catch unsafe or wrong steps before an analyst sees them", prompts.CRITIC, Review),
}


class FlowState(BaseModel):
    alert: str = ""
    triage: dict = Field(default_factory=dict)
    candidates: list = Field(default_factory=list)
    mapping: list = Field(default_factory=list)
    context: dict = Field(default_factory=dict)
    playbook: dict = Field(default_factory=dict)
    review: dict = Field(default_factory=dict)
    revisions: int = 0
    calls: list = Field(default_factory=list)


def make_llm(model: str, api_key: str, max_tokens: int) -> LLM:
    # Groq serves an OpenAI-compatible API, so CrewAI's native OpenAI client is used.
    extra = {"reasoning_effort": "low"} if model.startswith("openai/gpt-oss") else {}
    return LLM(model=model, provider="openai", base_url=GROQ_OPENAI_URL, api_key=api_key,
               temperature=0.1, max_tokens=max_tokens, **extra)


class AlertFlow(Flow[FlowState]):
    def __init__(self, kb: KnowledgeGraph, retriever: HybridRetriever, models: dict[str, str], api_key: str,
                 max_revisions: int = 1, candidates: int = 25, caps: dict[str, int] | None = None):
        super().__init__()
        self.kb, self.retriever = kb, retriever
        self.max_revisions, self.n_candidates = max_revisions, candidates
        caps = {**MAX_TOKENS, **(caps or {})}
        self.llms = {role: make_llm(model, api_key, caps[role]) for role, model in models.items()}

    def _crew(self, role: str, user: str):
        title, goal, system, schema = ROLES[role]
        agent = Agent(role=title, goal=goal, backstory=system, llm=self.llms[role], allow_delegation=False, max_iter=3, verbose=False)
        # The payload goes in through `inputs` so CrewAI's {placeholder} interpolation
        # never touches the braces inside alerts and JSON.
        task = Task(description="{payload}", expected_output=f"A valid {schema.__name__} object.", agent=agent, output_pydantic=schema)
        crew = Crew(agents=[agent], tasks=[task], verbose=False)
        t0 = time.perf_counter()
        out = crew.kickoff(inputs={"payload": user})
        usage = crew.usage_metrics
        self.state.calls.append({"step": role, "seconds": round(time.perf_counter() - t0, 2),
                                 "tokens": usage.total_tokens, "requests": usage.successful_requests})
        if out.pydantic is None:
            raise ValueError(f"{role}: CrewAI returned no structured output")
        return out.pydantic

    @start()
    def triage(self):
        self.state.triage = self._crew("triage", prompts.triage_input(self.state.alert)).model_dump()

    @listen(triage)
    def retrieve(self):
        t = self.state.triage
        hints = t.get("technique_hints", [])
        queries = [t["summary"], *t["behaviors"], *hints, self.state.alert[:600]]
        self.state.candidates = [asdict(c) for c in self.retriever.search(queries, k=self.n_candidates, name_hints=hints)]

    @listen(retrieve)
    def map_attack(self):
        parsed = self._crew("mapper", prompts.mapper_input(self.state.alert, self.state.triage, self.state.candidates, self.kb))
        self.state.mapping, _ = validate_mapping(parsed.matches, self.state.candidates, self.kb)

    @listen(map_attack)
    def enrich(self):
        context = {"techniques": [t for t in (self.kb.neighborhood(m["technique_id"]) for m in self.state.mapping) if t]}
        self.state.context = {**context, "allowed_refs": sorted(allowed_references(context))}

    @listen(or_(enrich, "revise"))
    def write_playbook(self):
        if self.state.review.get("verdict") == "revise":
            self.state.revisions += 1
        self.state.playbook = self._crew("writer", prompts.writer_input(self.state.model_dump())).model_dump()

    @router(write_playbook)
    def critic(self):
        auto = check_playbook(self.state.playbook, set(self.state.context["allowed_refs"]))
        parsed = self._crew("critic", prompts.critic_input(self.state.model_dump(), auto))
        verdict = "revise" if auto else parsed.verdict
        self.state.review = {"verdict": verdict, "issues": auto + [i for i in parsed.issues if i not in auto], "auto_issues": auto}
        return "revise" if verdict == "revise" and self.state.revisions < self.max_revisions else "done"

    @listen("done")
    def finalize(self):
        self.state.playbook = strip_unknown_references(self.state.playbook, set(self.state.context["allowed_refs"]))
        return self.state
