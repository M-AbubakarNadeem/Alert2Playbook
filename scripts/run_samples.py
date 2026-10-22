"""Run the live pipeline on the sample alerts and save each final state, so the demo
page can show a finished run instantly without spending LLM quota.

    python scripts/run_samples.py                 # all samples
    python scripts/run_samples.py lsass-access    # one sample
"""

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from alert2playbook.graph import new_state, stream_run  # noqa: E402
from alert2playbook.service import get_service  # noqa: E402

OUT = ROOT / "data" / "sample_runs"


def main(ids: list[str]) -> None:
    OUT.mkdir(exist_ok=True)
    samples = json.loads((ROOT / "data" / "samples.json").read_text(encoding="utf-8"))
    service = get_service()
    for sample in samples:
        if ids and sample["id"] not in ids:
            continue
        start = time.perf_counter()
        final = None
        for node, _, merged in stream_run(service.graph, new_state(sample["alert"]), source="samples"):
            final = merged
        wall = time.perf_counter() - start
        tokens = sum(s.get("input_tokens", 0) + s.get("output_tokens", 0) for s in final["steps"])
        final["saved_at"] = time.strftime("%Y-%m-%d")
        (OUT / f"{sample['id']}.json").write_text(json.dumps(final, indent=1), encoding="utf-8")
        mapped = ", ".join(m["technique_id"] for m in final["mapping"])
        print(
            f"{sample['id']:<28} {wall:5.1f}s {tokens:>6} tok  map=[{mapped}]  critic={final['review']['verdict']} "
            f"revisions={final.get('revisions', 0)} guard={final['guard']['score']}"
        )


if __name__ == "__main__":
    main(sys.argv[1:])
