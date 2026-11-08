"""The public API: access rules, visitor keys, the run log and the owner's dashboard.
The model service and provider calls are stubbed, so no keys or network."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import server
from alert2playbook import admin
from alert2playbook import providers as P
from alert2playbook.kb import get_kb
from alert2playbook.store import get_store

CODE = "amber-falcon-1234"
GROQ_KEY = "gsk_" + "Z9y8X7w6" * 6
PASSWORD = "a long test password for the dashboard"
ALERT = "Sysmon EID 1: schtasks /create /tn x /tr powershell.exe /sc onlogon /ru SYSTEM"
VISITOR = {"name": "Sara Khan", "provider": "groq", "model": "openai/gpt-oss-120b"}


class FakeGraph:
    """Stands in for the LangGraph graph: one finished step and a playbook."""

    def stream(self, state, config, stream_mode):
        yield {"map_attack": {"mapping": [{"technique_id": "T1053.005"}], "steps": [{"node": "map_attack", "input_tokens": 30, "output_tokens": 5}]}}
        yield {"finalize": {"playbook": {"title": "t"}, "triage": {"severity": "high"}, "review": {"verdict": "approve"},
                             "status": "awaiting_analyst", "steps": [{"node": "finalize"}]}}


class FakeLLM:
    closed = False

    def close(self):
        FakeLLM.closed = True


@pytest.fixture
def client(monkeypatch):
    settings = SimpleNamespace(demo_code=CODE, max_alert_chars=4000, writer_model="openai/gpt-oss-120b")
    stub = SimpleNamespace(settings=settings, models={"triage": "t"}, retriever=SimpleNamespace(mode="BM25 only"), graph=FakeGraph())
    monkeypatch.setattr(server, "get_service", lambda: stub)
    monkeypatch.setattr(server, "get_base", lambda: (settings, get_kb(), None))
    monkeypatch.setattr(server, "visitor_graph", lambda provider, key, model: (FakeGraph(), FakeLLM()))
    monkeypatch.setattr(server, "verify_key", lambda provider, key: None)          # the provider says yes
    monkeypatch.setattr(server, "list_models", lambda provider, key: ["openai/gpt-oss-120b", "llama-3.3-70b-versatile"])
    monkeypatch.setenv("A2P_ADMIN_USER", "abubakar")
    monkeypatch.setenv("A2P_ADMIN_PASSWORD_HASH", admin.hash_password(PASSWORD))
    monkeypatch.setenv("A2P_ADMIN_SESSION_SECRET", "k" * 64)
    return TestClient(server.app)


def everything_stored() -> str:
    store = get_store()
    return json.dumps(store.recent_runs()) + json.dumps(store.mem.counters, default=str) + json.dumps(store.mem.values, default=str)


# ---------------------------------------------------------------- open endpoints and the owner lock

def test_samples_health_and_providers_are_open(client):
    assert client.get("/api/samples").status_code == 200
    h = client.get("/api/health").json()
    assert h["live_runs"] == "need demo code" and h["own_keys"] is True
    ids = [p["id"] for p in client.get("/api/providers").json()["providers"]]
    assert {"groq", "openai", "gemini", "anthropic", "openrouter", "cohere"} <= set(ids)


def test_security_headers_are_set(client):
    h = client.get("/api/health").headers
    assert h["x-content-type-options"] == "nosniff" and h["x-frame-options"] == "DENY"


def test_unlock_checks_the_code(client):
    assert client.post("/api/unlock", json={"code": "wrong"}).status_code == 401
    assert client.post("/api/unlock", json={"code": CODE}).json() == {"ok": True}


def test_unlock_attempts_are_limited(client):
    for _ in range(server.CODE_FAILS_PER_IP_15MIN):
        assert client.post("/api/unlock", json={"code": "wrong"}).status_code == 401
    assert client.post("/api/unlock", json={"code": CODE}).status_code == 429


def test_live_run_needs_a_code_or_a_key(client):
    r = client.post("/api/run", json={"alert": ALERT})
    assert r.status_code == 401 and "own API key" in r.json()["detail"]
    assert client.post("/api/run", json={"alert": ALERT}, headers={"X-Demo-Code": "wrong"}).status_code == 401
    assert client.post("/api/run", json={"alert": ALERT}, headers={"X-Demo-Code": CODE}).status_code == 200


def test_wrong_codes_on_any_endpoint_lock_out_every_endpoint(client):
    """Guesses at /api/run, /api/feedback and /a2a count together with /api/unlock, and once
    the limit is reached even the right code is refused, so trying many codes gets nowhere."""
    for i in range(server.CODE_FAILS_PER_IP_15MIN // 2):
        assert client.post("/api/run", json={"alert": ALERT}, headers={"X-Demo-Code": f"guess-{i}"}).status_code == 401
        assert client.post("/a2a", json={"jsonrpc": "2.0", "id": 1, "method": "SendMessage", "params": {}},
                           headers={"X-Demo-Code": f"guess-a2a-{i}"}).status_code == 401
    r = client.post("/api/run", json={"alert": ALERT}, headers={"X-Demo-Code": CODE})
    assert r.status_code == 429 and "wrong demo codes" in r.json()["detail"]
    assert client.post("/api/unlock", json={"code": CODE}).status_code == 429
    # someone at another address is not affected
    assert client.post("/api/unlock", json={"code": CODE}, headers={"x-real-ip": "203.0.113.9"}).json() == {"ok": True}


def test_only_real_guesses_count(client):
    """A request with no code at all, or with the right code, is not a guess."""
    for _ in range(server.CODE_FAILS_PER_IP_15MIN + 5):
        assert client.post("/api/run", json={"alert": ALERT}).status_code == 401
    for _ in range(server.CODE_FAILS_PER_IP_15MIN + 1):
        assert client.post("/api/unlock", json={"code": CODE}).status_code == 200
    assert client.post("/api/run", json={"alert": ALERT}, headers={"X-Demo-Code": CODE}).status_code == 200


def test_wrong_codes_are_also_limited_for_the_whole_site(client, monkeypatch):
    """Spreading guesses over many addresses hits the site-wide limit."""
    monkeypatch.setattr(server, "CODE_FAILS_PER_HOUR", 5)
    for i in range(5):
        assert client.post("/api/unlock", json={"code": "wrong"}, headers={"x-real-ip": f"198.51.100.{i}"}).status_code == 401
    assert client.post("/api/unlock", json={"code": CODE}, headers={"x-real-ip": "198.51.100.99"}).status_code == 429


def test_owner_runs_are_limited_in_the_shared_store(client):
    for _ in range(server.OWNER_RUNS_PER_IP_10MIN):
        assert client.post("/api/run", json={"alert": ALERT}, headers={"X-Demo-Code": CODE}).status_code == 200
    r = client.post("/api/run", json={"alert": ALERT}, headers={"X-Demo-Code": CODE})
    assert r.status_code == 429 and "per 10 minutes" in r.json()["detail"]


def test_api_docs_page_loads_its_files_from_this_site(client):
    """The page must work under the site's Content-Security-Policy: no inline script, no CDN."""
    html = client.get("/api/docs").text
    assert '<script src="/swagger/swagger-ui-bundle.js"></script>' in html and '<script src="/swagger/init.js"></script>' in html
    assert "<script>" not in html and "cdn." not in html
    public = server.ROOT / "public" / "swagger"
    assert all((public / f).exists() for f in ("swagger-ui-bundle.js", "swagger-ui.css", "init.js", "LICENSE"))
    assert client.get("/api/openapi.json").json()["info"]["title"] == "Alert2Playbook"
    assert client.get("/redoc").status_code == 404


