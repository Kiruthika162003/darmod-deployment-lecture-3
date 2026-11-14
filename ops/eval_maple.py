"""Ask a running Maple a short, fixed set of questions and write down what happened.

    python ops/eval_maple.py --url https://champion---maple-....run.app --out ops/eval_champion.json

Six questions, each with a word the right answer has to contain. It is small on
purpose: enough to put honest numbers on the model card and to show what v2.4
changed, not a benchmark. The facts were checked on the date in CHECKED_ON, and
the CEO question is the kind that goes stale, so look at it again before you
rely on a result from another month.
"""

from __future__ import annotations

import argparse
import json
import statistics
import time

import httpx

CHECKED_ON = "2026-11-14"

QUESTIONS = [
    ("Where is Vector Institute headquartered?", ["MaRS", "Toronto"]),
    ("In what year was Vector Institute founded?", ["2017"]),
    ("Name one Vector Institute program for companies doing applied AI projects.", ["FastLane"]),
    ("Who is Vector Institute's chief scientific advisor?", ["Hinton"]),
    ("How much is a Vector Scholarship in AI worth, in Canadian dollars?", ["17,500", "17500"]),
    ("Who is the current president and CEO of Vector Institute?", ["Crisp"]),
]


def ask(client: httpx.Client, url: str, question: str) -> dict:
    t0 = time.monotonic()
    r = client.post(f"{url}/chat", json={"prompt": question})
    r.raise_for_status()
    body = r.json()
    return {
        "question": question,
        "reply": body["reply"],
        "searched": "search_agent" in body["used_tools"],
        "latency_ms": body["latency_ms"],
        "output_tokens": body["usage"].get("output_tokens", 0),
        "version": body["version"],
        "wall_ms": int((time.monotonic() - t0) * 1000),
    }


def run(url: str) -> dict:
    rows = []
    with httpx.Client(timeout=90) as client:
        for question, expected in QUESTIONS:
            row = ask(client, url.rstrip("/"), question)
            row["expected_any_of"] = expected
            row["correct"] = any(word.lower() in row["reply"].lower() for word in expected)
            rows.append(row)
    n = len(rows)
    return {
        "checked_on": CHECKED_ON,
        "url": url,
        "version": rows[0]["version"],
        "questions": n,
        "correct": sum(r["correct"] for r in rows),
        "searched": sum(r["searched"] for r in rows),
        "median_latency_ms": int(statistics.median(r["latency_ms"] for r in rows)),
        "median_output_tokens": int(statistics.median(r["output_tokens"] for r in rows)),
        "rows": rows,
    }


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    report = run(args.url)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)
    v = report["version"]
    print(f"{v['prompt_version']} ({v['revision']}): {report['correct']}/{report['questions']} correct, "
          f"{report['searched']}/{report['questions']} searched, median {report['median_latency_ms']} ms, "
          f"{report['median_output_tokens']} output tokens")
