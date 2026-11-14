"""What Maple tells the outside world about each turn.

One JSON line per chat turn goes to stdout. Cloud Run sends stdout to Cloud
Logging, and from there it feeds the log-based metrics, the BigQuery table
behind the Looker Studio page, and the lineage query. The field names here are
the contract those pieces filter on, so change them in one place only.
"""

from __future__ import annotations

import os

from .config import get_settings
from .logging_setup import get_logger

log = get_logger("maple.telemetry")

# Cloud Run sets K_REVISION to the revision serving the request.
_UNKNOWN_REVISION = "local"


def revision() -> str:
    return os.environ.get("K_REVISION", _UNKNOWN_REVISION)


def version_info() -> dict[str, str]:
    """Which build answered: shown next to every reply and in /healthz."""
    s = get_settings()
    return {
        "prompt_version": s.prompt_version,
        "revision": revision(),
        "commit": s.git_commit,
        "model": s.chat_model,
    }


def is_grounded(used_tools: list[str]) -> bool:
    """A turn is grounded when Maple searched before she answered."""
    return "search_agent" in used_tools


def log_turn(
    *,
    session_id: str,
    latency_ms: int,
    used_tools: list[str],
    usage: dict[str, int],
    answer_chars: int,
    cold_start: bool,
    search_allowed: bool,
) -> None:
    """The one line the whole monitoring story reads."""
    log.info(
        "maple.turn",
        session_id=session_id,
        latency_ms=latency_ms,
        grounded=is_grounded(used_tools),
        search_allowed=search_allowed,
        used_tools=used_tools,
        input_tokens=usage.get("input_tokens", 0),
        output_tokens=usage.get("output_tokens", 0),
        answer_chars=answer_chars,
        cold_start=cold_start,
        **version_info(),
    )
