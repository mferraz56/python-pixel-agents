"""Copilot OTel → EventEnvelope adapter.

Reads the OTel Collector's file exporter output (OTLP/JSON, one
``ResourceSpans`` document per line) and translates spans into normalized
EventEnvelopes. Designed to consume the file produced by the
``file/traces`` exporter in
``observability/otel-collector-config.yaml`` (default path inside the
collector container: ``/var/lib/otel/copilot-traces.jsonl``).

Copilot's exact OTel attribute taxonomy is still in flux, so the mapping
below is best-effort and conservative:

* session id is looked up from common attribute keys
* every span becomes a ``toolStart`` + ``toolEnd`` pair (``span.name`` as
  the tool label) when ``endTimeUnixNano`` is set
* a span whose name starts with ``llm`` and carries token attributes is
  also emitted as a ``tokenUsage`` event

This keeps the live view useful immediately without committing to a
brittle vendor-specific schema. A richer projector can be plugged in
later by replacing ``span_to_events``.
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..protocol import domain_events as de
from ._subagent_filters import synthetic_subagent_span_id
from .file_tailer import tail_lines

log = logging.getLogger(__name__)

PROVIDER_ID = "copilot"

_SESSION_ATTR_KEYS = (
    "copilot_chat.chat_session_id",
    "copilot_chat.session_id",
    "copilot.session.id",
    "session.id",
    "sid",
    "vscode.session.id",
)
_INPUT_TOK_KEYS = ("llm.usage.input_tokens", "gen_ai.usage.input_tokens", "inputTokens")
_OUTPUT_TOK_KEYS = ("llm.usage.output_tokens", "gen_ai.usage.output_tokens", "outputTokens")
_RUNSUBAGENT_SPAN_NAME = "execute_tool runSubagent"
_TOOL_NAME_KEYS = ("gen_ai.tool.name", "tool.name")
_TOOL_CALL_ID_KEYS = ("gen_ai.tool.call.id", "tool.call.id")
_TOOL_CALL_ARGS_KEYS = ("gen_ai.tool.call.arguments", "tool.call.arguments")


def _attr_value(v: dict[str, Any]) -> Any:
    """Unwrap an OTLP AnyValue dict to a Python primitive."""
    if not isinstance(v, dict):
        return v
    for key in ("stringValue", "intValue", "boolValue", "doubleValue"):
        if key in v:
            val = v[key]
            return int(val) if key == "intValue" and isinstance(val, str) else val
    if "arrayValue" in v:
        return [_attr_value(x) for x in (v["arrayValue"].get("values") or [])]
    return None


def _attrs_to_dict(attrs: list[dict[str, Any]] | None) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for a in attrs or []:
        k = a.get("key")
        if not k:
            continue
        out[k] = _attr_value(a.get("value", {}))
    return out


def _ns_to_dt(ns: Any) -> datetime:
    try:
        return datetime.fromtimestamp(int(ns) / 1_000_000_000, tz=timezone.utc)
    except (TypeError, ValueError):
        return datetime.now(timezone.utc)


def span_to_events(span: dict[str, Any], resource_attrs: dict[str, Any]) -> list[de.EventEnvelope]:
    end_ns = span.get("endTimeUnixNano")
    if not end_ns:
        return []  # in-flight; collector batches usually only flush completed spans
    span_attrs = _attrs_to_dict(span.get("attributes"))
    merged = {**resource_attrs, **span_attrs}

    sid: str | None = None
    for k in _SESSION_ATTR_KEYS:
        if k in merged and merged[k]:
            sid = str(merged[k])
            break
    if not sid:
        # fall back to traceId so we still see something
        sid = str(span.get("traceId") or "otel-unknown")

    name = str(span.get("name") or "span")
    span_id = str(span.get("spanId") or f"otel-{end_ns}")
    parent_span_id = span.get("parentSpanId") or None
    start_ts = _ns_to_dt(span.get("startTimeUnixNano") or end_ns)
    end_ts = _ns_to_dt(end_ns)

    # ── runSubagent detection ─────────────────────────────────
    # Copilot emits these as ``execute_tool runSubagent`` with the
    # tool-call id (``call_xxx``) under ``gen_ai.tool.call.id``. We surface
    # them as Subagent Start/End on the parent so the viewer can render
    # the child agent. We *replace* the regular Tool Start/End pair to
    # avoid double rendering the same span as both a tool and a subagent.
    tool_name_attr = next(
        (str(merged[k]) for k in _TOOL_NAME_KEYS if merged.get(k)),
        "",
    )
    is_run_subagent = name == _RUNSUBAGENT_SPAN_NAME or tool_name_attr == "runSubagent"
    if is_run_subagent:
        call_id = next(
            (str(merged[k]) for k in _TOOL_CALL_ID_KEYS if merged.get(k)),
            "",
        )
        if not call_id:
            call_id = span_id  # last-resort fallback
        agent_label = "subagent"
        for k in _TOOL_CALL_ARGS_KEYS:
            raw = merged.get(k)
            if not raw:
                continue
            try:
                parsed = json.loads(raw) if isinstance(raw, str) else raw
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, dict) and parsed.get("agentName"):
                agent_label = str(parsed["agentName"])
                break
        synthetic_span = synthetic_subagent_span_id(call_id)
        parent_tool_id = str(parent_span_id) if parent_span_id else span_id
        tool_id = f"child-{call_id}"
        return [
            de.EventEnvelope(
                provider_id=PROVIDER_ID,
                session_id=sid,
                span_id=synthetic_span,
                parent_span_id=str(parent_span_id) if parent_span_id else None,
                timestamp=start_ts,
                event=de.SubagentStart(
                    parent_tool_id=parent_tool_id,
                    tool_id=tool_id,
                    tool_name=agent_label,
                ),
            ),
            de.EventEnvelope(
                provider_id=PROVIDER_ID,
                session_id=sid,
                span_id=synthetic_span,
                parent_span_id=str(parent_span_id) if parent_span_id else None,
                timestamp=end_ts,
                event=de.SubagentEnd(
                    parent_tool_id=parent_tool_id,
                    tool_id=tool_id,
                ),
            ),
        ]

    out: list[de.EventEnvelope] = [
        de.EventEnvelope(
            provider_id=PROVIDER_ID,
            session_id=sid,
            span_id=span_id,
            parent_span_id=str(parent_span_id) if parent_span_id else None,
            timestamp=start_ts,
            event=de.ToolStart(tool_id=span_id, tool_name=name, input=None),
        ),
        de.EventEnvelope(
            provider_id=PROVIDER_ID,
            session_id=sid,
            span_id=span_id,
            parent_span_id=str(parent_span_id) if parent_span_id else None,
            timestamp=end_ts,
            event=de.ToolEnd(tool_id=span_id),
        ),
    ]

    in_tok = next((int(merged[k]) for k in _INPUT_TOK_KEYS if k in merged), 0)
    out_tok = next((int(merged[k]) for k in _OUTPUT_TOK_KEYS if k in merged), 0)
    if in_tok or out_tok:
        out.append(
            de.EventEnvelope(
                provider_id=PROVIDER_ID,
                session_id=sid,
                span_id=span_id,
                parent_span_id=str(parent_span_id) if parent_span_id else None,
                timestamp=end_ts,
                event=de.TokenUsage(input_tokens=in_tok, output_tokens=out_tok),
            )
        )
    return out


def normalize_line(line: str) -> list[de.EventEnvelope]:
    try:
        doc = json.loads(line)
    except json.JSONDecodeError:
        return []
    out: list[de.EventEnvelope] = []
    for rspans in doc.get("resourceSpans", []) or []:
        resource_attrs = _attrs_to_dict((rspans.get("resource") or {}).get("attributes"))
        for sspans in rspans.get("scopeSpans", []) or []:
            for span in sspans.get("spans", []) or []:
                out.extend(span_to_events(span, resource_attrs))
    return out


async def run_otel_file_adapter(
    path: Path,
    on_envelope,
    *,
    from_start: bool = False,
) -> None:
    log.info("otel: tailing %s", path)
    async for line in tail_lines(path, from_start=from_start):
        for env in normalize_line(line):
            await on_envelope(env)
