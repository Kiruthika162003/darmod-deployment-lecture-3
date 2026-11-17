"""Every command you type during the lecture, in one place.

    python ops/demo.py <command>

It talks to Google Cloud's APIs directly with your own login, so there is no quoting to get
wrong in PowerShell. Run it with no command to see the list.

  check      Is everything ready? (PASS or FAIL for each piece)
  room       A simulated room of visitors asking Maple questions, at human speed
  logs       The last few answers, as Maple logged them
  traffic    Which revision is serving, and how much
  release    Send all traffic to the challenger (v2.4, the bad release)
  watch      Count the seconds until the alert fires and maple-guard moves traffic back
  guard      What maple-guard did, from its own log
  canary     Give the challenger 10% of traffic
  reset      Put all traffic back on champion
  lineage    Trace answers to their prompt, revision and commit (BigQuery)
  paperwork  Write the postmortem, the model card and the system card
  door       door close | door open: stop or allow public visitors
  scale      scale normal | one | quiet: how many copies Maple may run
  alerts     alerts pause | resume: Maple's two alert policies
"""

from __future__ import annotations

import asyncio
import json
import random
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import google.auth
import httpx
from google.auth.transport.requests import Request

PROJECT = "devfest-demos-472013"
REGION = "northamerica-northeast2"
SERVICE = "maple"
URL = "https://maple-108279147534.northamerica-northeast2.run.app"
ROOT = Path(__file__).resolve().parent.parent
WIN = sys.platform == "win32"
BYREV = "TRAFFIC_TARGET_ALLOCATION_TYPE_REVISION"
RUN = f"https://run.googleapis.com/v2/projects/{PROJECT}/locations/{REGION}/services/{SERVICE}"


def token() -> str:
    creds, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
    creds.refresh(Request())
    return creds.token


def api(method: str, url: str, **kw) -> dict:
    headers = {"Authorization": f"Bearer {token()}", "x-goog-user-project": PROJECT}  # bill the quota to this project, not gcloud's
    r = httpx.request(method, url, headers=headers, timeout=60, **kw)
    if r.status_code >= 300:
        raise SystemExit(f"{method} {url.split('?')[0]} -> {r.status_code}: {r.text[:300]}")
    return r.json() if r.text else {}


def gcloud(*args: str, check: bool = True) -> str:
    out = subprocess.run(["gcloud", *args], capture_output=True, text=True, shell=WIN)
    if check and out.returncode != 0:
        raise SystemExit(out.stderr.strip() or "gcloud failed")
    return out.stdout.strip()


# --- traffic ------------------------------------------------------------------

def service() -> dict:
    return api("GET", RUN)


def tags(svc: dict) -> dict[str, str]:
    return {t["tag"]: t["revision"] for t in svc.get("trafficStatuses", []) if t.get("tag") and t.get("revision")}


def split(svc: dict) -> dict[str, int]:
    names = {v: k for k, v in tags(svc).items()}
    return {names.get(t["revision"], t["revision"]): t.get("percent", 0) for t in svc.get("trafficStatuses", []) if t.get("revision")}


def say_traffic(svc: dict) -> str:
    t = tags(svc)
    return ", ".join(f"{name} {pct}% ({t.get(name, '?').replace('maple-', '')})" for name, pct in split(svc).items() if pct) or "nothing"


def set_traffic(percent: dict[str, int]) -> None:
    svc = service()
    t = tags(svc)
    missing = set(percent) - set(t)
    if missing or "champion" not in t:
        raise SystemExit(f"no revision is tagged {sorted(missing or {'champion'})}")
    body = {"traffic": [{"type": BYREV, "revision": t[tag], "percent": pct, "tag": tag} for tag, pct in percent.items()]}
    for tag, rev in t.items():
        if tag not in percent:
            body["traffic"].append({"type": BYREV, "revision": rev, "percent": 0, "tag": tag})
    api("PATCH", RUN + "?updateMask=traffic", json=body)


def now() -> str:
    return datetime.now(timezone.utc).strftime("%H:%M:%S")


# --- logs ----------------------------------------------------------------------

