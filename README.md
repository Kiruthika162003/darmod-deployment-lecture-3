# Maple, lecture 3: running it in production

Maple is the Vector Institute assistant from lectures 1 and 2. Lecture 3 is about
what happens after it is deployed: how you see it, get told when it breaks, put
it right, know which version is running, and write down what an auditor will ask.

Nothing here is a mock. The monitoring is Google Cloud's own, pointed at the
deployed app.

## What was added to Maple

- **One log line per chat turn** (`backend/telemetry.py`): latency, tokens, whether
  Maple searched before answering, and which prompt version, Cloud Run revision and
  git commit answered. Cloud Logging, the metrics, the dashboard and the BigQuery
  table all read this one line.
- **Two prompt versions** (`backend/agent.py`): `v2.3` searches before it answers.
  `v2.4` is the "faster and cheaper" release that does not, so it is quicker and
  confidently wrong.
- **A reply shows its version** next to the timing line.
- **A turn limit per session** so one browser cannot run up the model bill.

## What sits around it

| Folder | What it is |
|---|---|
| `ops/metrics/` | The three log-based metrics |
| `ops/dashboard.json` | The Cloud Monitoring dashboard: infrastructure, app, ML |
| `ops/` | Alert policy, BigQuery view, lineage query, postmortem and card scripts |
| `guard/` | `maple-guard`, the small Cloud Run service that moves traffic back to `champion` |

## Run it

Everything you paste, in order, is in [demo.txt](demo.txt). The delete commands
are in [shutdown.txt](shutdown.txt), kept apart on purpose.

```bash
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.venv\Scripts\python.exe -m uvicorn backend.main:app --port 8080
```

## Tests

```bash
.venv\Scripts\python.exe -m pytest -q
```

No Google calls. They cover the prompt versions, the turn log, the session limit
and the guard's rollback decision.

MIT licence.
