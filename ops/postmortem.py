"""Write a blameless postmortem from what Cloud Logging recorded.

    python ops/postmortem.py --project PROJECT --freshness 3h

The numbers and the timeline come from the logs and are built here in code. Gemini
only writes the prose around them (summary, five whys, action items), and is told
to use nothing but the facts it is given. Read it before you share it: it is a
first draft for the people who were there to correct.
"""

from __future__ import annotations

import argparse
import json
import statistics
import subprocess
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

LOG_FILTER = (
    'resource.type="cloud_run_revision" AND '
    '(jsonPayload.event="maple.turn" OR jsonPayload.event="guard.rollback")'
)


def read_logs(project: str, freshness: str) -> list[dict]:
    out = subprocess.run(
        ["gcloud", "logging", "read", LOG_FILTER, "--project", project,
         f"--freshness={freshness}", "--order=asc", "--format=json"],
        capture_output=True, text=True, check=True, shell=(subprocess.os.name == "nt"),
    ).stdout
    return json.loads(out or "[]")


def when(entry: dict) -> datetime:
    return datetime.fromisoformat(entry["timestamp"].replace("Z", "+00:00"))


def facts_from(entries: list[dict], window_min: int = 30) -> dict:
    """Facts about the latest incident: the last rollback, and the turns just before it.

    Turns after the rollback are not user impact (someone asking the challenger's own
    link, an eval run), so the window stops two minutes after it.
    """
    rollbacks = [e for e in entries if e["jsonPayload"]["event"] == "guard.rollback"]
    if not rollbacks:
        raise SystemExit("No guard.rollback in that window, so there is no incident to write up.")
    rollback = rollbacks[-1]
    end = when(rollback) + timedelta(minutes=2)
    start = when(rollback) - timedelta(minutes=window_min)
    turns = [
        e for e in entries
        if e["jsonPayload"]["event"] == "maple.turn" and e["jsonPayload"].get("search_allowed") and start <= when(e) <= end
    ]
    if not turns:
        raise SystemExit("No Maple turns around the rollback. Widen --freshness.")

    by_version: dict[str, list[dict]] = defaultdict(list)
    for e in turns:
        by_version[e["jsonPayload"]["prompt_version"]].append(e)
    versions = {}
    for v, rows in by_version.items():
        p = [r["jsonPayload"] for r in rows]
        versions[v] = {
            "answers": len(rows),
            "first": when(rows[0]).isoformat(timespec="seconds"),
            "last": when(rows[-1]).isoformat(timespec="seconds"),
            "pct_searched": round(100 * sum(x["grounded"] for x in p) / len(p)),
            "median_latency_ms": int(statistics.median(x["latency_ms"] for x in p)),
            "median_output_tokens": int(statistics.median(x["output_tokens"] for x in p)),
            "revisions": sorted({x["revision"] for x in p}),
        }
    bad = min(versions, key=lambda v: versions[v]["pct_searched"])
    first_bad = datetime.fromisoformat(versions[bad]["first"])
    opened = rollback["jsonPayload"].get("started_at")
    opened_at = datetime.fromtimestamp(opened, timezone.utc) if opened else None
    return {
        "bad_version": bad,
        "versions": versions,
        "first_bad_answer_at": first_bad.isoformat(timespec="seconds"),
        "alert_opened_at": opened_at.isoformat(timespec="seconds") if opened_at else None,
        "rollback_at": when(rollback).isoformat(timespec="seconds"),
        "seconds_from_first_bad_answer_to_alert": int((opened_at - first_bad).total_seconds()) if opened_at else None,
        "seconds_from_first_bad_answer_to_rollback": int((when(rollback) - first_bad).total_seconds()),
        "guard_moved_traffic": rollback["jsonPayload"].get("now"),
    }


def timeline(facts: dict) -> str:
    rows = [(facts["first_bad_answer_at"], f"First answer from prompt {facts['bad_version']}")]
    if facts["alert_opened_at"]:
        rows.append((facts["alert_opened_at"], "Alert opened: most answers skipped the search"))
    rows.append((facts["rollback_at"], "maple-guard moved traffic back to champion"))
    lines = ["| Time (UTC) | What happened |", "|---|---|"]
    lines += [f"| {t.replace('T', ' ').replace('+00:00', '')} | {text} |" for t, text in sorted(rows)]
    return "\n".join(lines)


def numbers(facts: dict) -> str:
    lines = ["| Prompt | Answers | Searched first | Median latency | Median output tokens |", "|---|---|---|---|---|"]
    for v, d in facts["versions"].items():
        lines.append(f"| {v} | {d['answers']} | {d['pct_searched']}% | {d['median_latency_ms']} ms | {d['median_output_tokens']} |")
    return "\n".join(lines)


PROMPT = """You are writing the prose of a blameless postmortem for an AI assistant called Maple.
Use ONLY the facts below. Do not name people, and do not invent causes, durations or numbers: use the ones given.
Describe what the system did, not who was at fault.

Known change: release v2.4 changed the prompt so Maple answers from memory and never calls the search tool.
It was promoted to serve all traffic; an alert on the share of answers that skipped the search opened;
a small service called maple-guard moved traffic back to the revision tagged champion.

Facts (JSON):
{facts}

Write markdown with exactly these sections and nothing else:
## Summary  (three sentences)
## Impact  (use the numbers and the two "seconds_from_first_bad_answer" fields exactly as given)
## Five whys  (write why 1 from the facts only. For whys 2 to 5 write the question the team has to answer, each starting with "Team to answer:". The logs cannot say why a release was approved or tested)
## What went well  (two bullets)
## Action items  (three to five bullets; each starts with a role, such as ML engineer or SRE, never a name)
"""


def prose(facts: dict, model: str) -> str:
    from google import genai

    client = genai.Client(vertexai=True, location="global")
    reply = client.models.generate_content(model=model, contents=PROMPT.format(facts=json.dumps(facts, indent=2)))
    return reply.text.strip()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", required=True)
    ap.add_argument("--freshness", default="3h")
    ap.add_argument("--model", default="gemini-3.1-flash-lite")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    facts = facts_from(read_logs(args.project, args.freshness))
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    out = Path(args.out or f"postmortems/{day}-maple-v2.4.md")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        f"# Postmortem: Maple v2.4 stopped searching ({day})\n\n"
        f"_Draft from the logs. Numbers and timeline are computed; the prose was written by a model from those facts._\n\n"
        f"## Timeline\n\n{timeline(facts)}\n\n## Numbers\n\n{numbers(facts)}\n\n{prose(facts, args.model)}\n",
        encoding="utf-8",
    )
    print(f"wrote {out}")
