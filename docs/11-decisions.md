# 11. Why we built it this way

Every project is a pile of choices. Here are ours, with what each one costs.

## LangGraph for the agents

**Chosen because** the workflow has a fixed order, one loop (critic back to writer), one second entrance (analyst feedback goes straight to the writer) and a need to stream progress. LangGraph models exactly that as an explicit graph of nodes and edges, with a typed state, conditional edges and built-in streaming of each node's update. Most steps are plain code (retrieve, enrich, checks) and LangGraph mixes code nodes and AI nodes naturally.

**Given up:** the "crew of roles that chat with each other" style of frameworks like CrewAI or AutoGen. We wanted predictable, testable control flow more than open-ended conversation between agents.

### We measured it: the same pipeline in CrewAI

Instead of guessing, `benchmarks/crewai_flow.py` rebuilds the same pipeline with CrewAI: a CrewAI Flow moves the state between steps, and each AI step is a one-agent crew. Same models, prompts, schemas, search, knowledge graph and code checks; only the framework differs. `benchmarks/compare.py` runs both on the same sample alerts, alternating which goes first.

On all 7 sample alerts, with a paid Groq key (so rate limits do not distort the times):

| | LangGraph (ours) | CrewAI, same reply caps | CrewAI, bigger reply caps |
|---|---|---|---|
| Runs completed | **7 of 7** | 1 of 7 | 5 of 7 |
| Median time | **7.2 s** | 9.2 s (the one run) | 22.9 s |
| Tokens per alert | **~11,700** | ~12,600 (the one run) | ~21,200 |

What went wrong on the CrewAI side:

- **Empty replies** (4 runs across both settings): the model's answer came back empty through CrewAI's prompt format.
- **JSON cut off** (2 runs): CrewAI asks the model to think out loud ("Thought: ... Final Answer: ...") and wraps every prompt in role, goal, backstory and schema instructions. That extra writing ran past the reply-length cap.
- **"Tool choice is none, but model called a tool"** (2 runs): gpt-oss decided to call a tool although CrewAI had told it not to, and Groq rejected the reply.
- **Bigger caps** fixed most runs but almost doubled the tokens. One run needed 11 requests and 42,000 tokens because of retries.

LangGraph calls the model with a forced tool call and nothing else around it, which is why it uses fewer tokens and gets a valid form back.

An earlier run of the same comparison on the free key showed the same pattern (LangGraph 4 of 4; CrewAI 1 of 4 and 3 of 4), with longer times caused by rate limits. Results: `benchmarks/results-same-caps.json`, `benchmarks/results-raised-caps.json`, and the `*-free-tier.json` files.

This is 7 alerts, so it shows a direction rather than a final verdict, and it is specific to Groq's models. CrewAI is quicker to start with for role-based prototypes. For a fixed pipeline that must return valid structured output every time, plain graph orchestration was cheaper and more reliable here.

## Small, structured agents instead of one big prompt

Each agent has one job, one input and one validated output form. That makes each step testable on its own, lets us use a different model per step, and lets code check the hand-offs. One giant prompt would be shorter to write but impossible to debug.

## Groq, and a different model per agent

**Groq** runs open models very fast (a short reply in well under a second). The whole project works on Groq's free tier, which limits tokens **per model** (8,000 per minute and 200,000 per day each); the live demo uses a paid key so visitors never wait for those limits. Each agent gets its own model:

| Agent | Model | Why |
|---|---|---|
| Triage | Qwen 3.8 27B | fast and reliable at filling in a long form |
| Mapper | gpt-oss-20b | short task, cheap |
| Writer | gpt-oss-120b | the hardest task gets the strongest model |
| Critic | Qwen 3.8 27B | a different family from the writer |
| Fallback | gpt-oss-120b | used only when another model fails twice |
| Guard | Llama Prompt Guard 2 86M | a classifier built for this exact job |

On the free tier, spreading the agents over three models also roughly triples how many runs fit in a day.

## A demo code in front of the paid key

The live site runs on the owner's paid Groq key, so anyone on the internet could spend that credit. Instead of a daily cap, live runs need a **demo code** (`A2P_DEMO_CODE`, checked in constant time against the `X-Demo-Code` header). Everyone can still open every sample, which replays a saved real run step by step without calling any AI. Code guesses are rate limited, and the A2A Agent Card announces the requirement as an API-key security scheme, so other agents know what to send. The price: a hiring manager cannot run a custom alert alone; they watch the replays, or see it live in an interview.

## Two problems we hit, and the fixes

1. **gpt-oss lower-cases tool names.** We ask models to answer by "calling a tool" whose arguments are our form. gpt-oss called `mapping` when the tool was named `Mapping`, and Groq rejected the call. Fix: we name every tool in snake_case ourselves (`llm.py`).
2. **The smallest model sometimes skipped required fields.** On the cloud sample, gpt-oss-20b twice returned a triage form missing four fields. Fix: triage moved to Qwen, and every call now has a ladder: try, retry once with the error message, then try the fallback model. The step record shows `fallback_from` when that happens.

## Pinecone with built-in embeddings

The usual approach puts an embedding model inside the server. That would make our serverless function hundreds of megabytes and slow to start. Pinecone's integrated inference embeds the text on Pinecone's side, both when indexing and when searching, so the function stays small. The free Starter plan is enough for 810 short records.

## BM25 written by hand

About 30 lines of Python instead of a search library. 810 documents is tiny, so it runs in milliseconds, has no dependencies and keeps working when Pinecone is unavailable.

## RRF to merge results

Keyword scores and embedding scores live on different scales, and tuning weights between them needs lots of labelled data. RRF uses only positions in each list, needs no tuning, and is a well-known strong baseline for hybrid search.

## A knowledge graph in plain dictionaries

The graph has about 2,500 nodes. A graph database would add a service to run and pay for. Python dictionaries keyed by ID give the same walk in under a millisecond, and the whole graph fits in a 0.4 MB file shipped with the code.

## Human-in-the-loop without a database

LangGraph can pause a run and resume it later, but that needs a database to remember the paused run between requests, and a serverless function forgets everything after each request. Instead, the browser keeps the finished state and sends it back with the analyst's request. The server trusts only the technique IDs from it and rebuilds everything else. No database, no accounts, nothing stored about your alerts.

## Vercel for hosting

Free, no credit card, no server to manage, one URL for the page, the API, the Agent Card and the A2A endpoint. The trade-off: each request must finish within the function time limit (60 seconds here), and in-memory things like the rate limiter are per instance.

## Saved runs for the samples

Visitors usually click a sample first. Without the demo code, the page plays back a saved real run step by step, so anyone can see the whole flow without calling any AI or spending credit.

## ATT&CK Enterprise and ICS, plus D3FEND

Many alert tools only cover normal company IT. Adding the ICS domain lets the same pipeline handle factory and utility alerts (the sample "Unauthorised Modbus writes and PLC mode change" maps to ICS techniques T0836 and T0858). D3FEND adds the defender's side, so the playbook is grounded in named countermeasures, not only in prevention advice.

## NIST SP 800-61 phases

Responders already think in these phases, so the playbook drops straight into how SOC teams work.

Back to the [start](README.md).
