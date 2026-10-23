"""Minimal A2A (Agent2Agent protocol v1.0) server binding over JSON-RPC.

Other agents discover Alert2Playbook through its Agent Card at
/.well-known/agent-card.json and call `SendMessage` with an alert as a text part.
Each call runs the full graph synchronously and returns a completed Task whose
artifacts hold the playbook as Markdown and as structured JSON. The function is
stateless, so task history is not kept between calls.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

PROTOCOL_VERSION = "1.0"


DEMO_CODE_HEADER = "X-Demo-Code"
OWN_KEY_HEADER = "X-LLM-Key"


def agent_card(base_url: str, version: str, locked: bool = False) -> dict:
    card = {
        "name": "Alert2Playbook",
        "description": (
            "Turns a raw security alert or log excerpt into a MITRE ATT&CK mapping and a NIST SP 800-61 "
            "incident response playbook grounded in ATT&CK mitigations, detection strategies and MITRE D3FEND "
            "countermeasures."
        ),
        "supportedInterfaces": [
            {"url": f"{base_url}/a2a", "protocolBinding": "JSONRPC", "protocolVersion": PROTOCOL_VERSION}
        ],
        "provider": {"organization": "Muhammad Abubakar Nadeem", "url": "https://github.com/M-AbubakarNadeem"},
        "version": version,
        "documentationUrl": "https://github.com/M-AbubakarNadeem/Alert2Playbook",
        "capabilities": {"streaming": False, "pushNotifications": False},
        "defaultInputModes": ["text/plain"],
        "defaultOutputModes": ["text/markdown", "application/json"],
        "skills": [
            {
                "id": "alert-to-playbook",
                "name": "Alert to response playbook",
                "description": "Triage an alert, map it to ATT&CK techniques and write a grounded, reviewed response playbook.",
                "tags": ["security", "incident-response", "mitre-attack", "d3fend", "soc"],
                "examples": [
                    "Sysmon EID 1 on WS-FIN-07: vssadmin.exe delete shadows /all /quiet, parent: cmd.exe, user CORP\\svc_backup"
                ],
            }
        ],
    }
    if locked:
        # A run is paid for either by the owner (demo code) or by the caller's own model
        # key. The two requirements are alternatives: either one is enough.
        card["securitySchemes"] = {
            "demoCode": {
                "apiKeySecurityScheme": {
                    "description": "Demo code issued by the owner. Runs on the owner's Groq key.",
                    "location": "header",
                    "name": DEMO_CODE_HEADER,
                }
            },
            "ownKey": {
                "apiKeySecurityScheme": {
                    "description": (
                        "Your own model API key. Also send X-LLM-Provider (a provider id from GET /api/providers), "
                        "X-LLM-Model and X-Visitor-Name. The key is checked with its provider, used for this one "
                        "call and never stored."
                    ),
                    "location": "header",
                    "name": OWN_KEY_HEADER,
                }
            },
        }
        card["securityRequirements"] = [{"schemes": {"demoCode": {"list": []}}}, {"schemes": {"ownKey": {"list": []}}}]
    return card


def _error(req_id, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}}


def handle_jsonrpc(payload: dict, run_alert, render_markdown) -> dict:
    """`run_alert(text) -> final state`; `render_markdown(state) -> str`."""
    req_id = payload.get("id")
    if payload.get("jsonrpc") != "2.0" or "method" not in payload:
        return _error(req_id, -32600, "Invalid Request")
    if payload["method"] != "SendMessage":
        return _error(req_id, -32601, f"Method not found: {payload['method']}")

    message = (payload.get("params") or {}).get("message") or {}
    text = "\n".join(p["text"] for p in message.get("parts", []) if isinstance(p, dict) and p.get("text"))
    if not text.strip():
        return _error(req_id, -32602, "Invalid params: message must contain a text part with the alert")

    task_id, context_id = str(uuid.uuid4()), message.get("contextId") or str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    try:
        state = run_alert(text)
    except Exception as exc:
        status = {"state": "TASK_STATE_FAILED", "timestamp": now,
                  "message": {"messageId": str(uuid.uuid4()), "role": "ROLE_AGENT", "parts": [{"text": str(exc)}]}}
        return {"jsonrpc": "2.0", "id": req_id, "result": {"task": {"id": task_id, "contextId": context_id, "status": status}}}

    result = {
        "mapping": state["mapping"],
        "triage": state["triage"],
        "playbook": state["playbook"],
        "review": state["review"],
        "status": state["status"],
    }
    task = {
        "id": task_id,
        "contextId": context_id,
        "status": {"state": "TASK_STATE_COMPLETED", "timestamp": now},
        "artifacts": [
            {"artifactId": str(uuid.uuid4()), "name": "playbook.md",
             "parts": [{"text": render_markdown(state), "mediaType": "text/markdown"}]},
            {"artifactId": str(uuid.uuid4()), "name": "result.json",
             "parts": [{"data": result, "mediaType": "application/json"}]},
        ],
    }
    return {"jsonrpc": "2.0", "id": req_id, "result": {"task": task}}
