"""Create the Pinecone index and load every ATT&CK technique into it.

The index uses Pinecone's integrated embedding model (llama-text-embed-v2), so
Pinecone embeds both the records and the queries. Nothing to host, and the
serverless function stays small. Re-running is safe: records are upserted by ID.

    python scripts/index_pinecone.py
"""

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from pinecone import Pinecone  # noqa: E402

from alert2playbook.config import Settings  # noqa: E402
from alert2playbook.kb import get_kb  # noqa: E402

EMBED_MODEL = "llama-text-embed-v2"
BATCH = 90  # integrated-embedding upserts accept at most 96 records per call


def record(kb, tid: str) -> dict:
    t = kb.techniques[tid]
    text = (
        f"{kb.label(tid)}. Tactics: {', '.join(t['tactics'])}. Platforms: {', '.join(t['platforms'])}. "
        f"{t['description']}"
    )
    return {"_id": tid, "chunk_text": text, "name": kb.label(tid), "domain": t["domain"]}


def main() -> None:
    settings = Settings.from_env()
    if not settings.pinecone_api_key:
        sys.exit("PINECONE_API_KEY is not set")
    pc = Pinecone(api_key=settings.pinecone_api_key)
    name = settings.pinecone_index

    if not pc.has_index(name):
        print(f"creating index {name} ({EMBED_MODEL}, aws us-east-1) ...")
        pc.create_index_for_model(
            name=name,
            cloud="aws",
            region="us-east-1",
            embed={"model": EMBED_MODEL, "field_map": {"text": "chunk_text"}},
        )
    host = pc.describe_index(name).host
    index = pc.Index(host=host)

    kb = get_kb()
    records = [record(kb, tid) for tid in kb.techniques]
    for i in range(0, len(records), BATCH):
        for attempt in range(5):
            try:
                index.upsert_records(namespace=settings.pinecone_namespace, records=records[i : i + BATCH])
                break
            except Exception as exc:  # free tier embeds at a limited rate; back off and retry
                wait = 10 * (attempt + 1)
                print(f"  batch {i // BATCH}: {str(exc)[:120]} - retrying in {wait}s")
                time.sleep(wait)
        print(f"  upserted {min(i + BATCH, len(records))}/{len(records)}")

    print(f"done. Set PINECONE_HOST={host} to skip the host lookup at cold start.")


if __name__ == "__main__":
    main()