def entries(filter_: str, n: int) -> list[dict]:
    body = {"resourceNames": [f"projects/{PROJECT}"], "filter": filter_, "orderBy": "timestamp desc", "pageSize": n}
    return api("POST", "https://logging.googleapis.com/v2/entries:list", json=body).get("entries", [])


def turns_since(minutes: int) -> list[dict]:
    since = (datetime.now(timezone.utc) - timedelta(minutes=minutes)).strftime("%Y-%m-%dT%H:%M:%SZ")
    f = f'resource.type="cloud_run_revision" AND jsonPayload.event="maple.turn" AND timestamp>="{since}"'
    return [e["jsonPayload"] for e in entries(f, 500)]


# --- commands ------------------------------------------------------------------

def cmd_check() -> None:
    raise SystemExit(subprocess.call([sys.executable, str(ROOT / "ops" / "preflight.py")]))


def cmd_traffic() -> None:
    print("serving:", say_traffic(service()))


def cmd_logs() -> None:
    rows = turns_since(30)[:10]
    if not rows:
        print("No answers in the last 30 minutes. Run: python ops/demo.py room")
        return
    print(f"{'prompt':<7}{'revision':<18}{'searched':<10}{'latency':<10}{'tokens'}")
    for p in rows:
        print(f"{p['prompt_version']:<7}{p['revision']:<18}{str(p['grounded']):<10}{str(p['latency_ms']) + ' ms':<10}{p['output_tokens']}")


def cmd_release(watch: bool = False) -> None:
    print(f"{now()} UTC  release: all traffic to the challenger")
    set_traffic({"challenger": 100, "champion": 0})
    print("serving:", say_traffic(service()))
    if watch:
        cmd_watch()


def cmd_watch() -> None:
    start = time.time()
    print("Counting until the alert fires and maple-guard moves traffic back. Ctrl+C to stop.\n")
    while time.time() - start < 15 * 60:
        svc = service()
        s = split(svc)
        recent = turns_since(3)
        skipped = sum(1 for p in recent if not p["grounded"] and p["search_allowed"])
        total = sum(1 for p in recent if p["search_allowed"])
        mins, secs = divmod(int(time.time() - start), 60)
        print(f"+{mins}:{secs:02d}   serving {say_traffic(svc):<45} last 3 min: {total} answers, {skipped} skipped the search")
        if s.get("champion") == 100 and time.time() - start > 15:
            print(f"\nBack on champion after {mins}:{secs:02d}. Nobody typed anything.")
            return
        time.sleep(10)
    print("Still not back after 15 minutes. Is the room asking questions? Try: python ops/demo.py reset")


def cmd_guard() -> None:
    f = 'resource.type="cloud_run_revision" AND resource.labels.service_name="maple-guard" AND jsonPayload.event="guard.rollback"'
    found = entries(f, 3)
    if not found:
        print("maple-guard has not rolled anything back yet.")
    for e in found:
        p = e["jsonPayload"]
        opened = datetime.fromtimestamp(p["started_at"], timezone.utc).strftime("%H:%M:%S") if p.get("started_at") else "?"
        print(f"{e['timestamp'][:19]} UTC  alert opened {opened}, then moved traffic from {p['was']} to {p['now']}")


def cmd_canary() -> None:
    set_traffic({"champion": 90, "challenger": 10})
    print("serving:", say_traffic(service()))


def cmd_reset() -> None:
    set_traffic({"champion": 100, "challenger": 0})
    print("serving:", say_traffic(service()))


def bq(sql: str) -> list[list[str]]:
    r = api("POST", f"https://bigquery.googleapis.com/bigquery/v2/projects/{PROJECT}/queries", json={"query": sql, "useLegacySql": False, "location": REGION})
    cols = [f["name"] for f in r["schema"]["fields"]]
    return [cols] + [[("" if c["v"] is None else str(c["v"])) for c in row["f"]] for row in r.get("rows", [])]


def show(rows: list[list[str]]) -> None:
    widths = [max(len(r[i]) for r in rows) + 2 for i in range(len(rows[0]))]
    for r in rows:
        print("".join(c.ljust(w) for c, w in zip(r, widths)))


