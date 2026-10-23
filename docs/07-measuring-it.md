# 7. Measuring how good it is

"It looks right" is not a measurement. This page explains how we test the project and what the numbers are.

There are two kinds of tests:

1. **Unit tests** check that the machinery works. They are fast, free and run without internet.
2. **The evaluation** checks how often the AI picks the right ATT&CK technique on alerts whose right answer we know.

## 1. Unit tests

```bash
pytest
```

74 tests, about 15 seconds, no keys needed. They use the real knowledge graph and search, plus a scripted fake AI and fake provider replies that return answers we choose. They check that:

- the knowledge graph covers Enterprise and ICS, and old revoked IDs lead to their replacements;
- search finds the right technique from a behaviour description, and IDs written in the alert or exact technique names come first;
- RRF rewards techniques that several searches agree on;
- an invented technique (`T9999`) from the mapper is thrown away;
- an invented mitigation (`M9999`) from the writer forces a revision, even when the AI critic approves;
- if revisions run out, invented IDs are removed before the analyst sees the playbook;
- analyst feedback re-enters the graph at the writer and is kept in the history;
- the MCP server starts and its tools answer, exactly as an MCP client would see them;
- error messages shown to visitors or saved to results files have account IDs and key-shaped strings removed;
- the demo-code lock: live runs and A2A calls are refused without the right code or a visitor key, wrong codes are refused, guessing is rate limited, and the Agent Card lists both ways in;
- visitor keys: unknown providers, malformed keys and keys from the wrong provider are refused before any network call; only the provider's own "yes" lets a run start; redirects are never followed; a key never appears in the run log, an error or the dashboard's data; and any model can be driven, stepping down from forced tool calls to plain JSON when a model can't do them;
- the dashboard login: passwords are only stored as hashes, sessions can't be forged or reused after they expire or the password changes, sign-ins are rate limited, and sign-ins from other sites are refused.

## 2. The evaluation

### The test alerts

We need alerts where we already know the right technique. We take them from **Atomic Red Team**, a free library from the security company Red Canary in which every test is a real command that performs one known ATT&CK technique.

`eval/build_eval_set.py` turns 30 of those commands into EDR-style alerts. Before using them it removes anything that would give the answer away:

- technique IDs anywhere in the command (even inside file names like `T1020_exfil.txt`);
- the test-folder path, which contains the technique ID;
- the words "atomic" and "Red Canary";
- tests whose file name was only the technique ID, because nothing useful is left after removing it.

The 30 alerts are spread over 14 tactics (2 or 3 per tactic), one technique each, mostly Windows with some Linux and macOS.

These alerts are **hard on purpose**: each one is a single command with no story around it. Real alerts usually have more context, like the sample alerts on the page.

### What we score

`eval/run_eval.py` runs triage, search and mapping (no playbook, to save tokens) and measures:

| Score | Question it answers |
|---|---|
| Candidate recall@k | Is the right technique among the k search results at all? |
| Exact@1 | Is the mapper's first pick exactly right? |
| Exact@3 | Is the right technique among the mapper's picks (up to 3)? |
| Family@3 | Is a pick at least in the right technique family (for example T1059.001 when the answer is T1059.003)? |

### Results

Four full runs on the same 30 alerts, with Qwen 3.8 27B for triage and gpt-oss-20b for mapping:

| Run | Groq key | Search | Candidates | Recall | Exact@1 | Exact@3 | Family@3 | Median time | Scored |
|---|---|---|---|---|---|---|---|---|---|
| 1 | free tier | Keyword only (BM25) | 15 | 69.0% | 13.8% | 41.4% | 58.6% | 20.8 s | 29 of 30 |
| 2 | free tier | Hybrid | 15 | 67.9% | 10.7% | 39.3% | 50.0% | 19.5 s | 28 of 30 |
| 3 | free tier | Hybrid | 25 | 73.3% | 26.7% | 46.7% | 60.0% | 20.1 s | 30 of 30 |
| **4 (current)** | **paid** | **Hybrid** | **25** | **83.3%** | **30.0%** | **46.7%** | **60.0%** | **2.3 s** | **30 of 30** |

Each run's per-alert answers are in `eval/results/` (`mapping-bm25-k15.json`, `mapping-hybrid-k15.json`, `mapping-hybrid-k25-free-tier.json`, `mapping-hybrid-k25.json`).

**Why run 4 is so much faster.** Runs 1 to 3 used a free Groq key, which allows 8,000 tokens per minute per model, so most of those 20 seconds were spent waiting for the limit to reset. Run 4 used a paid key with much higher limits. The code was the same.