# ---------------------------------------------------------------- visitor keys

def test_visitor_run_streams_and_is_logged_without_the_key(client):
    FakeLLM.closed = False
    r = client.post("/api/run", json={"alert": ALERT, "visitor": VISITOR, "alert_kind": "builder:win"}, headers={"X-LLM-Key": GROQ_KEY})
    assert r.status_code == 200 and "event: done" in r.text
    assert FakeLLM.closed  # the visitor's connection is closed after the run
    rec = get_store().recent_runs()[0]
    assert rec["source"] == "visitor" and rec["name"] == "Sara Khan" and rec["provider"] == "groq"
    assert rec["status"] == "ok" and rec["techniques"] == ["T1053.005"] and rec["tokens"] == 35
    assert rec["key_masked"] == P.mask(GROQ_KEY) and rec["alert_kind"] == "builder:win"
    assert GROQ_KEY not in everything_stored()


def test_key_must_travel_with_the_visitor_details(client):
    assert client.post("/api/run", json={"alert": ALERT}, headers={"X-LLM-Key": GROQ_KEY}).status_code == 400
    assert client.post("/api/run", json={"alert": ALERT, "visitor": VISITOR}).status_code == 400


@pytest.mark.parametrize("change,detail", [
    ({"provider": "evil"}, "isn't supported"),
    ({"model": "bad model name"}, "model"),
    ({"name": "<script>alert(1)</script>"}, "name"),
    ({"name": "x"}, "name"),
])
def test_bad_visitor_details_are_refused(client, change, detail):
    r = client.post("/api/run", json={"alert": ALERT, "visitor": VISITOR | change}, headers={"X-LLM-Key": GROQ_KEY})
    assert r.status_code == 400 and detail in r.json()["detail"]