def cmd_lineage() -> None:
    sql = (ROOT / "ops" / "lineage.sql").read_text(encoding="utf-8").replace("PROJECT_ID", PROJECT)
    for query in [q.strip() for q in sql.split(";") if "SELECT" in q]:
        body = "\n".join(line for line in query.splitlines() if not line.strip().startswith("--"))
        show(bq(body))
        print()


def cmd_paperwork() -> None:
    py = sys.executable
    run = lambda *a: subprocess.run([py, *a], cwd=ROOT, check=True)  # noqa: E731
    run("ops/postmortem.py", "--project", PROJECT, "--freshness", "3h")
    t = tags(service())
    run("ops/eval_maple.py", "--url", f"https://champion---{SERVICE}-xniespgdxa-pd.a.run.app", "--out", "ops/eval_champion.json")
    run("ops/eval_maple.py", "--url", f"https://challenger---{SERVICE}-xniespgdxa-pd.a.run.app", "--out", "ops/eval_challenger.json")
    run("ops/make_cards.py", "--champion", "ops/eval_champion.json", "--challenger", "ops/eval_challenger.json")
    print("\nOpen: postmortems\\ (newest file), docs\\MODEL_CARD.md, docs\\SYSTEM_CARD.md", "| tagged:", sorted(t))


def cmd_door(action: str) -> None:
    # Maple was deployed with its invoker check switched off, which is what makes it public.
    # Switching the check back on closes it (visitors get a 403); switching it off reopens it.
    flag = {"close": "--invoker-iam-check", "open": "--no-invoker-iam-check"}.get(action)
    if not flag:
        raise SystemExit("door close | door open")
    gcloud("run", "services", "update", SERVICE, flag, "--region", REGION, "--project", PROJECT, "--quiet")
    print("Maple is now", "CLOSED to the public (403 within about 15 seconds)." if action == "close" else "OPEN to the public.")


def cmd_scale(mode: str) -> None:
    low, high = {"normal": (1, 3), "one": (1, 1), "quiet": (0, 3)}.get(mode, (None, None))
    if low is None:
        raise SystemExit("scale normal | one | quiet")
    base = ["run", "services", "update", SERVICE, "--region", REGION, "--project", PROJECT, "--quiet", "--max-instances", str(high)]
    print("Rebuilding the two revisions with new limits. About a minute.")
    gcloud(*base, "--min-instances", str(low), "--update-env-vars", "PROMPT_VERSION=v2.3", "--tag", "champion", "--no-traffic")
    set_traffic({"champion": 100})
    gcloud(*base, "--min-instances", "0", "--update-env-vars", "PROMPT_VERSION=v2.4", "--tag", "challenger", "--no-traffic")
    print(f"Maple may now run {low} to {high} copies.", "serving:", say_traffic(service()))


def cmd_alerts(action: str) -> None:
    if action not in ("pause", "resume"):
        raise SystemExit("alerts pause | alerts resume")
    wanted = {"Maple stopped searching before answering", "Maple is burning its error budget"}
    for p in json.loads(gcloud("alpha", "monitoring", "policies", "list", "--format=json", "--project", PROJECT)):
        if p["displayName"] in wanted:
            gcloud("alpha", "monitoring", "policies", "update", p["name"], "--enabled" if action == "resume" else "--no-enabled", "--project", PROJECT)
            print(("resumed: " if action == "resume" else "paused: ") + p["displayName"])


# --- the simulated room -----------------------------------------------------------

