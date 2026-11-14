"""What lecture 3 adds to Maple: versions, one log line per turn, and the guard.

No Google calls: tests/fake_model.py stands in for Gemini, and the guard's
decision is a plain function, so it is tested without Cloud Run.
"""

from __future__ import annotations

import base64
import importlib.util
import json
import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def fake_agent(monkeypatch):
    from backend import config, runner

    import tests.fake_model  # noqa: F401  (registers the model)

    monkeypatch.setattr(config.get_settings(), "chat_model", "fake-maple")
    monkeypatch.setattr(runner, "_RUNNER", None)
    monkeypatch.setattr(runner, "_AGENT", None)
    monkeypatch.setattr(runner, "_TURNS", {})


@pytest.fixture
def client(fake_agent):
    from backend.main import app

    with TestClient(app) as c:
        c.get("/me")
        yield c


class _Lines:
    """Stands in for the structured logger and keeps what was logged."""

    def __init__(self) -> None:
        self.lines: list[tuple[str, dict]] = []

    def info(self, event: str, **fields) -> None:
        self.lines.append((event, fields))

    def turns(self) -> list[dict]:
        return [f for e, f in self.lines if e == "maple.turn"]


@pytest.fixture
def lines(monkeypatch):
    from backend import telemetry

    captured = _Lines()
    monkeypatch.setattr(telemetry, "log", captured)
    return captured


# --- prompt versions -------------------------------------------------------

def test_the_champion_prompt_searches_and_the_challenger_does_not():
    from backend.agent import SYSTEM_PROMPT, system_prompt

    assert "run search_agent first" in system_prompt("v2.3")
    assert "never call search_agent" in system_prompt("v2.4")
    assert "run search_agent first" not in system_prompt("v2.4")
    assert SYSTEM_PROMPT == system_prompt("v2.3")


def test_both_versions_keep_the_store_instructions():
    from backend.agent import system_prompt

    for version in ("v2.3", "v2.4"):
        assert "submit_order" in system_prompt(version)


def test_an_unknown_prompt_version_fails_loudly():
    from backend.agent import system_prompt

    with pytest.raises(ValueError, match="v9.9"):
        system_prompt("v9.9")


# --- the turn log ----------------------------------------------------------

def test_a_turn_is_grounded_only_when_maple_searched():
    from backend.telemetry import is_grounded

    assert is_grounded(["search_agent"]) is True
    assert is_grounded([]) is False
    assert is_grounded(["submit_order"]) is False


def test_a_chat_turn_writes_one_line_with_the_fields_the_dashboards_read(client, lines, monkeypatch):
    from backend import config

    monkeypatch.setenv("K_REVISION", "maple-00007-abc")
    monkeypatch.setattr(config.get_settings(), "git_commit", "abc1234")
    client.post("/chat", json={"prompt": "What is DARMOD?"})
    (turn,) = lines.turns()
    assert turn["prompt_version"] == "v2.3"
    assert turn["revision"] == "maple-00007-abc"
    assert turn["commit"] == "abc1234"
    assert turn["grounded"] is False  # the scripted model does not call search
    assert turn["latency_ms"] >= 0
    assert turn["answer_chars"] > 0
    assert {"input_tokens", "output_tokens", "cold_start", "session_id", "model"} <= turn.keys()


def test_a_streamed_turn_is_logged_once_and_its_done_event_names_the_version(client, lines):
    response = client.post("/chat/stream", json={"prompt": "What is DARMOD?"})
    events = [json.loads(x[5:]) for x in response.text.splitlines() if x.startswith("data:")]
    assert len(lines.turns()) == 1
    assert lines.turns()[0]["answer_chars"] > 0
    assert events[-1]["type"] == "done"
    assert events[-1]["version"]["prompt_version"] == "v2.3"


def test_the_reply_says_which_build_answered(client):
    body = client.post("/chat", json={"prompt": "What is DARMOD?"}).json()
    assert body["version"]["prompt_version"] == "v2.3"
    assert body["version"]["revision"]


def test_search_off_is_recorded_so_it_is_not_mistaken_for_a_broken_release(client, lines):
    client.post("/chat", json={"prompt": "What is DARMOD?", "use_search": False})
    assert lines.turns()[0]["search_allowed"] is False