def test_injected_key_is_refused(client):
    r = client.post("/api/keys/check", json={"name": "Sara Khan", "provider": "groq"}, headers={"X-LLM-Key": "gsk_" + "a" * 30 + " evil"})
    assert r.status_code == 400


def test_key_check_lists_models_and_logs_a_masked_key(client):
    r = client.post("/api/keys/check", json={"name": "Sara Khan", "provider": "groq"}, headers={"X-LLM-Key": GROQ_KEY})
    body = r.json()
    assert r.status_code == 200 and body["default"] == "openai/gpt-oss-120b" and body["masked"] == P.mask(GROQ_KEY)
    assert get_store().recent_runs()[0]["kind"] == "check"
    assert GROQ_KEY not in everything_stored()


def test_a_rejected_key_is_logged_as_a_failed_check(client, monkeypatch):
    def reject(provider, key):
        raise P.KeyError_("Groq didn't accept that key.")

    monkeypatch.setattr(server, "verify_key", reject)
    r = client.post("/api/keys/check", json={"name": "Sara Khan", "provider": "groq"}, headers={"X-LLM-Key": GROQ_KEY})
    assert r.status_code == 401
    rec = get_store().recent_runs()[0]
    assert rec["status"] == "error" and rec["key_masked"] == P.mask(GROQ_KEY)


def test_visitor_runs_are_limited_per_visitor(client):
    for _ in range(server.VISITOR_RUNS_PER_IP_HOUR):
        client.post("/api/run", json={"alert": ALERT, "visitor": VISITOR}, headers={"X-LLM-Key": GROQ_KEY})
    r = client.post("/api/run", json={"alert": ALERT, "visitor": VISITOR}, headers={"X-LLM-Key": GROQ_KEY})
    assert r.status_code == 429


def test_a2a_takes_a_visitor_key_and_card_offers_both(client, monkeypatch):
    monkeypatch.setattr(server, "playbook_markdown", lambda state, kb: "# plan")
    msg = {"jsonrpc": "2.0", "id": 1, "method": "SendMessage",
           "params": {"message": {"messageId": "m1", "role": "ROLE_USER", "parts": [{"text": "alert text here"}]}}}
    assert client.post("/a2a", json=msg).status_code == 401
    headers = {"X-LLM-Key": GROQ_KEY, "X-LLM-Provider": "groq", "X-LLM-Model": "openai/gpt-oss-120b", "X-Visitor-Name": "Agent%20Smith"}
    r = client.post("/a2a", json=msg, headers=headers)
    assert r.status_code == 200 and r.json()["result"]["task"]["status"]["state"] == "TASK_STATE_COMPLETED"
    rec = get_store().recent_runs()[0]
    assert rec["entry"] == "a2a" and rec["name"] == "Agent Smith"
    card = client.get("/.well-known/agent-card.json").json()
    assert set(card["securitySchemes"]) == {"demoCode", "ownKey"}
    assert card["securitySchemes"]["ownKey"]["apiKeySecurityScheme"]["name"] == "X-LLM-Key"
    assert len(card["securityRequirements"]) == 2


# ---------------------------------------------------------------- the owner's dashboard

def login(client, password=PASSWORD, origin=None):
    headers = {"Origin": origin} if origin else {}
    return client.post("/api/admin/login", json={"username": "abubakar", "password": password}, headers=headers)


def test_dashboard_needs_a_login(client):
    assert client.get("/api/admin/overview").status_code == 401
    assert login(client, "wrong").status_code == 401
    assert client.get("/api/admin/overview").status_code == 401


def test_login_sets_a_locked_down_cookie_and_opens_the_dashboard(client):
    client.post("/api/run", json={"alert": ALERT, "visitor": VISITOR}, headers={"X-LLM-Key": GROQ_KEY})
    r = login(client)
    assert r.status_code == 200
    cookie = r.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=strict" in cookie and "path=/api/admin" in cookie
    data = client.get("/api/admin/overview")
    assert data.status_code == 200 and data.headers["cache-control"] == "no-store"
    body = data.json()
    assert body["totals"]["visitor_runs"] == 1 and body["providers"][0]["share"] == 100.0
    assert body["runs"][0]["key_masked"] == P.mask(GROQ_KEY)
    assert GROQ_KEY not in data.text


def test_logout_ends_the_session(client):
    login(client)
    assert client.get("/api/admin/me").json() == {"user": "abubakar"}
    client.post("/api/admin/logout")
    assert client.get("/api/admin/me").status_code == 401


