"""Small shared store: rate limits and the run log for the owner's dashboard.

Uses Upstash Redis over its REST API when UPSTASH_REDIS_REST_URL/TOKEN (or Vercel's
KV_REST_API_URL/TOKEN) are set, so limits hold across every serverless instance.
Without them, or if Upstash can't be reached, it falls back to memory in this one
process, which is enough for local runs and tests and still limits a single instance.

Nothing in here ever holds an API key. Run records carry a masked key and a
fingerprint only (see providers.mask and providers.fingerprint).
"""

from __future__ import annotations

import json
import os
import threading
import time
from collections import defaultdict

import httpx

RUNS_KEY = "a2p:runs"
MAX_RUNS_KEPT = 2000


class _Memory:
    def __init__(self):
        self.lock = threading.Lock()
        self.counters: dict[str, tuple[int, float]] = {}
        self.values: dict[str, tuple[str, float]] = {}
        self.runs: list[str] = []
        self.hashes: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))

    def incr(self, key: str, window: int) -> int:
        now = time.time()
        with self.lock:
            n, exp = self.counters.get(key, (0, 0.0))
            if exp <= now:
                n, exp = 0, now + window
            n += 1
            self.counters[key] = (n, exp)
            return n

    def set(self, key: str, value: str, ttl: int) -> None:
        with self.lock:
            self.values[key] = (value, time.time() + ttl)

    def get(self, key: str) -> str | None:
        with self.lock:
            v = self.values.get(key)
            return v[0] if v and v[1] > time.time() else None

    def log(self, record: str, provider: str, status: str) -> None:
        with self.lock:
            self.runs.insert(0, record)
            del self.runs[MAX_RUNS_KEPT:]
            self.hashes["a2p:count:provider"][provider] += 1
            self.hashes["a2p:count:status"][status] += 1

    def recent(self, n: int) -> list[str]:
        with self.lock:
            return self.runs[:n]

    def remove(self, raw: str, provider: str, status: str) -> bool:
        with self.lock:
            if raw not in self.runs:
                return False
            self.runs.remove(raw)
            self.hashes["a2p:count:provider"][provider] -= 1
            self.hashes["a2p:count:status"][status] -= 1
            return True

    def counts(self) -> dict:
        with self.lock:
            return {"provider": dict(self.hashes["a2p:count:provider"]), "status": dict(self.hashes["a2p:count:status"])}


def upstash_settings() -> tuple[str, str]:
    """The Upstash REST URL and token. Upstash's own names come first; then any prefix
    the Vercel integration was given (KV_, STORAGE_ or a custom one), which all end in
    _REST_API_URL and _REST_API_TOKEN. The read-only token is never used."""
    url, token = os.environ.get("UPSTASH_REDIS_REST_URL", ""), os.environ.get("UPSTASH_REDIS_REST_TOKEN", "")
    if url and token:
        return url, token
    for name in sorted(os.environ):
        if name.endswith("_REST_API_URL"):
            prefix = name[: -len("_REST_API_URL")]
            token = os.environ.get(f"{prefix}_REST_API_TOKEN", "")
            if os.environ[name].startswith("https://") and token:
                return os.environ[name], token
    return "", ""


class Store:
    def __init__(self):
        url, self.token = upstash_settings()
        self.url = url.rstrip("/")
        self.mem = _Memory()
        self._http = httpx.Client(timeout=httpx.Timeout(3.0, connect=2.0), follow_redirects=False) if self.url and self.token else None

    @property
    def backend(self) -> str:
        return "upstash" if self._http else "memory"

    def _pipe(self, *commands: list) -> list | None:
        """Runs Redis commands in one request. None means Upstash isn't available."""
        if not self._http:
            return None
        try:
            r = self._http.post(f"{self.url}/pipeline", json=list(commands), headers={"Authorization": f"Bearer {self.token}"})
            r.raise_for_status()
            out = r.json()
            if any("error" in item for item in out):
                return None
            return [item.get("result") for item in out]
        except (httpx.HTTPError, ValueError, TypeError):
            return None

    # ---- rate limits: fixed windows
    def hit(self, scope: str, ident: str, limit: int, window: int) -> bool:
        """Counts one use and says whether it is still within `limit` per `window` seconds."""
        bucket = int(time.time() // window)
        key = f"a2p:rl:{scope}:{ident}:{bucket}"
        res = self._pipe(["INCR", key], ["EXPIRE", key, window + 5])
        n = int(res[0]) if res else self.mem.incr(key, window)
        return n <= limit

    # ---- short-lived values (a key that passed its check recently)
    def remember(self, key: str, ttl: int) -> None:
        if self._pipe(["SET", f"a2p:{key}", "1", "EX", ttl]) is None:
            self.mem.set(key, "1", ttl)

    def recall(self, key: str) -> bool:
        res = self._pipe(["GET", f"a2p:{key}"])
        return bool(res[0]) if res is not None else self.mem.get(key) is not None

    # ---- run log
    def log_run(self, record: dict) -> None:
        raw = json.dumps(record, separators=(",", ":"), default=str)
        provider, status = str(record.get("provider", "?")), str(record.get("status", "?"))
        res = self._pipe(
            ["LPUSH", RUNS_KEY, raw],
            ["LTRIM", RUNS_KEY, 0, MAX_RUNS_KEPT - 1],
            ["HINCRBY", "a2p:count:provider", provider, 1],
            ["HINCRBY", "a2p:count:status", status, 1],
        )
        if res is None:
            self.mem.log(raw, provider, status)

    def recent_runs(self, n: int = MAX_RUNS_KEPT) -> list[dict]:
        res = self._pipe(["LRANGE", RUNS_KEY, 0, n - 1])
        raw = res[0] if res is not None else self.mem.recent(n)
        out = []
        for item in raw or []:
            try:
                out.append(json.loads(item))
            except (ValueError, TypeError):
                continue
        return out

    def delete_runs(self, ids: set[str]) -> int:
        """Removes the log entries with these ids. Returns how many were removed."""
        if not ids:
            return 0
        res = self._pipe(["LRANGE", RUNS_KEY, 0, -1])
        raw_items = res[0] if res is not None else self.mem.recent(MAX_RUNS_KEPT)
        removed = 0
        for raw in raw_items or []:
            try:
                rec = json.loads(raw)
            except (ValueError, TypeError):
                continue
            if rec.get("id") not in ids:
                continue
            provider, status = str(rec.get("provider", "?")), str(rec.get("status", "?"))
            if res is not None:
                out = self._pipe(["LREM", RUNS_KEY, 1, raw])
                if out and out[0]:
                    self._pipe(["HINCRBY", "a2p:count:provider", provider, -1], ["HINCRBY", "a2p:count:status", status, -1])
                    removed += 1
            else:
                removed += int(self.mem.remove(raw, provider, status))
        return removed

    def counts(self) -> dict:
        res = self._pipe(["HGETALL", "a2p:count:provider"], ["HGETALL", "a2p:count:status"])
        if res is None:
            return self.mem.counts()

        def pairs(flat):
            flat = flat or []
            return {flat[i]: int(flat[i + 1]) for i in range(0, len(flat) - 1, 2)}

        return {"provider": pairs(res[0]), "status": pairs(res[1])}


_store: Store | None = None
_lock = threading.Lock()


def get_store() -> Store:
    global _store
    with _lock:
        if _store is None:
            _store = Store()
        return _store


def reset_store() -> None:
    """For tests: forget the cached store so a new one reads the environment again."""
    global _store
    with _lock:
        _store = None
