"""Structured outputs for each agent. The LLM fills these through tool calling,
so every hand-off between agents is validated JSON rather than free text."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class Observables(BaseModel):
    hosts: list[str] = Field(default_factory=list, description="Hostnames or IPs of affected machines, as written in the alert")
    users: list[str] = Field(default_factory=list, description="Accounts involved")
    processes: list[str] = Field(default_factory=list, description="Process names or full command lines")
    files: list[str] = Field(default_factory=list, description="File paths and hashes")
    network: list[str] = Field(default_factory=list, description="Remote IPs, domains, URLs, ports")
    registry: list[str] = Field(default_factory=list, description="Registry keys or values")


class Triage(BaseModel):
    summary: str = Field(description="One or two plain-English sentences: what happened, where, and by whom")
    platform: Literal["windows", "linux", "macos", "cloud", "network", "unknown"]
    severity: Literal["low", "medium", "high", "critical"]
    severity_reason: str = Field(description="One sentence justifying the severity")
    behaviors: list[str] = Field(
        description="2 to 6 short, tool-agnostic phrases describing attacker behaviour, e.g. 'deleting volume shadow copies'. No technique IDs."
    )
    technique_hints: list[str] = Field(
        default_factory=list,
        description="2 to 6 MITRE ATT&CK technique names (names only, no IDs) that likely apply, e.g. 'Data Encrypted for Impact'",
    )
    observables: Observables


class TechniqueMatch(BaseModel):
    technique_id: str = Field(description="An ID copied exactly from the candidate list, e.g. T1053.005")
    confidence: Literal["high", "medium", "low"]
    evidence: str = Field(description="The exact fragment of the alert that supports this technique")


class Mapping(BaseModel):
    matches: list[TechniqueMatch] = Field(description="1 to 3 techniques, most confident first")


class PlaybookStep(BaseModel):
    action: str = Field(description="One imperative action, e.g. 'Isolate WS-FIN-07 from the network'")
    detail: str = Field(description="How to do it or what to check, specific to this alert")
    references: list[str] = Field(
        default_factory=list, description="IDs from the context this step relies on (T..., M..., DET..., D3-...)"
    )


class HuntLead(BaseModel):
    log_source: str = Field(description="A log source named in the detection analytics, e.g. 'WinEventLog:Security'")
    look_for: str = Field(description="What pattern to search for and why")


class Playbook(BaseModel):
    title: str
    executive_summary: str = Field(description="Three sentences maximum for a manager")
    detection_and_analysis: list[PlaybookStep]
    containment: list[PlaybookStep]
    eradication: list[PlaybookStep]
    recovery: list[PlaybookStep]
    hardening: list[PlaybookStep]
    hunt_leads: list[HuntLead]


PHASES = ("detection_and_analysis", "containment", "eradication", "recovery", "hardening")


class Review(BaseModel):
    verdict: Literal["approve", "revise"]
    issues: list[str] = Field(default_factory=list, description="At most 4 concrete, actionable problems; empty when approving")
