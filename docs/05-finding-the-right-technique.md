# 5. Finding the right attack technique

There are 810 techniques. The mapper agent cannot read all of them for every alert: that would be slow, expensive and confusing. So a search step first picks the 25 most likely ones, and the mapper chooses only from those. If the right technique is not in the 25, the mapper cannot pick it. That makes search the most important step for accuracy.

## Why one kind of search is not enough

Look at this alert line:

```text
vssadmin.exe delete shadows /all /quiet
```

The right answer is *T1490 Inhibit System Recovery*. But the words "vssadmin", "shadows" and "quiet" barely appear in that technique's name. A pure keyword search ranked other techniques first when we tried it.

Now look at the triage agent's description of the same line: "deleting volume shadow copies". A **meaning** search (embeddings) connects "deleting copies so you cannot restore" with "Inhibit System Recovery", even with no shared words.

The opposite also happens. A rare exact word such as `schtasks`, `crontab` or `Modbus` is a perfect clue for keyword search, while a meaning search can blur it.

So we use both, and we feed them several different questions.

## The four ingredients

### 1. Several queries, not one

For each alert we search with:

- the triage summary,
- each behaviour phrase ("deleting volume shadow copies", "disabling system recovery", ...),
- each technique-name hint ("Data Encrypted for Impact", ...),
- the first 600 characters of the raw alert.

That is usually 8 to 12 queries. Asking the question in several ways finds things a single phrasing misses.

### 2. Keyword search: BM25

Implemented by hand in `kb.py` (about 30 lines, no library). Each technique becomes one document made of its name (repeated three times, because names carry the most signal), its parent's name, its tactics and its description. BM25 scores a document higher when it contains the query's **rare** words, and stops rewarding a word after it appears a few times.

### 3. Meaning search: Pinecone

Each technique's name, tactics, platforms and description is stored in a Pinecone index. Pinecone turns the text into numbers (embeddings) with the `llama-text-embed-v2` model, both when we load the techniques (`scripts/index_pinecone.py`) and when we search. The project therefore ships no embedding model of its own, which keeps the serverless function small and fast to start.

### 4. Merging: Reciprocal Rank Fusion (RRF)

Each query gives two ranked lists (keyword and meaning), so one alert produces about 20 lists. RRF merges them with one rule:

> each list gives a technique `1 / (60 + its position)` points; add up the points.

A technique that sits near the top of many lists wins. RRF only uses positions, not raw scores, so it does not matter that BM25 scores and Pinecone scores are on completely different scales.

### Pinned results

Two kinds of candidates skip the queue and go first:

- **IDs written in the alert.** Some SIEM rules tag alerts with ATT&CK IDs. If the alert says `T1053.005`, that technique is a candidate.
- **Exact names from the hints.** If triage suggests "Data Encrypted for Impact" and that is the exact name of a real technique, it is pinned. Made-up names match nothing and are ignored.

At most half of the 25 slots can be pinned, so search always gets room to find things the hints missed. Pinning does not decide the answer: the mapper must still quote evidence from the alert for anything it picks.

## What happens without Pinecone

If the Pinecone key is missing or Pinecone is down, the retriever quietly falls back to keyword search alone and the page shows "BM25 only". The pipeline keeps working, with lower recall. The evaluation (page 7) measures both modes, so the difference is a number, not a guess.

## Where to look in the code

- `alert2playbook/retrieval.py`: `HybridRetriever.search` and `rrf`
- `alert2playbook/kb.py`: `bm25`, `by_name`, `explicit_ids`
- `scripts/index_pinecone.py`: creates and fills the index

Next: [Keeping it honest and safe](06-keeping-it-honest.md)
