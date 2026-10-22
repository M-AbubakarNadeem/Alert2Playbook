"""Offline tests: the real knowledge graph and retriever, a scripted LLM, no network."""

from __future__ import annotations

import json

import pytest

from alert2playbook.config import Settings
from alert2playbook.graph import Deps, build_graph, check_playbook, feedback_state, new_state, stream_run
from alert2playbook.kb import get_kb
from alert2playbook.retrieval import HybridRetriever, rrf
from alert2playbook.schemas import Mapping, Playbook, Review, Triage

ALERT = "WS-FIN-07 CORP\\svc_backup ran: vssadmin.exe delete shadows /all /quiet ; bcdedit /set {default} recoveryenabled No"


def settings(**kw) -> Settings:
    base = dict(
        groq_api_key=None, pinecone_api_key=None, pinecone_index="x", pinecone_host=None,
        pinecone_namespace="x", triage_model="t", mapper_model="m", writer_model="w",
        critic_model="c", fallback_model="f", guard_model=None, max_revisions=1, max_alert_chars=4000, candidates=25, demo_code=None,
    )
    return Settings(**{**base, **kw})


def step(action: str, refs: list[str]) -> dict:
    return {"action": action, "detail": "detail", "references": refs}


def playbook(refs: list[str]) -> Playbook:
    return Playbook(
        title="Recovery inhibition on WS-FIN-07",
        executive_summary="Shadow copies were deleted.",
        detection_and_analysis=[step("Confirm deletion", refs)],
        containment=[step("Isolate WS-FIN-07", [])],
        eradication=[step("Disable svc_backup", [])],
        recovery=[step("Restore from offline backup", [])],
        hardening=[step("Restrict vssadmin", [])],
        hunt_leads=[],
    )


class ScriptedLLM:
    """Returns canned structured outputs per role, in order, and records calls."""

    def __init__(self, script: dict[str, list]):
        self.script = {k: list(v) for k, v in script.items()}
        self.calls: list[tuple[str, str]] = []

    def generate(self, role, schema, system, user):
        self.calls.append((role, user))
        out = self.script[role].pop(0)
        assert isinstance(out, schema)
        return out, {"model": "stub", "ms": 1, "input_tokens": 10, "output_tokens": 5}


TRIAGE = Triage(
    summary="Volume shadow copies deleted and recovery disabled on WS-FIN-07.",
    platform="windows",
    severity="critical",
    severity_reason="Classic ransomware precursor.",
    behaviors=["deleting volume shadow copies", "disabling system recovery"],
    observables={"hosts": ["WS-FIN-07"], "users": ["CORP\\svc_backup"]},
)


@pytest.fixture(scope="module")
def kb():
    return get_kb()


def test_kb_covers_enterprise_and_ics(kb):
    assert kb.technique("T1490")["name"] == "Inhibit System Recovery"
    assert kb.technique("T0836")["domain"] == "ics"
    assert kb.resolve("t1490") == "T1490"


def test_revoked_ids_follow_to_replacement(kb):
    old, new = next(iter(kb.revoked.items()))
    assert kb.resolve(old) == kb.resolve(new)


def test_neighborhood_has_grounding(kb):
    n = kb.neighborhood("T1490")
    assert n["mitigations"] and n["detections"]
    assert all(m["id"].startswith("M") for m in n["mitigations"])


def test_rrf_rewards_agreement():
    fused = rrf({"a": ["x", "y", "z"], "b": ["y", "x", "w"]})
    assert {fused[0][0], fused[1][0]} == {"x", "y"}
    assert fused[-1][0] in {"z", "w"}


def test_retriever_finds_technique_from_behaviour(kb):
    r = HybridRetriever(kb, settings())
    ids = [c.id for c in r.search(["deleting volume shadow copies to inhibit system recovery"], k=10)]
    assert "T1490" in ids


def test_explicit_ids_come_first(kb):
    r = HybridRetriever(kb, settings())
    assert r.search(["rule tagged T1053.005 fired"], k=5)[0].id == "T1053.005"


def test_name_hints_pin_exact_technique_names(kb):
    r = HybridRetriever(kb, settings())
    out = r.search(["files renamed with a new extension"], k=10, name_hints=["Data Encrypted for Impact", "Not A Technique"])
    assert out[0].id == "T1486" and out[0].sources == ["name"]


