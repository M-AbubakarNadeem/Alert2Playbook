"""HTTP API (FastAPI). Vercel detects `app` in this root file and deploys it as one
Python function; the page in public/ is served from Vercel's CDN.

    GET  /api/health                  configuration and knowledge-graph version
    GET  /api/samples                 sample alerts, with pre-computed runs
    GET  /api/providers               providers a visitor can bring a key for
    POST /api/keys/check {name, provider}            check a visitor key, list its models   [X-LLM-Key]
    POST /api/unlock     {code}                      check the owner's demo code
    POST /api/run        {alert, visitor?}           run the graph, streamed as server-sent events   [access]
    POST /api/feedback   {state, feedback, visitor?} analyst-requested revision, streamed            [access]
    POST /api/markdown   {state}                     playbook as Markdown
    GET  /.well-known/agent-card.json                A2A Agent Card
    POST /a2a                                        A2A JSON-RPC endpoint (SendMessage)             [access]

    POST /api/admin/login  {username, password}      owner's dashboard login (sets an HttpOnly cookie)
    POST /api/admin/logout
    GET  /api/admin/me
    GET  /api/admin/overview                          run log and usage numbers                       [admin cookie]

[access] is one of:
  - the owner's demo code in the X-Demo-Code header (runs on the owner's Groq key), or
  - a visitor's own key in the X-LLM-Key header, with `visitor: {name, provider, model}` in
    the body (for /a2a: X-LLM-Provider, X-LLM-Model and X-Visitor-Name headers).
A visitor key is checked with its provider before a run, used in memory for that run,
and never stored. The run log keeps a masked form and a fingerprint only.
"""

from __future__ import annotations

import hmac
import json
import re
import secrets
import sys
import time
from collections import Counter, defaultdict, deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import unquote

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import JSONResponse, PlainTextResponse, StreamingResponse
from pydantic import BaseModel, Field

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from alert2playbook import __version__  # noqa: E402
from alert2playbook import admin  # noqa: E402
from alert2playbook.a2a import DEMO_CODE_HEADER, agent_card, handle_jsonrpc  # noqa: E402
from alert2playbook.graph import allowed_references, feedback_state, new_state, stream_run  # noqa: E402
from alert2playbook.kb import get_kb  # noqa: E402
from alert2playbook.llm import ProviderError, scrub_error  # noqa: E402
from alert2playbook.providers import (  # noqa: E402
    KeyError_, check_format, check_model, fingerprint, get_provider, list_models, mask, public_list, verify_key,
)
from alert2playbook.render import playbook_markdown  # noqa: E402
from alert2playbook.service import get_base, get_service, visitor_graph  # noqa: E402
from alert2playbook.store import get_store  # noqa: E402

app = FastAPI(title="Alert2Playbook", version=__version__, docs_url="/api/docs", openapi_url="/api/openapi.json")

SAMPLES = json.loads((ROOT / "data" / "samples.json").read_text(encoding="utf-8"))
SAMPLE_RUNS = ROOT / "data" / "sample_runs"

KEY_HEADER = "X-LLM-Key"


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    h = response.headers
    h.setdefault("X-Content-Type-Options", "nosniff")
    h.setdefault("X-Frame-Options", "DENY")
    h.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    h.setdefault("Cross-Origin-Opener-Policy", "same-origin")
    if request.url.path.startswith("/api/admin") or request.url.path.startswith("/api/keys"):
        h["Cache-Control"] = "no-store"
    return response


# --------------------------------------------------------------------- limits
# Owner runs: a best-effort per-IP limit per serverless instance, as before.
WINDOW_S, MAX_RUNS, MAX_UNLOCK_TRIES = 600, 8, 10
_runs: dict[str, deque] = defaultdict(deque)
_unlock_tries: dict[str, deque] = defaultdict(deque)

