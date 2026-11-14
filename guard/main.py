"""maple-guard: the part that rolls Maple back when the alert says so.

Flow: Cloud Monitoring raises an alert, publishes it to Pub/Sub, Pub/Sub pushes
it here. If the alert is open and Maple is not already serving the revision
tagged `champion`, this moves all traffic back to it and writes one ledger line
to the log. It never deploys anything and never deletes a revision.

The decision lives in `plan_rollback`, a plain function, so it can be tested
without Google Cloud. The rest is plumbing.
"""

from __future__ import annotations

import base64
import json
import os
from typing import Any

import google.auth
import httpx
from fastapi import FastAPI, Request
from google.auth.transport.requests import Request as AuthRequest

PROJECT = os.environ["PROJECT_ID"]
REGION = os.environ.get("REGION", "northamerica-northeast2")
SERVICE = os.environ.get("TARGET_SERVICE", "maple")
POLICY = os.environ.get("POLICY_NAME", "Maple stopped searching before answering")

_BY_REVISION = "TRAFFIC_TARGET_ALLOCATION_TYPE_REVISION"
_API = f"https://run.googleapis.com/v2/projects/{PROJECT}/locations/{REGION}/services/{SERVICE}"

app = FastAPI(title="maple-guard")


def ledger(event: str, **fields: Any) -> None:
    """One JSON line per action. Cloud Logging picks up severity and message."""
    print(json.dumps({"severity": "NOTICE", "message": event, "event": event, **fields}), flush=True)


def tagged_revisions(service: dict[str, Any]) -> dict[str, str]:
    """tag -> revision name, from what Cloud Run reports it is serving now."""
    return {
        t["tag"]: t["revision"]
        for t in service.get("trafficStatuses", [])
        if t.get("tag") and t.get("revision")
    }


def plan_rollback(service: dict[str, Any]) -> dict[str, Any] | None:
    """The traffic setting that puts champion back at 100%, or None if there is
    nothing to do. Tags stay in place so the challenger can still be reached."""
    tags = tagged_revisions(service)
    champion = tags.get("champion")
    if champion is None:
        raise ValueError("no revision is tagged 'champion', so there is nothing safe to roll back to")

    served = {
        t["revision"]: t.get("percent", 0)
        for t in service.get("trafficStatuses", [])
        if t.get("revision") and t.get("percent", 0) > 0
    }
    if served == {champion: 100}:
        return None

    traffic = [{"type": _BY_REVISION, "revision": champion, "percent": 100, "tag": "champion"}]
    for tag, revision in sorted(tags.items()):
        if tag != "champion":
            traffic.append({"type": _BY_REVISION, "revision": revision, "percent": 0, "tag": tag})
    return {"traffic": traffic}


def _headers() -> dict[str, str]:
    creds, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
    creds.refresh(AuthRequest())
    return {"Authorization": f"Bearer {creds.token}"}


def incident_from(push: dict[str, Any]) -> dict[str, Any]:
    """The alert, out of the Pub/Sub push envelope."""
    data = push["message"]["data"]
    return json.loads(base64.b64decode(data))["incident"]


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"ok": "true", "watching": SERVICE}


@app.post("/alert")
async def alert(request: Request) -> dict[str, Any]:
    incident = incident_from(await request.json())
    state = incident.get("state")
    name = incident.get("policy_name", "")
    if state != "open" or name != POLICY:
        ledger("guard.ignored", state=state, policy=name)
        return {"action": "ignored"}

    async with httpx.AsyncClient(timeout=30, headers=_headers()) as client:
        current = (await client.get(_API)).raise_for_status().json()
        plan = plan_rollback(current)
        if plan is None:
            ledger("guard.noop", incident=incident.get("incident_id"), reason="champion already serves all traffic")
            return {"action": "noop"}
        before = {t["revision"]: t.get("percent", 0) for t in current.get("trafficStatuses", []) if t.get("revision")}
        (await client.patch(_API, params={"updateMask": "traffic"}, json=plan)).raise_for_status()

    ledger(
        "guard.rollback",
        incident=incident.get("incident_id"),
        started_at=incident.get("started_at"),
        service=SERVICE,
        was=before,
        now={t["revision"]: t["percent"] for t in plan["traffic"] if t["percent"]},
    )
    return {"action": "rolled_back"}
