"""Write Maple's model card and system card.

    python ops/make_cards.py --champion ops/eval_champion.json --challenger ops/eval_challenger.json

What a script can know (model, settings, version, commit, test results, which
guardrails exist) is read from the code and the eval reports. What only people
can write (intended use, limits, ethics) comes from ops/card_human.json. Rerun
it on every promotion so the numbers never lag the model.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backend.config import get_settings  # noqa: E402
from backend.tools import CATALOG  # noqa: E402


def commit() -> str:
    try:
        return subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True, text=True, check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def bullets(items: list[str]) -> str:
    return "\n".join(f"- {i}" for i in items)


def results_table(reports: list[dict]) -> str:
    lines = ["| Prompt | Correct | Searched first | Median latency | Median output tokens |", "|---|---|---|---|---|"]
    for r in reports:
        v = r["version"]["prompt_version"]
        lines.append(
            f"| {v} | {r['correct']} of {r['questions']} | {r['searched']} of {r['questions']} | "
            f"{r['median_latency_ms']} ms | {r['median_output_tokens']} |"
        )
    return "\n".join(lines)


def model_card(s, human: dict, reports: list[dict]) -> str:
    return f"""# Model card: Maple

Generated {date.today().isoformat()} from commit `{commit()}`. Qualitative sections are written by people in `ops/card_human.json`.

## Version

- Prompt version in production: `{s.prompt_version}`
- Base model: `{s.chat_model}` (Google Gemini, served by Vertex AI)
- Temperature {s.temperature}, thinking budget {s.thinking_budget}, at most {s.max_output_tokens} output tokens

## Intended use

{human["intended_use"]}

## Training data

{human["training_data"]}

## Evaluation

{reports[0]["questions"]} fixed questions about Vector Institute, each with a word the right answer must contain, asked of the running service on {reports[0]["checked_on"]}. It is a small check, not a benchmark: it is enough to see what a change did, not to rank models.

{results_table(reports)}

Per-group performance (by language, topic or user type) was not measured.

## Known limitations

{bullets(human["limitations"])}

## Ethical considerations

{bullets(human["ethics"])}
"""


def system_card(s, human: dict, reports: list[dict]) -> str:
    return f"""# System card: Maple in production

Generated {date.today().isoformat()} from commit `{commit()}`.

## Model

`{s.chat_model}` with prompt `{s.prompt_version}`. See the model card for evaluation.

## Prompts and retrieval

- System prompts are in `backend/agent.py`, one per version. Changing one is a code change with a commit.
- For factual questions Maple calls a search sub-agent that uses Google Search. There is no private document store.

## Guardrails

- Orders go through `{", ".join(s.hitl_tools)}`, which is held for an admin to approve before anything ships. The store has {len(CATALOG)} items.
- Each session may ask at most {s.max_turns_per_session} questions.
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

{results_table(reports)}
"""


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--champion", required=True)
    ap.add_argument("--challenger", required=True)
    ap.add_argument("--out", default=str(ROOT / "docs"))
    args = ap.parse_args()

    s = get_settings()
    human = json.loads((ROOT / "ops" / "card_human.json").read_text(encoding="utf-8"))
    reports = [json.loads(Path(p).read_text(encoding="utf-8")) for p in (args.champion, args.challenger)]
    out = Path(args.out)
    out.mkdir(exist_ok=True)
    (out / "MODEL_CARD.md").write_text(model_card(s, human, reports), encoding="utf-8")
    (out / "SYSTEM_CARD.md").write_text(system_card(s, human, reports), encoding="utf-8")
    print(f"wrote {out / 'MODEL_CARD.md'} and {out / 'SYSTEM_CARD.md'}")