# Visitor runs and logins use the shared store, so the limits hold across instances.
# They also protect the owner's free Pinecone and LangSmith quotas.
VISITOR_RUNS_PER_IP_HOUR = 10
VISITOR_RUNS_PER_KEY_HOUR = 20
VISITOR_RUNS_PER_DAY = 300
KEY_CHECKS_PER_IP_HOUR = 20
LOGINS_PER_IP_15MIN = 5
LOGINS_PER_HOUR = 30


def client_ip(request: Request) -> str:
    # Vercel sets x-real-ip and overwrites x-forwarded-for, so neither can be spoofed there.
    raw = request.headers.get("x-real-ip") or request.headers.get("x-forwarded-for") or (request.client.host if request.client else "") or "?"
    return raw.split(",")[0].strip()


def visitor_id(request: Request) -> str:
    """A stable, non-reversible id for counting unique visitors without keeping their IP."""
    return fingerprint("ip:" + client_ip(request))


def _within_limit(store: dict[str, deque], request: Request, limit: int) -> bool:
    now, q = time.time(), store[client_ip(request)]
    while q and now - q[0] > WINDOW_S:
        q.popleft()
    if len(q) >= limit:
        return False
    q.append(now)
    return True


def check_rate(request: Request) -> None:
    if not _within_limit(_runs, request, MAX_RUNS):
        raise HTTPException(429, "Too many live runs from this address: 8 per 10 minutes. Samples still open instantly.")


def code_ok(given: str | None) -> bool:
    code = get_base()[0].demo_code
    return not code or hmac.compare_digest((given or "").encode(), code.encode())


def require_demo_code(request: Request) -> None:
    if not code_ok(request.headers.get(DEMO_CODE_HEADER)):
        raise HTTPException(401, "Live runs need your own API key (or the owner's demo code). You can also watch a saved run.")


def same_origin(request: Request) -> None:
    """State-changing admin requests must come from this site's own pages."""
    origin = request.headers.get("origin")
    if origin and origin.rstrip("/") != base_url(request):
        raise HTTPException(403, "Cross-site request refused.")


# --------------------------------------------------------------------- helpers

def sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, default=str)}\n\n"


def base_url(request: Request) -> str:
    proto = request.headers.get("x-forwarded-proto", request.url.scheme)
    host = request.headers.get("x-forwarded-host", request.url.netloc)
    return f"{proto}://{host}"


_NAME_BAD = re.compile(r"[\x00-\x1f\x7f<>]")
_KIND = re.compile(r"^[a-z0-9:_\-]{1,40}$")


def clean_name(name: str) -> str:
    name = " ".join((name or "").split())
    if not 2 <= len(name) <= 60 or _NAME_BAD.search(name):
        raise HTTPException(400, "Enter your name, 2 to 60 characters, without < or >.")
    return name


def browser(request: Request) -> str:
    """A rough browser and OS label for the dashboard. Not the full user agent."""
    ua = request.headers.get("user-agent", "")
    b = next((n for k, n in [("Edg/", "Edge"), ("OPR/", "Opera"), ("Firefox/", "Firefox"), ("Chrome/", "Chrome"), ("Safari/", "Safari"), ("curl/", "curl"), ("python", "Python")] if k in ua), "Other")
    o = next((n for k, n in [("Windows", "Windows"), ("Android", "Android"), ("iPhone", "iOS"), ("iPad", "iOS"), ("Mac OS", "macOS"), ("Linux", "Linux")] if k in ua), "")
    return f"{b} · {o}" if o else b


def place(request: Request) -> tuple[str, str]:
    country = (request.headers.get("x-vercel-ip-country") or "")[:2]
    city = unquote(request.headers.get("x-vercel-ip-city") or "")[:60]
    return country, re.sub(r"[\x00-\x1f\x7f<>]", "", city)


def key_problem(exc: KeyError_, status: int = 400) -> HTTPException:
    return HTTPException(status, str(exc))


# --------------------------------------------------------------------- runners

