# 9. Run it yourself

Everything here can run on free plans. You need Python 3.12 and about 10 minutes.

## 1. Get the code and install

```bash
git clone https://github.com/M-AbubakarNadeem/Alert2Playbook.git
cd Alert2Playbook
python -m venv .venv
# Windows:  .venv\Scripts\activate
# Mac/Linux: source .venv/bin/activate
pip install -r requirements-dev.txt
```

## 2. Run the tests (no keys needed)

```bash
pytest
```

74 tests in about 15 seconds. They use the real knowledge graph, a scripted fake AI and fake provider replies, so they need no internet and no keys. They check the knowledge graph, the search, every guard on page 6, the critic loop, the analyst feedback loop, the demo-code lock, visitor keys (format checks, the provider check, that a key never ends up in a log or an error), the dashboard login and the MCP server. A line of green dots and "74 passed" means everything works.

The tests never read your secrets file, so they can't touch your real database or keys.

## 3. Get the free keys

| Key | Where | Needed for |
|---|---|---|
| `GROQ_API_KEY` | console.groq.com → API Keys | the AI agents (required) |
| `PINECONE_API_KEY` | app.pinecone.io → API Keys (free Starter plan) | meaning search (optional, recommended) |
| `LANGSMITH_API_KEY` | smith.langchain.com → Settings → API Keys | tracing every AI call (optional) |

Copy `.env.example` to `.env` and paste the keys after the `=` signs. `.env` is ignored by git, so it never gets uploaded.

## 4. Fill the search index (once)

```bash
python scripts/index_pinecone.py
```

This creates a Pinecone index called `alert2playbook` in AWS us-east-1 and loads all 810 techniques. It prints a `PINECONE_HOST` value; add it to `.env` to save one lookup when the server starts.

## 5. Run locally

```bash
python scripts/dev.py
```

Open http://127.0.0.1:8000. Click a sample and press **Start**, or build your own alert. If you set `A2P_DEMO_CODE` in your `.env`, live runs are locked locally too: add your own key in the box under the alert, or use the code. Leave it out and everything runs on `GROQ_API_KEY` without a code.

The local server sends the same security headers as the live site and serves the dashboard at http://127.0.0.1:8000/admin.

## 6. Set up the dashboard (optional)

```bash
python scripts/make_admin.py
```

This writes a username, a random password, the password's hash and two random secrets into the file named by `A2P_ENV_FILE`. It never prints the password: open that file to read it. Only the hash goes to Vercel. Run it again any time to change the password, which also signs out every open session.

For the run log and rate limits to survive between serverless instances, add a free Upstash Redis database (page 12 explains why). Without one, they live in memory and reset whenever the server restarts.

## 7. Useful scripts

| Command | What it does |
|---|---|
| `python scripts/build_kb.py` | Rebuild `data/kb.json.gz` from the latest ATT&CK and D3FEND |
| `python scripts/index_pinecone.py` | Create or refresh the Pinecone index |
| `python scripts/run_samples.py` | Run the live pipeline on the samples and save the runs the page shows |
| `python eval/build_eval_set.py` | Build the 30 labelled test alerts from Atomic Red Team (run this first; the file is not in the repository because it contains real attack commands) |
| `python eval/run_eval.py` | Score ATT&CK mapping (add `--bm25-only` for the comparison) |
| `python eval/tune_retrieval.py` | Compare search settings offline from saved triage answers |
| `python benchmarks/compare.py` | LangGraph vs CrewAI on the same alerts (needs the benchmark environment, see the file's header) |
| `python scripts/export_results.py` | Copy the latest measured numbers into `public/results.json` for the website |
| `python scripts/make_admin.py` | Create or change the dashboard login |

## 8. Deploy for free on Vercel

1. Push the repository to GitHub.
2. On vercel.com, **Add New → Project**, import the repository. No build settings are needed: Vercel finds the FastAPI `app` in `server.py` and runs it as one Python function, and serves `public/` as the website from its CDN. `vercel.json` only raises the time limit to 60 seconds.
3. In **Settings → Environment Variables**, add `GROQ_API_KEY`, `PINECONE_API_KEY`, `PINECONE_HOST` and, for tracing, `LANGSMITH_TRACING=true`, `LANGSMITH_API_KEY`, `LANGSMITH_PROJECT=alert2playbook`. If your Groq key is paid, also add `A2P_DEMO_CODE` (any phrase you choose), so your key only runs for people you give it to. Everyone else brings their own key.
4. For the dashboard: in **Storage**, add **Upstash for Redis** (free). Vercel sets `KV_REST_API_URL` and `KV_REST_API_TOKEN` for you. Then add `A2P_ADMIN_USER`, `A2P_ADMIN_PASSWORD_HASH`, `A2P_ADMIN_SESSION_SECRET` and `A2P_FINGERPRINT_SECRET` from your secrets file. Never add the plain password.
5. Deploy. The page, the API, the Agent Card and the A2A endpoint are all on one URL.

The live demo of this project is deployed from the command line instead: `vercel deploy --prod` with a `VERCEL_TOKEN`, from the project folder. Both ways give the same result.

Why Vercel: free, no credit card, no server to manage, and both Vercel's default region and Pinecone's free region are in the US East, so search calls stay fast.

## 9. Change the models

Each agent's model is a setting, so you can swap them without touching code:

```bash
A2P_TRIAGE_MODEL=qwen/qwen3.8-27b
A2P_MAPPER_MODEL=openai/gpt-oss-20b
A2P_WRITER_MODEL=openai/gpt-oss-120b
A2P_CRITIC_MODEL=qwen/qwen3.8-27b
A2P_FALLBACK_MODEL=openai/gpt-oss-120b
A2P_GUARD_MODEL=meta-llama/llama-prompt-guard-2-86m
```

## Troubleshooting

| You see | It means | Do this |
|---|---|---|
| "Live runs need your own API key" | The server has a demo code, and the request had neither a key nor the code | Add your key in the box under the alert, or watch a saved run |
| "Groq didn't accept that key" | The provider refused the key during the check | Copy it again in full, check it hasn't been revoked, and that you picked the right provider |
| "That's 10 live runs from you in the last hour" | The per-visitor limit | Wait, or watch a saved run |
| "... couldn't produce the structured answers this needs" | The model you picked can't fill in the agents' forms reliably | Pick a larger model from the list |
| Dashboard says "The shared database isn't connected" | No Upstash variables on the server | Add Upstash (step 4 of the deploy) |
| "The AI provider is busy right now" | Groq's per-minute or daily limit was hit (common on a free key) | Wait a minute, or use a paid key |
| Footer says "BM25 only" | No Pinecone key, or Pinecone failed | Add the key and run `scripts/index_pinecone.py` |
| `GROQ_API_KEY is not set` | The server started without the key | Check `.env` or the Vercel settings |
| A run takes 20+ seconds | On a free Groq key, several runs in a row hit the per-minute token limit; the client waits and retries | Space runs out, use a paid key, or use the saved samples |

Next: [Map of the code](10-code-map.md)
