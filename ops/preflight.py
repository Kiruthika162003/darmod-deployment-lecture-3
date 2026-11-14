"""Check every piece of the lecture 3 demo and say PASS, WARN or FAIL for each.

    python ops/preflight.py

Run it about half an hour before you start. It changes nothing, except that it asks
Maple one real question so the first question in the room is not a cold start.
"""

from __future__ import annotations

import json
import subprocess
import sys

import httpx

PROJECT = "devfest-demos-472013"
REGION = "northamerica-northeast2"
RESULTS: list[str] = []


def report(level: str, what: str, detail: str = "") -> None:
    RESULTS.append(level)
    print(f"{level:<5} {what}" + (f"  [{detail}]" if detail else ""))


def gcloud(*args: str) -> str:
    out = subprocess.run(["gcloud", *args, "--project", PROJECT], capture_output=True, text=True, shell=(sys.platform == "win32"))
    return out.stdout.strip()


def json_of(*args: str):
    text = gcloud(*args, "--format=json")
    return json.loads(text) if text else None


def main() -> None:
    service = json_of("run", "services", "describe", "maple", "--region", REGION)
    traffic = {t.get("tag"): t for t in service["status"]["traffic"]}
    champion, challenger = traffic.get("champion"), traffic.get("challenger")

    if champion and champion.get("percent") == 100:
        report("PASS", "all traffic is on champion", champion["revisionName"])
    else:
        report("FAIL", "traffic is not 100% on champion", json.dumps([(t.get("tag"), t.get("percent")) for t in service["status"]["traffic"]]))

    for name, entry, expected in (("champion", champion, "v2.3"), ("challenger", challenger, "v2.4")):
        if not entry:
            report("FAIL", f"no revision is tagged {name}")
            continue
        try:
            body = httpx.get(entry["url"] + "/version", timeout=60).json()
            ok = body["prompt_version"] == expected
            report("PASS" if ok else "FAIL", f"{name} answers as prompt {expected}", f"{body['prompt_version']} on {body['revision']}, commit {body['commit']}")
        except Exception as exc:  # noqa: BLE001
            report("FAIL", f"{name} did not answer /version", type(exc).__name__)

    scaling = service["spec"]["template"]["metadata"].get("annotations", {})
    report("PASS" if scaling.get("autoscaling.knative.dev/maxScale") == "3" else "WARN", "at most 3 copies", f"maxScale {scaling.get('autoscaling.knative.dev/maxScale')}")

    if gcloud("run", "services", "describe", "maple-guard", "--region", REGION, "--format=value(status.url)"):
        report("PASS", "maple-guard is deployed")
    else:
        report("FAIL", "maple-guard is not deployed")
    sub = gcloud("pubsub", "subscriptions", "describe", "maple-guard-push", "--format=value(pushConfig.pushEndpoint)")
    report("PASS" if sub else "FAIL", "Pub/Sub pushes alerts to maple-guard", sub)

    metrics = set(gcloud("logging", "metrics", "list", "--format=value(name)").split())
    missing = {"maple_turns", "maple_searched", "maple_not_searched", "maple_output_tokens"} - metrics
    report("PASS" if not missing else "FAIL", "the four log-based metrics exist", ", ".join(sorted(missing)))

    policies = json_of("alpha", "monitoring", "policies", "list", "--filter=displayName='Maple stopped searching before answering'") or []
    if policies and policies[0].get("enabled"):
        n = len(policies[0].get("notificationChannels", []))
        report("PASS", "the ML-layer alert is on", f"{n} notification channel(s)")
        report("PASS" if n >= 2 else "WARN", "an email channel is attached (the alert that reaches your phone)", "see demo.txt 8.6" if n < 2 else "")
    else:
        report("FAIL", "the ML-layer alert is missing or off")
    burn = json_of("alpha", "monitoring", "policies", "list", "--filter=displayName='Maple is burning its error budget'") or []
    report("PASS" if burn and burn[0].get("enabled") else "WARN", "the SLO burn-rate alert is on")

    dash = gcloud("monitoring", "dashboards", "list", "--filter=displayName='Maple: three layers'", "--format=value(name)")
    report("PASS" if dash else "FAIL", "the dashboard 'Maple: three layers' exists")
    uptime = gcloud("monitoring", "uptime", "list-configs", "--filter=displayName='Maple answers /version'", "--format=value(name)")
    report("PASS" if uptime else "WARN", "the uptime check exists")

    rows = subprocess.run(
        ["bq", "query", "--use_legacy_sql=false", "--format=csv", f"--project_id={PROJECT}", f"SELECT COUNT(*) AS n FROM `{PROJECT}.maple_ops.turns`"],
        capture_output=True, text=True, shell=(sys.platform == "win32"),
    )
    n = rows.stdout.strip().splitlines()[-1] if rows.returncode == 0 and rows.stdout.strip() else ""
    report("PASS" if n.isdigit() and int(n) > 0 else "WARN", "BigQuery has Maple's answers (the Looker Studio source)", f"{n or 'view not there yet'} rows")

    try:
        reply = httpx.post("https://maple-108279147534.northamerica-northeast2.run.app/chat", json={"prompt": "What is the DARMOD program at Vector Institute?"}, timeout=120).json()
        report("PASS" if "search_agent" in reply["used_tools"] else "WARN", "a real question gets a searched answer", f"{reply['latency_ms']} ms, {reply['version']['prompt_version']}")
    except Exception as exc:  # noqa: BLE001
        report("FAIL", "the live Maple link did not answer", type(exc).__name__)

    bad = RESULTS.count("FAIL")
    print(f"\n{RESULTS.count('PASS')} pass, {RESULTS.count('WARN')} warn, {bad} fail")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
