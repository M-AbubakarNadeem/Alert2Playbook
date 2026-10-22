"""The demo-code lock on the public API. The service is stubbed, so no keys or network."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import server

CODE = "amber-falcon-1234"


@pytest.fixture
def client(monkeypatch):
    stub = SimpleNamespace(
        settings=SimpleNamespace(demo_code=CODE, max_alert_chars=4000),
        models={"triage": "t"},
        retriever=SimpleNamespace(mode="BM25 only"),
    )
    monkeypatch.setattr(server, "get_service", lambda: stub)
    server._runs.clear()
    server._unlock_tries.clear()
    return TestClient(server.app)


def test_samples_and_health_are_open(client):
    assert client.get("/api/samples").status_code == 200
    assert client.get("/api/health").json()["live_runs"] == "need demo code"


def test_unlock_checks_the_code(client):
    assert client.post("/api/unlock", json={"code": "wrong"}).status_code == 401
    assert client.post("/api/unlock", json={"code": CODE}).json() == {"ok": True}


def test_unlock_attempts_are_limited(client):
    for _ in range(server.MAX_UNLOCK_TRIES):
        client.post("/api/unlock", json={"code": "wrong"})
    assert client.post("/api/unlock", json={"code": CODE}).status_code == 429


def test_live_run_needs_the_code(client):
    alert = {"alert": "Sysmon EID 1: schtasks /create /tn x /tr powershell.exe /sc onlogon"}
    r = client.post("/api/run", json=alert)
    assert r.status_code == 401 and "locked" in r.json()["detail"]
    r = client.post("/api/run", json=alert, headers={"X-Demo-Code": "wrong"})
    assert r.status_code == 401


def test_a2a_needs_the_code_and_card_says_so(client):
    msg = {"jsonrpc": "2.0", "id": 1, "method": "SendMessage",
           "params": {"message": {"messageId": "m1", "role": "ROLE_USER", "parts": [{"text": "alert text here"}]}}}
    assert client.post("/a2a", json=msg).status_code == 401
    card = client.get("/.well-known/agent-card.json").json()
    scheme = card["securitySchemes"]["demoCode"]["apiKeySecurityScheme"]
    assert scheme == {"description": scheme["description"], "location": "header", "name": "X-Demo-Code"}
    assert card["securityRequirements"] == [{"schemes": {"demoCode": {"list": []}}}]
