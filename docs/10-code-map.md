# 10. Map of the code

```text
Alert2Playbook/
├── alert2playbook/          the Python package: all the real logic
│   ├── __init__.py          version number
│   ├── config.py            reads settings and keys from the environment
│   ├── kb.py                the knowledge graph + keyword search (BM25)
│   ├── retrieval.py         hybrid search: Pinecone + BM25, merged with RRF
│   ├── schemas.py           the forms each agent must fill in (Pydantic models)
│   ├── prompts.py           the instructions for each agent, and what context each one sees
│   ├── llm.py               calls Groq, checks the form, retries, falls back to a bigger model
│   ├── graph.py             the LangGraph workflow: nodes, edges, critic loop, code checks
│   ├── service.py           connects the graph to the real Groq, Pinecone and knowledge graph
│   ├── render.py            turns a finished run into Markdown with linked IDs
│   ├── a2a.py               the A2A Agent Card and the SendMessage endpoint
│   └── mcp_server.py        the MCP server with three tools
├── server.py                the FastAPI web API (Vercel runs it as one function)
├── public/                  the website (served by Vercel's CDN)
│   ├── index.html           page skeleton: header, 4 tabs, footer, side drawer
│   ├── app.css              the look: colours, spacing, cards (light and dark)
│   ├── content.js           all explanations: steps, parts, messages, glossary, alert-builder templates
│   ├── app.js               the behaviour: tabs, alert picker and builder, unlock, live runs, replays, plan view
│   └── results.json         measured numbers for the "How good is it" tab (made by scripts/export_results.py)
├── data/
│   ├── kb.json.gz           the built knowledge graph (ATT&CK + D3FEND), 0.4 MB
│   ├── samples.json         the 7 sample alerts on the page
│   ├── sample_runs/         saved results for the samples, so they load instantly
│   └── raw/                 downloaded source files (not in git, rebuilt by scripts)
├── scripts/
│   ├── build_kb.py          downloads ATT&CK and D3FEND and builds kb.json.gz
│   ├── index_pinecone.py    creates and fills the Pinecone index
│   ├── run_samples.py       runs the live pipeline on the samples and saves the results
│   ├── export_results.py    copies the measured numbers into public/results.json
│   └── dev.py               local server: API + page on http://127.0.0.1:8000
├── eval/
│   ├── build_eval_set.py    turns Atomic Red Team tests into 30 labelled alerts (alerts.jsonl, kept out of git)
│   ├── run_eval.py          scores the ATT&CK mapping
│   ├── export_triage.py     pulls past triage answers back out of LangSmith
│   ├── tune_retrieval.py    replays search offline from saved triage answers to compare search settings
│   └── results/             saved scores (IDs and numbers only)
├── benchmarks/
│   ├── crewai_flow.py       the same pipeline rebuilt with CrewAI, for comparison
│   └── compare.py           runs both versions on the same alerts and compares them
├── tests/
│   ├── test_pipeline.py     knowledge graph, search, guards, critic loop, feedback loop
│   ├── test_mcp.py          starts the MCP server and calls its tools
│   ├── test_server.py       the demo-code lock on the web API and the A2A endpoint
│   └── conftest.py          lets the tests import the package
├── docs/                    you are here
├── requirements.txt         what the deployed function needs
├── requirements-dev.txt     plus what you need for scripts, tests and MCP
├── vercel.json              tells Vercel how to route URLs to the function
├── .env.example             template for your keys (copy to .env, which git ignores)
├── LICENSE                  MIT licence, plus the MITRE attribution
└── README.md                the front page of the repository
```

## How the pieces call each other

```mermaid
flowchart TB
    page[public/index.html] -->|fetch + SSE| api[server.py]
    a2aclient[Other AI agent] -->|JSON-RPC| api
    mcpclient[Claude / Cursor] -->|stdio| mcp[mcp_server.py]
    api --> service[service.py]
    mcp --> service
    service --> graph[graph.py]
    graph --> llm[llm.py] --> groq[(Groq)]
    graph --> retrieval[retrieval.py] --> pinecone[(Pinecone)]
    retrieval --> kb[kb.py]
    graph --> kb
    graph --> prompts[prompts.py]
    graph --> schemas[schemas.py]
    llm -.traces.-> langsmith[(LangSmith)]
```

## Where to change things

| I want to... | Edit |
|---|---|
| change what an agent is told | `prompts.py` |
| add a field to an agent's answer | `schemas.py` (and its prompt) |
| add or reorder a step | `graph.py`, function `build_graph` |
| allow more critic rounds | `A2P_MAX_REVISIONS` setting |
| change how search ranks techniques | `retrieval.py` |
| change a word or explanation on the page | `public/content.js` |
| change how the page behaves | `public/app.js` |
| change the look | `public/app.css` |
| change or add a sample alert | `data/samples.json`, then `python scripts/run_samples.py` |
| change the demo code | `A2P_DEMO_CODE` in Vercel's settings, then redeploy |
| add an API endpoint | `server.py` |
| add an MCP tool | `mcp_server.py` |

Next: [Why we built it this way](11-decisions.md)
