"""Copilot debug-log JSONL → EventEnvelope adapter.

Watches a Copilot debug-logs directory (each subdirectory is a session,
each contains `main.jsonl` and optional child-session JSONL files).
Tails every `main.jsonl` and translates known record types into normalized
EventEnvelopes. Unknown records are ignored.

Record schema (samples observed):
  {"ts":..., "dur":0,    "sid":"<uuid>", "type":"session_start", ...}
  {"ts":..., "dur":0,    "sid":"<uuid>", "type":"user_message",  ...}
  {"ts":..., "dur":0,    "sid":"<uuid>", "type":"turn_start",    ...}
  {"ts":..., "dur":<ms>, "sid":"<uuid>", "type":"tool_call",
      "spanId":"<id>", "name":"<tool>", "attrs":{...}}
  {"ts":..., "dur":<ms>, "sid":"<uuid>", "type":"llm_request",
      "attrs":{"inputTokens":N, "outputTokens":M, ...}}
  {"ts":..., "dur":0,    "sid":"<uuid>", "type":"child_session_ref",
      "attrs":{"childSessionId":"<uuid>", "childLogFile":"...", "label":"..."}}
  {"ts":..., "dur":0,    "sid":"<uuid>", "type":"turn_end", ...}

Single-record `tool_call` events represent a completed call (start+end).
We emit `toolStart` immediately followed by `toolEnd` so the viewer briefly
shows the active tool.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from collections import OrderedDict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..protocol import domain_events as de
from ._subagent_filters import (
    is_real_subagent_id,
    looks_like_uuid as _looks_like_uuid,
    synthetic_subagent_span_id,
)
from .file_tailer import tail_lines

log = logging.getLogger(__name__)

PROVIDER_ID = "copilot"

# ── Subagent close registry ──────────────────────────────────
# Maps child sid (call_xxx or uuid) → metadata needed to emit a
# matching SubagentEnd on the parent when the child stream closes.
_CHILD_REGISTRY: "OrderedDict[str, dict[str, Any]]" = OrderedDict()
_REGISTRY_TTL_SECONDS = 3600.0
_REGISTRY_MAX_ENTRIES = 4096


def _registry_register(child_sid: str, payload: dict[str, Any]) -> None:
    payload = {**payload, "_ts": time.monotonic()}
    _CHILD_REGISTRY[child_sid] = payload
    _CHILD_REGISTRY.move_to_end(child_sid)
    while len(_CHILD_REGISTRY) > _REGISTRY_MAX_ENTRIES:
        _CHILD_REGISTRY.popitem(last=False)


def _registry_lookup(child_sid: str) -> dict[str, Any] | None:
    entry = _CHILD_REGISTRY.get(child_sid)
    if entry is None:
        return None
    if time.monotonic() - entry.get("_ts", 0.0) > _REGISTRY_TTL_SECONDS:
        _CHILD_REGISTRY.pop(child_sid, None)
        return None
    if entry.get("_closed"):
        return None
    return entry


def _registry_close(child_sid: str) -> None:
    entry = _CHILD_REGISTRY.get(child_sid)
    if entry is not None:
        entry["_closed"] = True


def _ts_to_dt(ts_ms: Any) -> datetime:
    try:
        return datetime.fromtimestamp(float(ts_ms) / 1000.0, tz=timezone.utc)
    except (TypeError, ValueError):
        return datetime.now(timezone.utc)


def normalize_record(rec: dict[str, Any]) -> list[de.EventEnvelope]:
    """Translate one debug-log record into 0+ envelopes."""
    sid = rec.get("sid")
    rtype = rec.get("type")
    if not sid or not rtype:
        return []
    ts = _ts_to_dt(rec.get("ts"))
    attrs: dict[str, Any] = rec.get("attrs") or {}
    span_id = rec.get("spanId")

    def _env(event: de.AgentEvent, *, when: datetime | None = None) -> de.EventEnvelope:
        return de.EventEnvelope(
            provider_id=PROVIDER_ID,
            session_id=str(sid),
            span_id=str(span_id) if span_id else None,
            timestamp=when or ts,
            event=event,
        )

    if rtype == "session_start":
        return [_env(de.SessionStart(source="copilot"))]

    if rtype == "user_message":
        return [_env(de.UserTurn())]

    if rtype == "turn_end":
        return [_env(de.TurnEnd())]

    if rtype == "tool_call":
        tool_id = str(rec.get("spanId") or f"tool-{rec.get('ts')}")
        tool_name = str(rec.get("name") or "tool")
        return [
            _env(de.ToolStart(tool_id=tool_id, tool_name=tool_name, input=attrs.get("input"))),
            _env(de.ToolEnd(tool_id=tool_id)),
        ]

    if rtype == "llm_request":
        in_tok = int(attrs.get("inputTokens") or 0)
        out_tok = int(attrs.get("outputTokens") or 0)
        if in_tok or out_tok:
            return [_env(de.TokenUsage(input_tokens=in_tok, output_tokens=out_tok))]
        return []

    if rtype == "child_session_ref":
        child_sid = attrs.get("childSessionId")
        if not child_sid:
            return []
        label = str(attrs.get("label") or "subagent")
        # Surface as a SubagentStart on the parent session. Only emit a
        # standalone child SessionStart for *real* subagent sessions backed
        # by their own debug log (Copilot writes attrs.childLogFile pointing
        # at the corresponding runSubagent-*.jsonl). API-side tool/use ids
        # like Anthropic's ``toolu_xxx`` arrive without childLogFile and
        # would otherwise pollute the viewer with phantom agents.
        has_child_log = bool(attrs.get("childLogFile"))
        parent_tool_id = str(rec.get("spanId") or f"child-{child_sid}")
        child_id_str = str(child_sid)
        synthetic_span = synthetic_subagent_span_id(child_id_str)
        # Register so the child stream's terminator can emit a matching
        # SubagentEnd on the parent session.
        if has_child_log or is_real_subagent_id(child_id_str):
            _registry_register(
                child_id_str,
                {
                    "parent_sid": str(sid),
                    "parent_tool_id": parent_tool_id,
                    "tool_id": f"child-{child_id_str}",
                    "label": label,
                    "synthetic_span_id": synthetic_span,
                },
            )
        envelopes: list[de.EventEnvelope] = [
            de.EventEnvelope(
                provider_id=PROVIDER_ID,
                session_id=str(sid),
                span_id=synthetic_span,
                timestamp=ts,
                event=de.SubagentStart(
                    parent_tool_id=parent_tool_id,
                    tool_id=f"child-{child_id_str}",
                    tool_name=label,
                ),
            )
        ]
        if has_child_log or _looks_like_uuid(child_id_str):
            envelopes.append(
                de.EventEnvelope(
                    provider_id=PROVIDER_ID,
                    session_id=child_id_str,
                    parent_agent_id=str(sid),
                    timestamp=ts,
                    event=de.SessionStart(source=f"copilot-child:{label}"),
                )
            )
        return envelopes

    if rtype == "subagent":
        # Terminator written at the *end* of a runSubagent-*.jsonl child
        # stream: ``{"sid":"call_xxx","type":"subagent","attrs":{"agentName":...},"dur":...}``.
        # ``sid`` here is the child sid; emit a SubagentEnd on the parent
        # session so the viewer can mark the child agent done.
        entry = _registry_lookup(str(sid))
        if entry is None:
            return []
        _registry_close(str(sid))
        return [
            de.EventEnvelope(
                provider_id=PROVIDER_ID,
                session_id=entry["parent_sid"],
                span_id=entry["synthetic_span_id"],
                timestamp=ts,
                event=de.SubagentEnd(
                    parent_tool_id=entry["parent_tool_id"],
                    tool_id=entry["tool_id"],
                ),
            )
        ]

    return []


async def _tail_file(
    path: Path,
    on_envelope,
    *,
    from_start: bool,
) -> None:
    log.info("debug-log: tailing %s", path)
    async for line in tail_lines(path, from_start=from_start):
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        for env in normalize_record(rec):
            await on_envelope(env)


async def run_debug_log_adapter(
    base_dir: Path,
    on_envelope,
    *,
    poll_seconds: float = 1.0,
    from_start: bool = False,
) -> None:
    """Watch ``base_dir`` for session subdirectories and tail their main.jsonl.

    ``on_envelope`` is an async callable taking one EventEnvelope.
    """
    if not base_dir.exists():
        log.warning("debug-log: base dir does not exist: %s", base_dir)
    tasks: dict[Path, asyncio.Task[None]] = {}
    try:
        while True:
            if base_dir.exists():
                for child in base_dir.iterdir():
                    if not child.is_dir():
                        continue
                    # Tail every JSONL produced for this Copilot session:
                    # main.jsonl plus any runSubagent-*.jsonl produced when
                    # a runSubagent tool fires (each child agent writes its
                    # own session_start/tool_call/turn_end stream there).
                    for jsonl in child.glob("*.jsonl"):
                        if jsonl in tasks and not tasks[jsonl].done():
                            continue
                        tasks[jsonl] = asyncio.create_task(
                            _tail_file(jsonl, on_envelope, from_start=from_start)
                        )
            await asyncio.sleep(poll_seconds)
    except asyncio.CancelledError:
        for t in tasks.values():
            t.cancel()
        raise