QUESTIONS = [
    "What is the DARMOD program at Vector Institute?", "Where is Vector Institute located?",
    "What is FastLane at Vector Institute?", "Tell me about the Vector Scholarship in AI.",
    "When was Vector Institute founded?", "What does NextAI do?", "What events does Vector Institute host?",
    "How can a company work with Vector Institute?", "Who is the CEO of Vector Institute?",
    "What is the MaRS Discovery District?", "Who is the chief scientific advisor at Vector Institute?",
    "Is Vector Institute a university?", "What research areas does Vector Institute work on?",
    "How do I apply for a Vector scholarship?", "What is the difference between FastLane and DARMOD?",
    "Which Canadian cities have AI institutes?", "What is Mila and how is it related to Vector?",
    "What is Amii in Edmonton?", "Who founded Vector Institute?", "What is the Pan-Canadian AI Strategy?",
    "How many AI startups are in Toronto?", "Can students join Vector programs?",
    "What is a transformer model, in two sentences?", "What is the difference between machine learning and deep learning?",
    "Why do language models make things up?", "What is a vector database?",
    "What does Vector Institute offer for healthcare AI?", "Where can I find Vector Institute's open-source work?",
    "What is the Vector Institute's mission?", "Does Vector Institute offer jobs or internships?",
    "Tell me something surprising about AI in Canada.", "What is Geoffrey Hinton known for?",
    "hello", "what can you do?", "thanks!",
]


async def person(i: int, deadline: float, stats: dict) -> None:
    await asyncio.sleep(random.uniform(0, 25))
    client = httpx.AsyncClient(timeout=120)
    try:
        while time.time() < deadline:
            try:
                r = await client.post(f"{URL}/chat", json={"prompt": random.choice(QUESTIONS)})
                body = r.json()
                if "limit for one session" in body.get("reply", ""):  # a new visitor arrives
                    await client.aclose()
                    client = httpx.AsyncClient(timeout=120)
                elif r.status_code == 200:
                    stats["answers"] += 1
                    stats["searched"] += "search_agent" in body["used_tools"]
                    stats["versions"][body["version"]["prompt_version"]] = stats["versions"].get(body["version"]["prompt_version"], 0) + 1
                else:
                    stats["busy"] += 1
            except Exception:  # noqa: BLE001
                stats["errors"] += 1
            await asyncio.sleep(random.uniform(15, 40))
    finally:
        await client.aclose()


async def room(people: int, minutes: float) -> None:
    deadline = time.time() + minutes * 60
    stats = {"answers": 0, "searched": 0, "busy": 0, "errors": 0, "versions": {}}
    tasks = [asyncio.create_task(person(i, deadline, stats)) for i in range(people)]
    print(f"{people} visitors asking Maple, one question every 15 to 40 seconds each, for {minutes:g} minutes. Ctrl+C to stop.\n")
    while time.time() < deadline and not all(t.done() for t in tasks):
        await asyncio.sleep(15)
        a = stats["answers"]
        pct = round(100 * stats["searched"] / a) if a else 0
        print(f"{now()} UTC  {a} answers, {pct}% searched first, by prompt {stats['versions']}, {stats['busy']} busy, {stats['errors']} errors")
    await asyncio.gather(*tasks, return_exceptions=True)
    print("The room has left.")


def cmd_room(args: list[str]) -> None:
    people = int(args[0]) if args else 12
    minutes = float(args[1]) if len(args) > 1 else 10
    try:
        asyncio.run(room(people, minutes))
    except KeyboardInterrupt:
        print("\nThe room has left.")


def main() -> None:
    if len(sys.argv) < 2 or sys.argv[1] in ("-h", "--help", "help"):
        print(__doc__)
        return
    c, rest = sys.argv[1], sys.argv[2:]
    simple = {"check": cmd_check, "traffic": cmd_traffic, "logs": cmd_logs, "watch": cmd_watch, "guard": cmd_guard,
              "canary": cmd_canary, "reset": cmd_reset, "lineage": cmd_lineage, "paperwork": cmd_paperwork}
    if c in simple:
        simple[c]()
    elif c == "release":
        cmd_release(watch="--watch" in rest)
    elif c == "room":
        cmd_room(rest)
    elif c == "door":
        cmd_door(rest[0] if rest else "")
    elif c == "scale":
        cmd_scale(rest[0] if rest else "")
    elif c == "alerts":
        cmd_alerts(rest[0] if rest else "")
    else:
        raise SystemExit(f"unknown command {c!r}. Run it with no command to see the list.")


if __name__ == "__main__":
    main()
