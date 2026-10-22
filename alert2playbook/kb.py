"""The ATT&CK + D3FEND knowledge graph and its lexical (BM25) index.

Nodes are techniques, mitigations, detection strategies and D3FEND countermeasures.
Edges come from STIX relationships (mitigates, detects, subtechnique-of, revoked-by)
and from D3FEND's offensive-to-defensive inference. `neighborhood()` is the graph
walk the playbook writer is grounded on.
"""

from __future__ import annotations

import gzip
import json
import math
import re
from collections import Counter
from functools import lru_cache
from pathlib import Path

KB_PATH = Path(__file__).resolve().parents[1] / "data" / "kb.json.gz"

TECHNIQUE_ID = re.compile(r"\bT\d{4}(?:\.\d{3})?\b", re.IGNORECASE)
TOKEN = re.compile(r"[a-z0-9][a-z0-9_\-]{1,}")
STOPWORDS = frozenset(
    "the a an and or of to in on for by with from as is are was were be been may can this that these those it its "
    "into via such other use used using adversaries adversary attackers attacker".split()
)

# D3FEND tactics in the order an incident responder works through them.
D3FEND_PHASES = ("Detect", "Isolate", "Evict", "Restore", "Harden", "Deceive", "Model")


def tokenize(text: str) -> list[str]:
    return [t for t in TOKEN.findall(text.lower()) if t not in STOPWORDS]