def test_login_attempts_are_limited(client):
    for _ in range(server.LOGIN_FAILS_PER_IP_15MIN):
        assert login(client, "wrong").status_code == 401
    assert login(client).status_code == 429


def test_only_wrong_passwords_count(client):
    for _ in range(server.LOGIN_FAILS_PER_IP_15MIN + 3):
        assert login(client).status_code == 200


def test_a_flood_of_wrong_passwords_cannot_lock_the_owner_out(client, monkeypatch):
    """Someone spamming wrong passwords from many addresses hits the limit for unknown
    browsers, but the owner's browser, which has signed in before, still gets in."""
    assert login(client).status_code == 200  # the owner's browser becomes a known browser
    monkeypatch.setattr(server, "LOGIN_FAILS_NEW_BROWSERS_PER_HOUR", 5)
    attacker = TestClient(server.app)
    for i in range(5):
        r = attacker.post("/api/admin/login", json={"username": "abubakar", "password": "guess"}, headers={"x-real-ip": f"198.51.100.{i}"})
        assert r.status_code == 401
    r = attacker.post("/api/admin/login", json={"username": "abubakar", "password": PASSWORD}, headers={"x-real-ip": "198.51.100.77"})
    assert r.status_code == 429  # even the right password, from a browser the site doesn't know
    assert login(client).status_code == 200  # the owner is not affected


def test_a_known_browser_has_its_own_limit(client):
    login(client)
    for _ in range(server.LOGIN_FAILS_PER_KNOWN_BROWSER_15MIN):
        assert login(client, "wrong").status_code == 401
    r = login(client)
    assert r.status_code == 429 and "this browser" in r.json()["detail"]


def test_known_browser_cookie_is_locked_down_and_cannot_be_faked(client, monkeypatch):
    r = login(client)
    device = next(c for c in r.headers.get_list("set-cookie") if c.startswith(admin.DEVICE_COOKIE + "=")).lower()
    assert "httponly" in device and "samesite=strict" in device and "path=/api/admin" in device and f"max-age={admin.DEVICE_TTL}" in device
    token = client.cookies.get(admin.DEVICE_COOKIE)
    assert admin.read_device(token)
    payload, sig = token.split(".")
    assert admin.read_device(f"{payload}.{sig[::-1]}") is None  # a changed signature
    monkeypatch.setenv("A2P_ADMIN_PASSWORD_HASH", admin.hash_password("a brand new password for the dashboard"))
    assert admin.read_device(token) is None  # a new password forgets every known browser


def test_session_and_known_browser_tokens_cannot_stand_in_for_each_other(client):
    login(client)
    session, device = client.cookies.get(admin.COOKIE), client.cookies.get(admin.DEVICE_COOKIE)
    assert admin.read_session(session) == "abubakar" and admin.read_device(device)
    assert admin.read_session(device) is None and admin.read_device(session) is None
    client.cookies.delete(admin.COOKIE)
    assert client.get("/api/admin/overview").status_code == 401  # a known browser alone opens nothing


def test_login_from_another_site_is_refused(client):
    assert login(client, origin="https://evil.example").status_code == 403


def test_a_forged_cookie_is_refused(client):
    client.cookies.set(admin.COOKIE, "YWJ1YmFrYXJ8OTk5OTk5OTk5OXxhYmM.forged", path="/api/admin")
    assert client.get("/api/admin/overview").status_code == 401


def test_deleting_entries_needs_a_login_and_removes_only_those(client):
    for _ in range(3):
        client.post("/api/run", json={"alert": ALERT, "visitor": VISITOR}, headers={"X-LLM-Key": GROQ_KEY})
    ids = [r["id"] for r in get_store().recent_runs()]
    assert len(ids) == 3
    assert client.post("/api/admin/runs/delete", json={"ids": ids[:1]}).status_code == 401
    login(client)
    assert client.post("/api/admin/runs/delete", json={"ids": ids[:1]}, headers={"Origin": "https://evil.example"}).status_code == 403
    assert client.post("/api/admin/runs/delete", json={"ids": ["not-an-id"]}).status_code == 400
    r = client.post("/api/admin/runs/delete", json={"ids": ids[:2]})
    assert r.status_code == 200 and r.json() == {"deleted": 2}
    left = client.get("/api/admin/overview").json()
    assert [x["id"] for x in left["runs"]] == ids[2:]
    assert left["totals"]["visitor_runs"] == 1
    assert client.post("/api/admin/runs/delete", json={"ids": ids[:1]}).json() == {"deleted": 0}  # already gone
