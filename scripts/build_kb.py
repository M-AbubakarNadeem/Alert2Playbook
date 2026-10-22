"""Build the compact knowledge graph that Alert2Playbook ships with.

Inputs (downloaded into data/raw/ on first run):
  - MITRE ATT&CK Enterprise STIX 2.1 bundle
  - MITRE D3FEND countermeasures for every ATT&CK technique (one API call each, cached)

Output: data/kb.json.gz with techniques, mitigations, detection strategies and
D3FEND countermeasures linked by ID. The API loads it at cold start; it is small
enough (~1 MB gzipped) to bundle with the serverless function.

    python scripts/build_kb.py
"""

from __future__ import annotations

import gzip
import json
import re
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw"
D3F_CACHE = RAW / "d3fend"
OUT = ROOT / "data" / "kb.json.gz"

STIX_URL = "https://raw.githubusercontent.com/mitre-attack/attack-stix-data/master/{domain}/{domain}.json"
# Enterprise covers IT; ICS covers OT (PLCs, HMIs, engineering workstations).
DOMAINS = ("enterprise-attack", "ics-attack")
D3F_URL = "https://d3fend.mitre.org/api/offensive-technique/attack/{tid}.json"

CITATION = re.compile(r"\(Citation:[^)]*\)")
MD_LINK = re.compile(r"\[([^\]]+)\]\([^)]*\)")
HTML_TAG = re.compile(r"</?code>")


def clean(text: str | None, limit: int | None = None) -> str:
    text = CITATION.sub("", text or "")
    text = MD_LINK.sub(r"\1", text)
    text = HTML_TAG.sub("", text)
    text = re.sub(r"\s+", " ", text).strip()
    if limit and len(text) > limit:
        cut = text[:limit]
        text = cut[: cut.rfind(". ") + 1] or cut
    return text


def external_id(obj: dict) -> str | None:
    for ref in obj.get("external_references", []):
        if ref.get("source_name") == "mitre-attack":
            return ref.get("external_id")
    return None


def active(obj: dict) -> bool:
    return not obj.get("revoked") and not obj.get("x_mitre_deprecated")


def load_stix(domain: str) -> list[dict]:
    path = RAW / f"{domain}.json"
    if not path.exists():
        RAW.mkdir(parents=True, exist_ok=True)
        print(f"downloading {domain} STIX bundle ...")
        path.write_bytes(httpx.get(STIX_URL.format(domain=domain), timeout=120, follow_redirects=True).content)
    return json.loads(path.read_text(encoding="utf-8"))["objects"]


def fetch_d3fend(tid: str) -> list[dict]:
    """Return the distinct D3FEND countermeasures mapped to one ATT&CK technique."""
    cache = D3F_CACHE / f"{tid}.json"
    if cache.exists():
        data = json.loads(cache.read_text(encoding="utf-8"))
    else:
        for attempt in range(4):
            resp = httpx.get(D3F_URL.format(tid=tid), timeout=60)
            if resp.status_code == 200:
                break
            time.sleep(2 * (attempt + 1))
        else:
            return []
        data = resp.json()
        cache.write_text(json.dumps(data), encoding="utf-8")

    seen: dict[str, dict] = {}
    for row in data.get("off_to_def", {}).get("results", {}).get("bindings", []):
        def_id = row.get("def_tech_id", {}).get("value")
        if not def_id or def_id in seen:
            continue
        seen[def_id] = {
            "id": def_id,
            "name": row.get("def_tech_label", {}).get("value", ""),
            "tactic": row.get("def_tactic_label", {}).get("value", ""),
            # The D3FEND site links techniques by ontology class name, not by D3-xxx ID.
            "slug": row.get("def_tech", {}).get("value", "").rsplit("#", 1)[-1],
        }
    return sorted(seen.values(), key=lambda d: (d["tactic"], d["id"]))


