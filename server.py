"""HTTP API (FastAPI). Vercel detects `app` in this root file and deploys it as one
Python function; the page in public/ is served from Vercel's CDN.

    GET  /api/health                  configuration and knowledge-graph version
    GET  /api/samples                 sample alerts, with pre-computed runs
    POST /api/unlock     {code}       check a demo code
    POST /api/run        {alert}      run the graph, streamed as server-sent events   [demo code]
    POST /api/feedback   {state, feedback}   analyst-requested revision, streamed     [demo code]
    POST /api/markdown   {state}      playbook as Markdown
    GET  /.well-known/agent-card.json A2A Agent Card
    POST /a2a                         A2A JSON-RPC endpoint (SendMessage)             [demo code]

[demo code]: when A2P_DEMO_CODE is set, send it in the X-Demo-Code header.
"""

from __future__ import annotations

import hmac
import json
import sys
import time
from collections import defaultdict, deque
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, PlainTextResponse, StreamingResponse
from pydantic import BaseModel, Field

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from alert2playbook import __version__  # noqa: E402
from alert2playbook.a2a import DEMO_CODE_HEADER, agent_card, handle_jsonrpc  # noqa: E402
from alert2playbook.graph import allowed_references, feedback_state, new_state, stream_run  # noqa: E402
from alert2playbook.kb import get_kb  # noqa: E402
from alert2playbook.llm import scrub_error  # noqa: E402
from alert2playbook.render import playbook_markdown  # noqa: E402
from alert2playbook.service import get_service  # noqa: E402

app = FastAPI(title="Alert2Playbook", version=__version__, docs_url="/api/docs", openapi_url="/api/openapi.json")

SAMPLES = json.loads((ROOT / "data" / "samples.json").read_text(encoding="utf-8"))
SAMPLE_RUNS = ROOT / "data" / "sample_runs"


# --------------------------------------------------------------------- access
# Live runs spend the owner's Groq credit. When A2P_DEMO_CODE is set (it is on the
# public site), only callers who send that code in the X-Demo-Code header can run
# the agents; everyone else can still open the samples, which replay saved runs.
# On top of that, a best-effort per-IP limit (per serverless instance).
WINDOW_S, MAX_RUNS, MAX_UNLOCK_TRIES = 600, 8, 10
_runs: dict[str, deque] = defaultdict(deque)
_unlock_tries: dict[str, deque] = defaultdict(deque)


def client_ip(request: Request) -> str:
    return (request.headers.get("x-forwarded-for") or request.client.host or "?").split(",")[0].strip()


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
    code = get_service().settings.demo_code
    return not code or hmac.compare_digest((given or "").encode(), code.encode())


def require_demo_code(request: Request) -> None:
    if not code_ok(request.headers.get(DEMO_CODE_HEADER)):
        raise HTTPException(401, "Live runs are locked. Enter the demo code, or open a sample to watch a saved run.")


# --------------------------------------------------------------------- helpers

def sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, default=str)}\n\n"


def friendly_error(exc: Exception) -> str:
    text = str(exc)
    if "rate_limit" in text or "429" in text:
        return "The AI provider is busy right now. Wait a minute and try again, or open a sample alert."
    return f"{type(exc).__name__}: {scrub_error(text)[:300]}"


def stream_graph(state: dict) -> StreamingResponse:
    service = get_service()

    def events():
        final = None
        try:
            for node, update, merged in stream_run(service.graph, state):
                final = merged
                yield sse("node", {"node": node, "update": update})
            yield sse("done", {"state": final})
        except Exception as exc:
            yield sse("error", {"message": friendly_error(exc)})

    headers = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
    return StreamingResponse(events(), media_type="text/event-stream", headers=headers)


def base_url(request: Request) -> str:
    proto = request.headers.get("x-forwarded-proto", request.url.scheme)
    host = request.headers.get("x-forwarded-host", request.url.netloc)
    return f"{proto}://{host}"


# --------------------------------------------------------------------- routes

class RunRequest(BaseModel):
    alert: str = Field(min_length=10)


class FeedbackRequest(BaseModel):
    state: dict
    feedback: str = Field(min_length=3, max_length=1000)


class MarkdownRequest(BaseModel):
    state: dict


class UnlockRequest(BaseModel):
    code: str = Field(min_length=1, max_length=100)


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
    }


@app.post("/api/unlock")
def unlock(body: UnlockRequest, request: Request):
    """Lets the page check a demo code before using it. Attempts are rate limited."""
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
    limit = get_service().settings.max_alert_chars
    if len(body.alert) > limit:
        raise HTTPException(413, f"Alerts are limited to {limit} characters.")
    require_demo_code(request)
    check_rate(request)
    return stream_graph(new_state(body.alert.strip()))


@app.post("/api/feedback")
def feedback(body: FeedbackRequest, request: Request):
    require_demo_code(request)
    check_rate(request)
    kb = get_kb()
    prev = body.state
    if not all(k in prev for k in ("alert", "triage", "mapping", "playbook")):
        raise HTTPException(400, "state must come from a previous run")
    # The client returns the previous state, so rebuild the grounding context on the
    # server from validated technique IDs instead of trusting what came back.
    mapping = [m for m in prev["mapping"] if kb.resolve(str(m.get("technique_id", "")))][:3]
    if not mapping:
        raise HTTPException(400, "state has no valid ATT&CK mapping")
    context = {"techniques": [kb.neighborhood(m["technique_id"]) for m in mapping]}
    context["allowed_refs"] = sorted(allowed_references(context))
    state = feedback_state({**prev, "mapping": mapping, "context": context}, body.feedback.strip())
    state["alert"] = state["alert"][: get_service().settings.max_alert_chars]
    return stream_graph(state)


@app.post("/api/markdown", response_class=PlainTextResponse)
def markdown(body: MarkdownRequest):
    try:
        return playbook_markdown(body.state, get_kb())
    except KeyError as exc:
        raise HTTPException(400, f"state is missing {exc}") from exc


@app.get("/.well-known/agent-card.json")
def card(request: Request):
    return agent_card(base_url(request), __version__, locked=bool(get_service().settings.demo_code))


@app.post("/a2a")
async def a2a(request: Request):
    try:
        payload = await request.json()
    except ValueError:
        return JSONResponse({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}})
    require_demo_code(request)
    check_rate(request)
    service = get_service()

    def run_alert(text: str) -> dict:
        final = None
        for _, _, merged in stream_run(service.graph, new_state(text[: service.settings.max_alert_chars]), source="a2a"):
            final = merged
        return final

    from starlette.concurrency import run_in_threadpool

    result = await run_in_threadpool(handle_jsonrpc, payload, run_alert, lambda s: playbook_markdown(s, service.kb))
    return JSONResponse(result)