class KnowledgeGraph:
    def __init__(self, data: dict):
        self.attack_version: str = data["attack_version"]
        self.domain_versions: dict[str, str] = data.get("domain_versions", {})
        self.techniques: dict[str, dict] = data["techniques"]
        self.mitigations: dict[str, dict] = data["mitigations"]
        self.detections: dict[str, dict] = data["detections"]
        self.revoked: dict[str, str] = data["revoked"]
        self.d3fend_slugs: dict[str, str] = data["d3fend_slugs"]
        self.children: dict[str, list[str]] = {}
        self._names: dict[str, list[str]] = {}
        for tid, tech in self.techniques.items():
            if tech["parent"]:
                self.children.setdefault(tech["parent"], []).append(tid)
            for name in {tech["name"].lower(), self.label(tid).lower()}:
                self._names.setdefault(name, []).append(tid)
        self._build_bm25()

    # ------------------------------------------------------------------ lookup

    def resolve(self, technique_id: str) -> str | None:
        """Normalise an ID and follow ATT&CK revocations to the live technique."""
        tid = technique_id.strip().upper()
        seen = set()
        while tid in self.revoked and tid not in seen:
            seen.add(tid)
            tid = self.revoked[tid]
        return tid if tid in self.techniques else None

    def technique(self, technique_id: str) -> dict | None:
        tid = self.resolve(technique_id)
        return self.techniques[tid] if tid else None

    def label(self, technique_id: str) -> str:
        tech = self.technique(technique_id)
        if tech is None:
            return technique_id
        if tech["parent"]:
            return f"{self.techniques[tech['parent']]['name']}: {tech['name']}"
        return tech["name"]

    def neighborhood(self, technique_id: str, max_detections: int = 2) -> dict | None:
        """Walk the graph around one technique: parent, tactics, mitigations,
        detection strategies with their analytics, and D3FEND countermeasures.
        Sub-techniques inherit their parent's mitigations and countermeasures
        when they have none of their own."""
        tid = self.resolve(technique_id)
        if tid is None:
            return None
        tech = self.techniques[tid]
        parent = self.techniques.get(tech["parent"]) if tech["parent"] else None

        mitigation_ids = tech["mitigations"] or (parent["mitigations"] if parent else [])
        d3fend = tech["d3fend"] or (parent["d3fend"] if parent else [])
        detection_ids = tech["detections"] or (parent["detections"] if parent else [])

        by_phase: dict[str, list[dict]] = {}
        for counter in d3fend:
            by_phase.setdefault(counter["tactic"], []).append(
                {"id": counter["id"], "name": counter["name"], "url": self.reference_url(counter["id"])}
            )

        return {
            "id": tid,
            "name": self.label(tid),
            "url": attack_url(tid),
            "domain": tech["domain"],
            "tactics": tech["tactics"],
            "platforms": tech["platforms"],
            "description": tech["description"],
            "parent": tech["parent"],
            "used_by": tech["used_by"],
            "mitigations": [
                {**self.mitigations[m], "url": self.reference_url(m)} for m in mitigation_ids if m in self.mitigations
            ],
            "detections": [
                {**self.detections[d], "url": self.reference_url(d)}
                for d in detection_ids[:max_detections]
                if d in self.detections
            ],
            "d3fend": {phase: by_phase[phase] for phase in D3FEND_PHASES if phase in by_phase},
        }

    # ------------------------------------------------------------------ lexical search

    def _doc_text(self, tech: dict) -> str:
        parent_name = self.techniques[tech["parent"]]["name"] if tech["parent"] else ""
        # Names carry the most signal, so they are repeated to weight them up.
        return " ".join([tech["name"]] * 3 + [parent_name] * 2 + [" ".join(tech["tactics"]), tech["description"]])

    def _build_bm25(self, k1: float = 1.4, b: float = 0.75) -> None:
        self._ids = list(self.techniques)
        docs = [Counter(tokenize(self._doc_text(self.techniques[t]))) for t in self._ids]
        self._lengths = [sum(d.values()) for d in docs]
        self._avg_len = sum(self._lengths) / len(docs)
        df = Counter(term for d in docs for term in d)
        n = len(docs)
        self._idf = {term: math.log(1 + (n - f + 0.5) / (f + 0.5)) for term, f in df.items()}
        self._docs = docs
        self._k1, self._b = k1, b

    def bm25(self, query: str, k: int = 20) -> list[tuple[str, float]]:
        terms = [t for t in tokenize(query) if t in self._idf]
        if not terms:
            return []
        scores = []
        for i, doc in enumerate(self._docs):
            norm = self._k1 * (1 - self._b + self._b * self._lengths[i] / self._avg_len)
            s = 0.0
            for term in terms:
                f = doc.get(term)
                if f:
                    s += self._idf[term] * f * (self._k1 + 1) / (f + norm)
            if s > 0:
                scores.append((self._ids[i], s))
        scores.sort(key=lambda x: x[1], reverse=True)
        return scores[:k]

    def reference_url(self, ref: str) -> str | None:
        """Link for any ID the playbook may cite."""
        if TECHNIQUE_ID.fullmatch(ref):
            return attack_url(ref)
        if ref.startswith("DET"):
            return f"https://attack.mitre.org/detectionstrategies/{ref}/"
        if ref.startswith("M"):
            return f"https://attack.mitre.org/mitigations/{ref}/"
        if ref in self.d3fend_slugs:
            return f"https://d3fend.mitre.org/technique/d3f:{self.d3fend_slugs[ref]}/"
        return None

    def by_name(self, name: str) -> list[str]:
        """Techniques whose name matches exactly, ignoring case. Accepts both
        'LSASS Memory' and 'OS Credential Dumping: LSASS Memory'."""
        return self._names.get(name.strip().lower(), [])

    def explicit_ids(self, text: str) -> list[str]:
        """Technique IDs written literally in the alert (some SIEM rules tag them)."""
        out = []
        for match in TECHNIQUE_ID.findall(text):
            tid = self.resolve(match)
            if tid and tid not in out:
                out.append(tid)
        return out


def attack_url(technique_id: str) -> str:
    return "https://attack.mitre.org/techniques/" + technique_id.replace(".", "/") + "/"


@lru_cache(maxsize=1)
def get_kb() -> KnowledgeGraph:
    with gzip.open(KB_PATH, "rt", encoding="utf-8") as fh:
        return KnowledgeGraph(json.load(fh))
