"""The lecture's command line (ops/demo.py): the traffic it sets and the room it simulates."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="module")
def demo():
    spec = importlib.util.spec_from_file_location("demo_cli", ROOT / "ops" / "demo.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _service(*rows):
    return {"trafficStatuses": [
        {"type": "TRAFFIC_TARGET_ALLOCATION_TYPE_REVISION", "revision": rev, "percent": pct, **({"tag": tag} if tag else {})}
        for rev, pct, tag in rows
    ]}


def test_split_names_each_revision_by_its_tag(demo):
    svc = _service(("maple-00020", 100, "champion"), ("maple-00022", 0, "challenger"))
    assert demo.split(svc) == {"champion": 100, "challenger": 0}
    assert demo.say_traffic(svc) == "champion 100% (00020)"


def test_a_canary_split_is_described_for_both_tags(demo):
    svc = _service(("maple-00020", 90, "champion"), ("maple-00022", 10, "challenger"))
    assert demo.say_traffic(svc) == "champion 90% (00020), challenger 10% (00022)"


def test_release_sends_everything_to_the_challenger_and_keeps_the_tags(demo, monkeypatch):
    sent = {}
    svc = _service(("maple-00020", 100, "champion"), ("maple-00022", 0, "challenger"))
    monkeypatch.setattr(demo, "service", lambda: svc)
    monkeypatch.setattr(demo, "api", lambda method, url, **kw: sent.update(method=method, url=url, body=kw["json"]))
    demo.set_traffic({"challenger": 100, "champion": 0})
    assert sent["method"] == "PATCH" and sent["url"].endswith("?updateMask=traffic")
    by_tag = {t["tag"]: t for t in sent["body"]["traffic"]}
    assert by_tag["challenger"]["percent"] == 100 and by_tag["challenger"]["revision"] == "maple-00022"
    assert by_tag["champion"]["percent"] == 0


def test_a_missing_tag_stops_the_command_instead_of_guessing(demo, monkeypatch):
    monkeypatch.setattr(demo, "service", lambda: _service(("maple-00020", 100, "champion")))
    with pytest.raises(SystemExit, match="challenger"):
        demo.set_traffic({"challenger": 100, "champion": 0})


def test_the_simulated_room_asks_mostly_factual_questions(demo):
    factual = [q for q in demo.QUESTIONS if len(q.split()) > 3]
    assert len(demo.QUESTIONS) >= 30
    assert len(factual) / len(demo.QUESTIONS) > 0.85  # chit-chat would not search, and would blur the alert


def test_door_only_accepts_open_or_close(demo):
    with pytest.raises(SystemExit):
        demo.cmd_door("ajar")
    with pytest.raises(SystemExit):
        demo.cmd_scale("huge")