@dataclass
class Runner:
    graph: object
    source: str                       # owner | visitor
    record: dict = field(default_factory=dict)
    llm: object | None = None
    secrets: tuple[str, ...] = ()


class VisitorIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    provider: str = Field(min_length=1, max_length=40)
    model: str = Field(min_length=1, max_length=120)


def base_record(request: Request, kind: str, entry: str, source: str) -> dict:
    country, city = place(request)
    return {
        "id": secrets.token_hex(6),
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "kind": kind, "entry": entry, "source": source,
        "visitor": visitor_id(request), "country": country, "city": city, "browser": browser(request),
    }


def resolve_runner(request: Request, visitor: VisitorIn | None, kind: str, entry: str) -> Runner:
    """Who pays for this run: the visitor's own key, or the owner's (with the demo code)."""
    key = request.headers.get(KEY_HEADER)
    if key or visitor:
        if not (key and visitor):
            raise HTTPException(400, "Send your name, provider and model with the key in the X-LLM-Key header.")
        return visitor_runner(request, visitor, key, kind, entry)
    require_demo_code(request)
    check_rate(request)
    service = get_service()
    record = base_record(request, kind, entry, "owner") | {
        "name": "Owner (demo code)", "provider": "groq", "provider_label": "Groq", "model": service.settings.writer_model,
        "key_masked": "", "key_fp": "",
    }
    return Runner(service.graph, "owner", record)


def visitor_runner(request: Request, visitor: VisitorIn, key: str, kind: str, entry: str) -> Runner:
    try:
        provider = get_provider(visitor.provider)
        key = check_format(provider, key)
        model = check_model(visitor.model)
    except KeyError_ as exc:
        raise key_problem(exc) from None
    name = clean_name(visitor.name)
    fp, store = fingerprint(key), get_store()
    if not store.hit("vrun-day", "all", VISITOR_RUNS_PER_DAY, 86400):
        raise HTTPException(429, "The site has reached today's limit for live runs. Try again tomorrow, or watch a saved run.")
    if not store.hit("vrun-ip", visitor_id(request), VISITOR_RUNS_PER_IP_HOUR, 3600):
        raise HTTPException(429, f"That's {VISITOR_RUNS_PER_IP_HOUR} live runs from you in the last hour. Wait a bit, or watch a saved run.")
    if not store.hit("vrun-key", fp, VISITOR_RUNS_PER_KEY_HOUR, 3600):
        raise HTTPException(429, "This key has run a lot in the last hour. Wait a bit and try again.")
    if not store.recall(f"ok:{provider.id}:{fp}"):
        try:
            verify_key(provider, key)
        except KeyError_ as exc:
            raise key_problem(exc, 401) from None
        store.remember(f"ok:{provider.id}:{fp}", 900)
    graph, llm = visitor_graph(provider, key, model)
    record = base_record(request, kind, entry, "visitor") | {
        "name": name, "provider": provider.id, "provider_label": provider.label, "model": model,
        "key_masked": mask(key), "key_fp": fp,
    }
    return Runner(graph, "visitor", record, llm, (key,))


def friendly_error(exc: Exception, runner: Runner) -> str:
    if isinstance(exc, ProviderError):
        return str(exc)
    text = scrub_error(str(exc), runner.secrets)
    if "rate_limit" in text or "429" in text:
        return "The AI provider is busy right now. Wait a minute and try again, or open a sample alert."
    return f"{type(exc).__name__}: {text[:300]}"


def log_outcome(runner: Runner, extra: dict, final: dict | None, error: str | None, started: float) -> None:
    try:
        final = final or {}
        steps = final.get("steps") or []
        rec = dict(runner.record) | extra | {
            "status": "ok" if final.get("playbook") and not error else "error",
            "error": (error or "")[:300],
            "duration_ms": round((time.perf_counter() - started) * 1000),
            "steps": len({s.get("node") for s in steps}),
            "tokens": sum((s.get("input_tokens") or 0) + (s.get("output_tokens") or 0) for s in steps),
            "techniques": [m.get("technique_id") for m in final.get("mapping") or []][:3],
            "severity": (final.get("triage") or {}).get("severity", ""),
            "guard_flagged": bool((final.get("guard") or {}).get("flagged")),
        }
        get_store().log_run(rec)
    except Exception:  # the log must never break a run
        pass


