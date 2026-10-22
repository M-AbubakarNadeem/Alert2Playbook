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

19 tests in about 4 seconds. They use the real knowledge graph and a scripted fake AI, so they need no internet and no keys. They check the knowledge graph, the search, every guard on page 6, the critic loop, the analyst feedback loop, the demo-code lock and the MCP server. A line of green dots and "19 passed" means everything works.

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

Open http://127.0.0.1:8000. Click a sample and press **Start**, or build your own alert. If you set `A2P_DEMO_CODE` in your `.env`, live runs are locked locally too; leave it out and everything runs without a code.

## 6. Useful scripts

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

## 7. Deploy for free on Vercel

1. Push the repository to GitHub.
2. On vercel.com, **Add New → Project**, import the repository. No build settings are needed: Vercel finds the FastAPI `app` in `server.py` and runs it as one Python function, and serves `public/` as the website from its CDN. `vercel.json` only raises the time limit to 60 seconds.
3. In **Settings → Environment Variables**, add `GROQ_API_KEY`, `PINECONE_API_KEY`, `PINECONE_HOST` and, for tracing, `LANGSMITH_TRACING=true`, `LANGSMITH_API_KEY`, `LANGSMITH_PROJECT=alert2playbook`. If your Groq key is paid, also add `A2P_DEMO_CODE` (any phrase you choose), so only people you give it to can run live.
4. Deploy. The page, the API, the Agent Card and the A2A endpoint are all on one URL.

The live demo of this project is deployed from the command line instead: `vercel deploy --prod` with a `VERCEL_TOKEN`, from the project folder. Both ways give the same result.

Why Vercel: free, no credit card, no server to manage, and both Vercel's default region and Pinecone's free region are in the US East, so search calls stay fast.

## 8. Change the models

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
| "Live runs are locked" | The server has a demo code and the request had none (or a wrong one) | Click the lock button at the top and enter the code, or choose a sample to watch a saved run |
| "The AI provider is busy right now" | Groq's per-minute or daily limit was hit (common on a free key) | Wait a minute, or use a paid key |
| Footer says "BM25 only" | No Pinecone key, or Pinecone failed | Add the key and run `scripts/index_pinecone.py` |
| `GROQ_API_KEY is not set` | The server started without the key | Check `.env` or the Vercel settings |
| A run takes 20+ seconds | On a free Groq key, several runs in a row hit the per-minute token limit; the client waits and retries | Space runs out, use a paid key, or use the saved samples |

Next: [Map of the code](10-code-map.md)