**Be careful comparing rows.** The AI agents do not give exactly the same answer twice, and with 30 alerts one alert is worth 3.3 points. Runs 3 and 4 have identical settings, yet recall differs by 10 points, because the triage agent wrote different search hints. Runs 1 and 2 also differ in more than search: between them a mapper rule was added (rank the attacker's goal above incidental launchers like `cmd.exe`) and the bug below was fixed. So the rows above show where the system stands, not a clean comparison of search methods. The clean comparison is the next section.

### Comparing search methods fairly: the offline study

To compare search methods without AI randomness, `eval/export_triage.py` pulled run 2's saved triage answers back out of LangSmith (28 alerts). Then `eval/tune_retrieval.py` replayed **only the search step** with different settings. Same triage, same alerts, so search is the only thing that changes, and it costs no LLM tokens:

| Search setting | Recall@10 | @15 | @20 | @25 | @30 |
|---|---|---|---|---|---|
| Keyword only (BM25) | 64.3% | 67.9% | 71.4% | 71.4% | 71.4% |
| Meaning only (Pinecone) | 60.7% | 64.3% | 67.9% | 75.0% | 78.6% |
| **Hybrid (both, merged with RRF)** | 57.1% | **71.4%** | 71.4% | **78.6%** | **82.1%** |
| Hybrid, hints merged instead of pinned | 60.7% | 67.9% | 75.0% | 78.6% | 82.1% |
| Hybrid, no technique-name hints | 60.7% | 67.9% | 71.4% | 78.6% | 78.6% |

What this shows:

- **Keyword search stops improving at 20 results (71.4%). Hybrid keeps climbing (82.1% at 30).** Meaning search finds right answers that keyword search never ranks at all.
- **Hybrid is not better everywhere.** With only 10 results it is the worst setting (57.1%): merging two lists pushes some strong keyword hits down. Its advantage only shows when the mapper sees enough candidates.
- **More candidates help.** Going from 15 to 25 candidates lifts hybrid recall from 71.4% to 78.6%, for about 450 extra tokens per alert. That is why the mapper now sees 25 (`A2P_CANDIDATES`). Run 3 above confirms it end to end.
- **Hints help at the top.** Removing the technique-name hints costs recall at 15 and 30.

These settings were compared on the same 30 alerts used for scoring, so treat the chosen setting as slightly optimistic. We changed only one setting, the number of candidates, and it is a simple "more is better until it costs too much" choice rather than something fitted to these alerts.

### What the numbers tell us

- **Search is still the bottleneck.** In about one alert in six the right technique never reaches the mapper.
- **Close, but not exact, is common.** Family@3 is 13 points above Exact@3: the system often finds the right kind of attack but a neighbouring sub-technique.
- **The first pick is often a launcher.** Many test alerts run through PowerShell, and the mapper often lists "PowerShell" first, which is true but is not the labelled answer. That is why Exact@3 is much higher than Exact@1, and why the playbook covers up to three techniques.
- **The test is harder than real use.** Each test alert is one command with no story around it. The sample alerts on the demo page, which read like real alerts, map correctly (for example T1486 and T1490 for the ransomware sample).

### Two bugs the evaluation found

1. **Requests refused for being "too large".** Groq answered *request too large ... output tokens per minute: limit 1000, requested 1905*. We had not set a maximum reply length, so Groq assumed each reply could be as long as the whole remaining context window, and refused. The same failure could have hit the live demo on a long alert. Fix: every agent has a reply-length cap sized from real runs (`MAX_TOKENS` in `llm.py`).
2. **Giving up too early on rate limits.** Qwen's free tier allows only 1,000 output tokens per minute, so back-to-back runs hit the limit, and the client gave up after 2 retries. Groq says how long to wait, so the client now retries up to 5 times. Run 3 had no failures.

## Re-running it

```bash
python eval/build_eval_set.py          # build the 30 alerts first (they are not in the repository)
python eval/run_eval.py --bm25-only    # keyword search only
python eval/run_eval.py                # hybrid search (needs PINECONE_API_KEY)
python eval/export_triage.py --hours 6 # save the triage answers of a recent run from LangSmith
python eval/tune_retrieval.py          # compare search settings offline, no LLM tokens
```

`run_eval.py` also saves each alert's triage answer to `eval/results/triage-cache.json`, so `tune_retrieval.py` works without LangSmith. That cache quotes the alerts' command lines, so git ignores it, like the alerts themselves.

A full run uses about 100,000 Groq tokens: a few US cents on a paid key. On the free tier (200,000 tokens per model per day) run it at most once a day.

After any new run, `python scripts/export_results.py` copies the numbers into `public/results.json`, which feeds the website's "Results" tab. The website never shows a number that is not in a results file.

Next: [Talking to other programs](08-talking-to-other-agents.md)
