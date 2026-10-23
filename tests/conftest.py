import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# Tests are offline. Never load the owner's secrets file, so no test can reach the real
# Upstash database, Groq, Pinecone or the real dashboard login, whatever is in it.
os.environ["A2P_ENV_FILE"] = ""

_REAL_SERVICES = (
    "UPSTASH_REDIS_REST_URL", "UPSTASH_REDIS_REST_TOKEN", "KV_REST_API_URL", "KV_REST_API_TOKEN",
    "A2P_ADMIN_USER", "A2P_ADMIN_PASSWORD_HASH", "A2P_ADMIN_SESSION_SECRET", "A2P_FINGERPRINT_SECRET",
)


@pytest.fixture(autouse=True)
def offline_store(monkeypatch):
    """Every test starts with an empty in-memory store and no real credentials."""
    for name in _REAL_SERVICES:
        monkeypatch.delenv(name, raising=False)
    for name in list(os.environ):
        if name.endswith(("_REST_API_URL", "_REST_API_TOKEN")):  # Upstash under any prefix
            monkeypatch.delenv(name, raising=False)
    from alert2playbook.store import reset_store

    reset_store()
    yield
    reset_store()
