"""Shared subagent identification helpers used by Copilot adapters.

Hoisted out of `copilot_debug_adapter` so both OTel and debug-log adapters
agree on what counts as a real subagent reference.
"""
from __future__ import annotations

import re

_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)

# Copilot tool call ids look like `call_<22+ alnum>` (OpenAI-style).
_CALL_ID_RE = re.compile(r"^call_[A-Za-z0-9]{16,}$")


def looks_like_uuid(value: str) -> bool:
    return bool(_UUID_RE.match(value))


def looks_like_call_id(value: str) -> bool:
    return bool(_CALL_ID_RE.match(value))


def is_real_subagent_id(value: str | None) -> bool:
    """True when the value resembles a Copilot subagent identifier.

    Accepts both UUIDs (parent/child session ids) and `call_xxx` tool-call
    ids that Copilot uses as the child sid for runSubagent invocations.
    """
    if not value:
        return False
    return looks_like_uuid(value) or looks_like_call_id(value)


def synthetic_subagent_span_id(call_id: str) -> str:
    """Stable span_id for cross-adapter SubagentStart/End dedup."""
    return f"subagent-{call_id}"