def stream_graph(runner: Runner, state: dict, extra: dict) -> StreamingResponse:
    def events():
        started, final, error = time.perf_counter(), None, None
        try:
            for node, update, merged in stream_run(runner.graph, state, source=runner.source):
                final = merged
                yield sse("node", {"node": node, "update": update})
            yield sse("done", {"state": final})
        except Exception as exc:
            error = friendly_error(exc, runner)
            yield sse("error", {"message": error})
        finally:
            if runner.llm is not None:
                runner.llm.close()
            log_outcome(runner, extra, final, error, started)

    headers = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
    return StreamingResponse(events(), media_type="text/event-stream", headers=headers)


# --------------------------------------------------------------------- routes

class RunRequest(BaseModel):
    alert: str = Field(min_length=10)
    visitor: VisitorIn | None = None
    alert_kind: str | None = Field(default=None, max_length=40)


class FeedbackRequest(BaseModel):
    state: dict
    feedback: str = Field(min_length=3, max_length=1000)
    visitor: VisitorIn | None = None


class MarkdownRequest(BaseModel):
    state: dict


class UnlockRequest(BaseModel):
    code: str = Field(min_length=1, max_length=100)


class KeyCheckRequest(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    provider: str = Field(min_length=1, max_length=40)


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=100)
    password: str = Field(min_length=1, max_length=200)


@app.get("/api/health")
def health():
    kb = get_kb()
    try:
        service = get_service()
        models, retrieval, ready = service.models, service.retriever.mode, True
        locked = bool(service.settings.demo_code)
    except RuntimeError as exc:
        models, retrieval, ready, locked = {}, str(exc), False, True
    return {
        "ready": ready,
        "version": __version__,
        "attack_versions": kb.domain_versions,
        "techniques": len(kb.techniques),
        "models": models,
        "retrieval": retrieval,
        "live_runs": "need demo code" if locked else "open",
        "own_keys": True,
    }


@app.get("/api/providers")
def providers():
    return {"providers": public_list(), "key_header": KEY_HEADER}


@app.post("/api/keys/check")
def keys_check(body: KeyCheckRequest, request: Request):
    """Checks a visitor's key with its provider and lists the models it can use.
    The key is not kept; a successful check is remembered by fingerprint for 15 minutes."""
    store = get_store()
    if not store.hit("kcheck-ip", visitor_id(request), KEY_CHECKS_PER_IP_HOUR, 3600):
        raise HTTPException(429, "Too many key checks from you in the last hour. Wait a bit and try again.")
    name = clean_name(body.name)
    try:
        provider = get_provider(body.provider)
        key = check_format(provider, request.headers.get(KEY_HEADER, ""))
    except KeyError_ as exc:
        raise key_problem(exc) from None
    fp = fingerprint(key)
    record = base_record(request, "check", "web", "visitor") | {
        "name": name, "provider": provider.id, "provider_label": provider.label, "model": "",
        "key_masked": mask(key), "key_fp": fp,
    }
    try:
        verify_key(provider, key)
    except KeyError_ as exc:
        get_store().log_run(record | {"status": "error", "error": str(exc)})
        raise key_problem(exc, 401) from None
    store.remember(f"ok:{provider.id}:{fp}", 900)
    models = list_models(provider, key)
    get_store().log_run(record | {"status": "ok", "error": ""})
    return {"ok": True, "provider": provider.id, "label": provider.label, "models": models,
            "default": models[0] if models else "", "masked": mask(key), "guard": provider.has_guard}