def test_the_synthetic_follow_up_after_an_order_decision_is_not_a_turn(fake_agent, lines):
    import asyncio

    from backend import runner

    asyncio.run(runner.run_turn("s1", "HUMAN_DENIED_TOOL_CALL: call_id=x", observed=False))
    assert lines.turns() == []


def test_healthz_names_the_prompt_version_and_commit(client, monkeypatch):
    from backend import config

    monkeypatch.setattr(config.get_settings(), "git_commit", "abc1234")
    body = client.get("/healthz").json()
    assert body["prompt_version"] == "v2.3"
    assert body["commit"] == "abc1234"


def test_version_endpoint_names_the_build(client, monkeypatch):
    monkeypatch.setenv("K_REVISION", "maple-00009-xyz")
    body = client.get("/version").json()
    assert body["prompt_version"] == "v2.3"
    assert body["revision"] == "maple-00009-xyz"
    assert {"commit", "model"} <= body.keys()


# --- the classroom guard ---------------------------------------------------

def test_a_session_stops_at_its_turn_limit(client, lines, monkeypatch):
    from backend import config

    monkeypatch.setattr(config.get_settings(), "max_turns_per_session", 2)
    replies = [client.post("/chat", json={"prompt": "What is DARMOD?"}).json()["reply"] for _ in range(3)]
    assert "limit for one session (2 questions)" in replies[2]
    assert "limit" not in replies[0] and "limit" not in replies[1]
    assert len(lines.turns()) == 2  # the refused turn is not a Maple answer


def test_the_limit_also_holds_on_the_stream(client, monkeypatch):
    from backend import config

    monkeypatch.setattr(config.get_settings(), "max_turns_per_session", 1)
    client.post("/chat/stream", json={"prompt": "What is DARMOD?"})
    second = client.post("/chat/stream", json={"prompt": "What is DARMOD?"})
    assert "limit for one session" in second.text


# --- Cloud Logging fields --------------------------------------------------

def test_cloud_logging_gets_severity_and_message():
    from backend.logging_setup import _for_cloud_logging

    out = _for_cloud_logging(None, "info", {"event": "maple.turn", "level": "warning"})
    assert out["severity"] == "WARNING"
    assert out["message"] == "maple.turn"
    assert out["event"] == "maple.turn"


# --- maple-guard -----------------------------------------------------------

@pytest.fixture(scope="module")
def guard():
    os.environ.setdefault("PROJECT_ID", "test-project")
    spec = importlib.util.spec_from_file_location("guard_main", ROOT / "guard" / "main.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _service(statuses: list[dict]) -> dict:
    return {"trafficStatuses": statuses}


def _status(revision: str, percent: int, tag: str | None = None) -> dict:
    out = {"type": "TRAFFIC_TARGET_ALLOCATION_TYPE_REVISION", "revision": revision, "percent": percent}
    if tag:
        out["tag"] = tag
    return out


def test_guard_sends_all_traffic_back_to_champion(guard):
    plan = guard.plan_rollback(
        _service([_status("maple-00002", 100, "challenger"), _status("maple-00001", 0, "champion")])
    )
    by_tag = {t["tag"]: t for t in plan["traffic"]}
    assert by_tag["champion"]["revision"] == "maple-00001"
    assert by_tag["champion"]["percent"] == 100
    assert by_tag["challenger"]["percent"] == 0  # still reachable, no longer serving


def test_guard_undoes_a_split_too(guard):
    plan = guard.plan_rollback(
        _service([_status("maple-00001", 90, "champion"), _status("maple-00002", 10, "challenger")])
    )
    assert plan["traffic"][0] == {
        "type": "TRAFFIC_TARGET_ALLOCATION_TYPE_REVISION",
        "revision": "maple-00001", "percent": 100, "tag": "champion",
    }


def test_guard_does_nothing_when_champion_already_serves_everyone(guard):
    assert guard.plan_rollback(_service([_status("maple-00001", 100, "champion")])) is None


def test_guard_refuses_to_guess_when_there_is_no_champion(guard):
    with pytest.raises(ValueError, match="champion"):
        guard.plan_rollback(_service([_status("maple-00002", 100, "challenger")]))


def test_guard_reads_the_alert_out_of_the_pubsub_push(guard):
    incident = {"incident_id": "i1", "state": "open", "policy_name": "Maple stopped searching before answering"}
    data = base64.b64encode(json.dumps({"version": "1.2", "incident": incident}).encode()).decode()
    assert guard.incident_from({"message": {"data": data}}) == incident
