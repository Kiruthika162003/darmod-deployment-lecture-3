# System card: Maple in production

Generated 2026-11-13 from commit `202adff`.

## Model

`gemini-3.1-flash-lite` with prompt `v2.3`. See the model card for evaluation.

## Prompts and retrieval

- System prompts are in `backend/agent.py`, one per version. Changing one is a code change with a commit.
- For factual questions Maple calls a search sub-agent that uses Google Search. There is no private document store.

## Guardrails

- Orders go through `submit_order`, which is held for an admin to approve before anything ships. The store has 4 items.
- Each session may ask at most 25 questions.
- Maple is told never to invent people, dates or program details (prompt `v2.3`). Prompt `v2.4` removes that and the search; it exists to show why quality is monitored.

## Deployment and monitoring

- Runs on Cloud Run with at most 3 copies. A revision tagged `champion` serves traffic; `challenger` is a pre-built candidate.
- Every answer writes one log line: latency, tokens, whether it searched, prompt version, revision, commit.
- Cloud Monitoring shows infrastructure, app and ML signals together. An alert fires when most answers skip the search.

## Audit and rollback

- Any answer can be traced to its prompt version, revision and commit through the BigQuery `turns` view.
- `maple-guard` moves traffic back to `champion` when the alert opens, and logs what it did. A person can do the same with `gcloud run services update-traffic maple --to-tags champion=100`.
- Logs hold no question or answer text.

## Evaluation on file

| Prompt | Correct | Searched first | Median latency | Median output tokens |
|---|---|---|---|---|
| v2.3 | 5 of 5 | 5 of 5 | 3138 ms | 79 |
| v2.4 | 4 of 5 | 0 of 5 | 798 ms | 48 |