def test_scrub_error_hides_account_ids_and_keys():
    from alert2playbook.llm import scrub_error

    msg = "Rate limit for model x in organization `org_00fakeorgid00000000000000`; key gsk_FAKEkey000000000000"
    out = scrub_error(msg)
    assert "00fakeorgid" not in out and "FAKEkey" not in out
    assert "org_<redacted>" in out and "gsk_<redacted>" in out


def test_check_playbook_flags_invented_ids():
    issues = check_playbook(playbook(["T1490", "M9999"]).model_dump(), {"T1490"})
    assert any("M9999" in i for i in issues)


def run_graph(kb, llm, state, max_revisions=1):
    graph = build_graph(Deps(kb=kb, retriever=HybridRetriever(kb, settings()), llm=llm, max_revisions=max_revisions))
    events = list(stream_run(graph, state))
    return [n for n, _, _ in events], events[-1][2]


def test_full_run_with_one_critic_revision(kb):
    llm = ScriptedLLM({
        "triage": [TRIAGE],
        "mapper": [Mapping(matches=[{"technique_id": "T1490", "confidence": "high", "evidence": "vssadmin.exe delete shadows"},
                                    {"technique_id": "T9999", "confidence": "low", "evidence": "made up"}])],
        "writer": [playbook(["T1490", "M9999"]), playbook(["T1490"])],
        "critic": [Review(verdict="approve"), Review(verdict="approve")],
    })
    nodes, final = run_graph(kb, llm, new_state(ALERT))

    assert nodes == ["screen", "triage", "retrieve", "map_attack", "enrich", "write_playbook", "critic",
                     "write_playbook", "critic", "finalize"]
    # The invented technique never reaches the mapping, and the invented mitigation
    # forced a revision even though the LLM critic approved the first draft.
    assert [m["technique_id"] for m in final["mapping"]] == ["T1490"]
    assert final["revisions"] == 1
    assert final["status"] == "awaiting_analyst"
    assert "M9999" in llm.calls[-2][1] and "reviewer rejected" in llm.calls[-2][1]
    assert sum(1 for s in final["steps"] if s["node"] == "write_playbook") == 2
    json.dumps(final)  # the whole state must survive the round trip to the browser


def test_invalid_refs_are_stripped_when_revisions_run_out(kb):
    llm = ScriptedLLM({
        "triage": [TRIAGE],
        "mapper": [Mapping(matches=[{"technique_id": "T1490", "confidence": "high", "evidence": "vssadmin"}])],
        "writer": [playbook(["T1490", "M9999"])],
        "critic": [Review(verdict="approve")],
    })
    _, final = run_graph(kb, llm, new_state(ALERT), max_revisions=0)
    assert final["status"] == "awaiting_analyst_with_open_issues"
    assert final["playbook"]["detection_and_analysis"][0]["references"] == ["T1490"]


def test_analyst_feedback_reenters_at_writer(kb):
    llm = ScriptedLLM({
        "triage": [TRIAGE],
        "mapper": [Mapping(matches=[{"technique_id": "T1490", "confidence": "high", "evidence": "vssadmin"}])],
        "writer": [playbook(["T1490"]), playbook(["T1490"])],
        "critic": [Review(verdict="approve"), Review(verdict="approve")],
    })
    _, first = run_graph(kb, llm, new_state(ALERT))
    nodes, second = run_graph(kb, llm, feedback_state(first, "Add a step to notify the finance manager."))
    assert nodes == ["write_playbook", "critic", "finalize"]
    assert "notify the finance manager" in llm.calls[-2][1]
    assert second["feedback_history"] == ["Add a step to notify the finance manager."]


def test_mapper_fallback_when_nothing_valid(kb):
    llm = ScriptedLLM({
        "triage": [TRIAGE],
        "mapper": [Mapping(matches=[{"technique_id": "T9999", "confidence": "high", "evidence": "x"}])],
        "writer": [playbook([])],
        "critic": [Review(verdict="approve")],
    })
    _, final = run_graph(kb, llm, new_state(ALERT))
    assert len(final["mapping"]) == 1 and final["mapping"][0]["confidence"] == "low"