@app.post("/api/unlock")
def unlock(body: UnlockRequest, request: Request):
    """Lets the page check the owner's demo code before using it. Attempts are rate limited."""
    if not _within_limit(_unlock_tries, request, MAX_UNLOCK_TRIES):
        raise HTTPException(429, "Too many attempts. Wait 10 minutes.")
    if not code_ok(body.code.strip()):
        raise HTTPException(401, "That demo code is not right.")
    return {"ok": True}


@app.get("/api/samples")
def samples():
    out = []
    for s in SAMPLES:
        run_file = SAMPLE_RUNS / f"{s['id']}.json"
        out.append({**s, "run": json.loads(run_file.read_text(encoding="utf-8")) if run_file.exists() else None})
    return out


@app.post("/api/run")
def run(body: RunRequest, request: Request):
    limit = get_base()[0].max_alert_chars
    if len(body.alert) > limit:
        raise HTTPException(413, f"Alerts are limited to {limit} characters.")
    runner = resolve_runner(request, body.visitor, "run", "web")
    kind = body.alert_kind if body.alert_kind and _KIND.fullmatch(body.alert_kind) else "pasted"
    return stream_graph(runner, new_state(body.alert.strip()), {"alert_kind": kind, "alert_chars": len(body.alert)})


@app.post("/api/feedback")
def feedback(body: FeedbackRequest, request: Request):
    kb = get_kb()
    prev = body.state
    if not all(k in prev for k in ("alert", "triage", "mapping", "playbook")):
        raise HTTPException(400, "state must come from a previous run")
    # The client returns the previous state, so rebuild the grounding context on the
    # server from validated technique IDs instead of trusting what came back.
    mapping = [m for m in prev["mapping"] if isinstance(m, dict) and kb.resolve(str(m.get("technique_id", "")))][:3]
    if not mapping:
        raise HTTPException(400, "state has no valid ATT&CK mapping")
    runner = resolve_runner(request, body.visitor, "feedback", "web")
    context = {"techniques": [kb.neighborhood(m["technique_id"]) for m in mapping]}
    context["allowed_refs"] = sorted(allowed_references(context))
    state = feedback_state({**prev, "mapping": mapping, "context": context}, body.feedback.strip())
    state["alert"] = str(state["alert"])[: get_base()[0].max_alert_chars]
    return stream_graph(runner, state, {"alert_kind": "feedback", "alert_chars": len(state["alert"])})


@app.post("/api/markdown", response_class=PlainTextResponse)
def markdown(body: MarkdownRequest):
    try:
        return playbook_markdown(body.state, get_kb())
    except (KeyError, TypeError, AttributeError) as exc:
        raise HTTPException(400, f"state is missing {exc}") from exc


@app.get("/.well-known/agent-card.json")
def card(request: Request):
    return agent_card(base_url(request), __version__, locked=bool(get_base()[0].demo_code))


@app.post("/a2a")
async def a2a(request: Request):
    try:
        payload = await request.json()
    except ValueError:
        return JSONResponse({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}})
    h = request.headers
    visitor = None
    if h.get(KEY_HEADER):
        try:
            visitor = VisitorIn(name=unquote(h.get("X-Visitor-Name", "")) or "A2A client", provider=h.get("X-LLM-Provider", ""), model=h.get("X-LLM-Model", ""))
        except ValueError:
            raise HTTPException(400, "Send X-LLM-Provider, X-LLM-Model and X-Visitor-Name with X-LLM-Key.") from None
    runner = resolve_runner(request, visitor, "run", "a2a")
    max_chars = get_base()[0].max_alert_chars
    outcome: dict = {}

    def run_alert(text: str) -> dict:
        started, final, error = time.perf_counter(), None, None
        try:
            for _, _, merged in stream_run(runner.graph, new_state(text[:max_chars]), source="a2a"):
                final = merged
            return final
        except Exception as exc:
            error = friendly_error(exc, runner)
            raise ValueError(error) from None
        finally:
            outcome.update(final=final, error=error, started=started, chars=len(text))

    from starlette.concurrency import run_in_threadpool

    try:
        result = await run_in_threadpool(handle_jsonrpc, payload, run_alert, lambda s: playbook_markdown(s, get_kb()))
    finally:
        if runner.llm is not None:
            runner.llm.close()
        if outcome:
            log_outcome(runner, {"alert_kind": "a2a", "alert_chars": outcome["chars"]}, outcome["final"], outcome["error"], outcome["started"])
    return JSONResponse(result)


