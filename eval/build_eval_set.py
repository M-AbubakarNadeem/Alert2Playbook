"""Build a labelled evaluation set from Atomic Red Team (Red Canary, MIT licence).

Each atomic test is a real command that performs one ATT&CK technique. The command
is rendered as an EDR-style process alert; the label is the technique ID. Anything
that would leak the label is scrubbed first: technique IDs, the atomics folder path
(which contains the ID), and the words "atomic" / "Red Canary". Tests whose commands
contain well-known offensive tool names are skipped so antivirus does not flag the
dataset file.

    python eval/build_eval_set.py            # -> eval/alerts.jsonl (30 alerts)
"""

from __future__ import annotations

import json
import random
import re
import sys
from pathlib import Path

import httpx
import yaml

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw" / "atomics-index.yaml"
URL = "https://raw.githubusercontent.com/redcanaryco/atomic-red-team/master/atomics/Indexes/index.yaml"
OUT = ROOT / "eval" / "alerts.jsonl"

N, SEED = 30, 7
# No \b: IDs also hide inside names like T1020_exfil.txt or T1091Test1.txt.
TECH_ID = re.compile(r"(?<![A-Za-z0-9])T\d{4}(?:\.\d{3})?(?!\d)", re.IGNORECASE)
# A file whose whole name was the technique ID is left as "/.py" after scrubbing; such alerts carry no signal.
EMPTY_NAME = re.compile(r"[\\/][._-]*\.(?:py|exe|ps1|sh|bat|cmd|dll|bin|vbs|js|hta|txt|xml|zip)\b", re.IGNORECASE)
ARG = re.compile(r"#\{(\w+)\}")
URLS = re.compile(r"https?://[^\s\"'`)]*(?:redcanary|atomic-red-team)[^\s\"'`)]*", re.IGNORECASE)
DENY = re.compile(
    r"mimikatz|sekurlsa|lsadump|rubeus|sharphound|bloodhound|powersploit|invoke-mimi|kerberoast|"
    r"minidump|procdump|nanodump|lazagne|empire|metasploit|meterpreter|cobalt|psexec|impacket",
    re.IGNORECASE,
)

HOSTS = {
    "windows": [("WS-ACC-14", "CORP\\r.malik"), ("HR-LAPTOP-03", "CORP\\s.ahmed"), ("SRV-APP-02", "CORP\\svc_app")],
    "linux": [("web-07", "www-data"), ("db-02", "postgres"), ("build-01", "jenkins")],
    "macos": [("MBP-DESIGN-4", "z.hussain"), ("MBP-SALES-9", "a.raza")],
}
SHELLS = {
    "powershell": "C:\\Windows\\System32\\WindowsPowerShell\\v1.0\\powershell.exe",
    "command_prompt": "C:\\Windows\\System32\\cmd.exe",
    "sh": "/bin/sh",
    "bash": "/bin/bash",
}


def substitute(command: str, args: dict) -> str:
    defaults = {k: str(v.get("default", "")) for k, v in (args or {}).items()}
    for _ in range(5):  # defaults can reference other arguments
        new = ARG.sub(lambda m: defaults.get(m.group(1), m.group(0)), command)
        if new == command:
            break
        command = new
    return command


def scrub(command: str, platform: str) -> str:
    base = "C:\\ProgramData\\AppCache" if platform == "windows" else "/tmp/.cache"
    command = re.sub(r"\$?PathToAtomicsFolder", lambda _: base, command, flags=re.IGNORECASE)
    command = URLS.sub("https://cdn.example-files.net/payload.bin", command)
    command = TECH_ID.sub("", command)
    command = re.sub(r"atomic[\s_-]*red[\s_-]*team|red\s*canary", "app", command, flags=re.IGNORECASE)
    command = re.sub(r"atomic", "app", command, flags=re.IGNORECASE)
    command = re.sub(r"[ \t]+", " ", command)
    return command.strip()


def render(command: str, platform: str, executor: str, rng: random.Random) -> str:
    host, user = rng.choice(HOSTS[platform])
    shell = SHELLS.get(executor, executor)
    lines = command.splitlines()
    shown = "\n  ".join(lines[:12])
    if platform == "windows":
        return (
            f"[EDR] Suspicious process activity\nHost: {host} (Windows 11)  User: {user}\n"
            f"ParentImage: C:\\Windows\\explorer.exe\nImage: {shell}\nCommandLine:\n  {shown}"
        )
    return f"[EDR] Suspicious process activity\nHost: {host} ({platform})  User: {user}\nProcess: {shell}\nCommand:\n  {shown}"


def main() -> None:
    if not RAW.exists():
        RAW.parent.mkdir(parents=True, exist_ok=True)
        RAW.write_bytes(httpx.get(URL, timeout=120, follow_redirects=True).content)
    index = yaml.safe_load(RAW.read_text(encoding="utf-8"))

    sys.path.insert(0, str(ROOT))
    from alert2playbook.kb import get_kb

    kb = get_kb()
    seen, pool = set(), []
    for tactic, techniques in index.items():
        for tid, entry in techniques.items():
            if not kb.resolve(tid):
                continue  # technique no longer active in this ATT&CK version
            for test in entry.get("atomic_tests", []):
                guid, executor = test.get("auto_generated_guid"), test.get("executor", {})
                command = executor.get("command") or ""
                platforms = [p for p in test.get("supported_platforms", []) if p in HOSTS]
                if guid in seen or executor.get("name") not in SHELLS or not platforms or DENY.search(command):
                    continue
                seen.add(guid)
                command = scrub(substitute(command, test.get("input_arguments")), platforms[0])
                if 15 <= len(command) <= 700 and not EMPTY_NAME.search(command):
                    pool.append({"technique_id": kb.resolve(tid), "tactic": tactic, "platform": platforms[0],
                                 "executor": executor["name"], "command": command})

    rng = random.Random(SEED)
    rng.shuffle(pool)
    # One alert per technique, spread across tactics so no single tactic dominates.
    by_tactic: dict[str, list[dict]] = {}
    used: set[str] = set()
    for item in pool:
        if item["technique_id"] not in used:
            by_tactic.setdefault(item["tactic"], []).append(item)
            used.add(item["technique_id"])
    chosen, tactics = [], sorted(by_tactic)
    while len(chosen) < N and any(by_tactic.values()):
        for t in tactics:
            if by_tactic[t] and len(chosen) < N:
                chosen.append(by_tactic[t].pop())

    with OUT.open("w", encoding="utf-8") as fh:
        for i, item in enumerate(chosen):
            alert = render(item["command"], item["platform"], item["executor"], rng)
            fh.write(json.dumps({"id": f"art-{i:03d}", "technique_id": item["technique_id"], "tactic": item["tactic"],
                                 "platform": item["platform"], "alert": alert}) + "\n")
    print(f"{len(chosen)} alerts from {len(pool)} usable tests across {len(tactics)} tactics -> {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
