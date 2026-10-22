"""Pull the triage answers of a past evaluation run back out of LangSmith, so retrieval
can be studied offline without spending LLM tokens again.

    python eval/export_triage.py --hours 3     # -> eval/results/triage-cache.json
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from alert2playbook.config import Settings  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=float, default=3)
    args = ap.parse_args()
    Settings.from_env()
    from langsmith import Client

    alerts = {json.loads(l)["alert"]: json.loads(l)["id"] for l in (ROOT / "eval" / "alerts.jsonl").read_text(encoding="utf-8").splitlines()}
    client = Client()
    since = datetime.now(timezone.utc) - timedelta(hours=args.hours)
    cache: dict[str, dict] = {}
    for run in client.list_runs(project_name="alert2playbook", run_type="llm", start_time=since):
        if run.name != "triage" or run.error:
            continue
        messages = (run.inputs or {}).get("messages", [[]])
        flat = messages[0] if messages and isinstance(messages[0], list) else messages
        user = next((m.get("content") or m.get("kwargs", {}).get("content") for m in flat
                     if (m.get("type") or m.get("kwargs", {}).get("type") or m.get("role")) in ("human", "user")
                     or "HumanMessage" in str(m.get("id", ""))), "")
        alert = (user or "").removeprefix("<alert>\n").removesuffix("\n</alert>")
        if alert not in alerts:
            continue
        gen = (run.outputs or {}).get("generations", [[{}]])[0][0]
        msg = gen.get("message", {})
        calls = msg.get("kwargs", {}).get("tool_calls") or msg.get("tool_calls") or []
        if calls:
            cache.setdefault(alerts[alert], calls[0]["args"])
    out = ROOT / "eval" / "results" / "triage-cache.json"
    out.write_text(json.dumps(cache, indent=1), encoding="utf-8")
    print(f"recovered triage for {len(cache)} of {len(alerts)} alerts -> {out.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