def main() -> None:
    bundles = {domain: load_stix(domain) for domain in DOMAINS}
    objs = [o for bundle in bundles.values() for o in bundle]
    by_ref = {o["id"]: o for o in objs}
    versions = {
        domain: next(o.get("x_mitre_version") for o in bundle if o["type"] == "x-mitre-collection")
        for domain, bundle in bundles.items()
    }
    version = versions["enterprise-attack"]

    techniques: dict[str, dict] = {}
    for domain, bundle in bundles.items():
        for o in bundle:
            if o["type"] != "attack-pattern" or not active(o):
                continue
            tid = external_id(o)
            if tid in techniques:
                continue
            techniques[tid] = {
                "id": tid,
                "domain": domain.removesuffix("-attack"),
                "name": o["name"],
                "tactics": [p["phase_name"] for p in o.get("kill_chain_phases", [])],
                "platforms": o.get("x_mitre_platforms", []),
                "description": clean(o.get("description"), 900),
                "parent": tid.split(".")[0] if "." in tid else None,
                "mitigations": [],
                "detections": [],
                "d3fend": [],
                "used_by": 0,
            }

    mitigations: dict[str, dict] = {}
    detections: dict[str, dict] = {}
    revoked_to: dict[str, str] = {}

    seen_rels: set[str] = set()
    for o in objs:
        if o["type"] != "relationship" or not active(o) or o["id"] in seen_rels:
            continue
        seen_rels.add(o["id"])
        src, tgt = by_ref.get(o["source_ref"]), by_ref.get(o["target_ref"])
        if not src or not tgt:
            continue
        rel = o["relationship_type"]

        if rel == "revoked-by" and src["type"] == "attack-pattern":
            revoked_to[external_id(src)] = external_id(tgt)
            continue

        if tgt["type"] != "attack-pattern" or not active(tgt):
            continue
        tech = techniques.get(external_id(tgt))
        if tech is None:
            continue

        if rel == "mitigates" and active(src):
            mid = external_id(src)
            mitigations.setdefault(mid, {"id": mid, "name": src["name"], "description": clean(src.get("description"), 400)})
            tech["mitigations"].append(mid)
        elif rel == "detects" and active(src):
            did = external_id(src)
            analytics = [by_ref[a] for a in src.get("x_mitre_analytic_refs", []) if a in by_ref]
            detections.setdefault(did, {
                "id": did,
                "name": src["name"],
                "analytics": [
                    {
                        "platforms": a.get("x_mitre_platforms", []),
                        "description": clean(a.get("description"), 350),
                        "log_sources": sorted({ls.get("name", "") for ls in a.get("x_mitre_log_source_references", [])} - {""}),
                    }
                    for a in analytics[:4]
                ],
            })
            tech["detections"].append(did)
        elif rel == "uses" and src["type"] in {"intrusion-set", "campaign", "malware", "tool"}:
            tech["used_by"] += 1

    # D3FEND still maps a few techniques that ATT&CK has since revoked, so keep both
    # the live IDs and the revoked ones and fold the latter into their replacements.
    D3F_CACHE.mkdir(parents=True, exist_ok=True)
    query_ids = sorted(set(techniques) | set(revoked_to))
    print(f"fetching D3FEND countermeasures for {len(query_ids)} technique IDs ...")
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = dict(zip(query_ids, pool.map(fetch_d3fend, query_ids)))
    for tid, counter in results.items():
        target = tid if tid in techniques else revoked_to.get(tid)
        if target in techniques:
            merged = {c["id"]: c for c in techniques[target]["d3fend"]}
            merged.update({c["id"]: c for c in counter})
            techniques[target]["d3fend"] = sorted(merged.values(), key=lambda d: (d["tactic"], d["id"]))

    for tech in techniques.values():
        tech["mitigations"] = sorted(set(tech["mitigations"]))
        tech["detections"] = sorted(set(tech["detections"]))

    d3fend_slugs = {c["id"]: c.pop("slug") for t in techniques.values() for c in t["d3fend"]}

    kb = {
        "attack_version": version,
        "domain_versions": versions,
        "techniques": techniques,
        "mitigations": mitigations,
        "detections": detections,
        "d3fend_slugs": d3fend_slugs,
        "revoked": revoked_to,
    }
    with gzip.open(OUT, "wt", encoding="utf-8") as fh:
        json.dump(kb, fh, separators=(",", ":"))

    with_d3f = sum(1 for t in techniques.values() if t["d3fend"])
    per_domain = {d: sum(1 for t in techniques.values() if t["domain"] == d.removesuffix("-attack")) for d in DOMAINS}
    print(
        f"ATT&CK {versions}: {len(techniques)} techniques {per_domain}, {len(mitigations)} mitigations, "
        f"{len(detections)} detection strategies, {with_d3f} techniques with D3FEND countermeasures "
        f"-> {OUT.relative_to(ROOT)} ({OUT.stat().st_size / 1e6:.2f} MB)"
    )


if __name__ == "__main__":
    main()