# --------------------------------------------------------------------- owner dashboard

def require_admin(request: Request) -> str:
    who = admin.read_session(request.cookies.get(admin.COOKIE))
    if not who:
        raise HTTPException(401, "Sign in first.")
    return who


@app.post("/api/admin/login")
def admin_login(body: LoginRequest, request: Request, response: Response):
    same_origin(request)
    store = get_store()
    if not admin.configured():
        raise HTTPException(503, "The dashboard login isn't set up on this server.")
    if not store.hit("login-ip", visitor_id(request), LOGINS_PER_IP_15MIN, 900) or not store.hit("login-all", "all", LOGINS_PER_HOUR, 3600):
        raise HTTPException(429, "Too many sign-in attempts. Wait 15 minutes.")
    if not admin.check_login(body.username.strip(), body.password):
        time.sleep(0.4)
        raise HTTPException(401, "Wrong username or password.")
    secure = base_url(request).startswith("https://")
    response.set_cookie(admin.COOKIE, admin.make_session(), max_age=admin.SESSION_TTL, httponly=True, secure=secure, samesite="strict", path="/api/admin")
    return {"ok": True}


@app.post("/api/admin/logout")
def admin_logout(request: Request, response: Response):
    same_origin(request)
    response.delete_cookie(admin.COOKIE, path="/api/admin")
    return {"ok": True}


@app.get("/api/admin/me")
def admin_me(request: Request):
    return {"user": require_admin(request)}


@app.get("/api/admin/overview")
def admin_overview(request: Request):
    require_admin(request)
    store = get_store()
    runs = store.recent_runs()
    real = [r for r in runs if r.get("kind") in ("run", "feedback")]
    visitor_runs = [r for r in real if r.get("source") == "visitor"]
    checks = [r for r in runs if r.get("kind") == "check"]
    today = datetime.now(timezone.utc).date()
    days = [(today - timedelta(days=i)).isoformat() for i in range(13, -1, -1)]
    per_day = Counter(r.get("ts", "")[:10] for r in visitor_runs)
    providers = Counter(r.get("provider_label") or r.get("provider") for r in visitor_runs)
    keys_by_provider: dict[str, set] = defaultdict(set)
    for r in runs:
        if r.get("source") == "visitor" and r.get("key_fp"):
            keys_by_provider[r.get("provider_label") or r.get("provider")].add(r["key_fp"])
    ok = sum(1 for r in visitor_runs if r.get("status") == "ok")
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "store": store.backend,
        "totals": {
            "visitor_runs": len(visitor_runs),
            "owner_runs": sum(1 for r in real if r.get("source") == "owner"),
            "key_checks": len(checks),
            "key_checks_failed": sum(1 for r in checks if r.get("status") != "ok"),
            "unique_visitors": len({r.get("visitor") for r in runs if r.get("source") == "visitor"}),
            "unique_keys": len({r.get("key_fp") for r in runs if r.get("key_fp")}),
            "success_rate": round(100 * ok / len(visitor_runs), 1) if visitor_runs else None,
        },
        "providers": [{"provider": p, "runs": n, "share": round(100 * n / len(visitor_runs), 1), "keys": len(keys_by_provider.get(p, ()))} for p, n in providers.most_common()],
        "per_day": [{"day": d, "runs": per_day.get(d, 0)} for d in days],
        "all_time": store.counts(),
        "runs": runs,
    }
