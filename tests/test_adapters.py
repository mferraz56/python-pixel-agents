"""Adapter normalizer tests (no I/O)."""
from __future__ import annotations

import json

from app.adapters.copilot_debug_adapter import normalize_record
from app.adapters.copilot_otel_adapter import normalize_line
from app.protocol import domain_events as de


def test_debug_session_start() -> None:
    rec = {
        "ts": 1776885378359,
        "dur": 0,
        "sid": "sess-1",
        "type": "session_start",
        "attrs": {"copilotVersion": "0.45.0"},
    }
    envs = normalize_record(rec)
    assert len(envs) == 1
    assert isinstance(envs[0].event, de.SessionStart)
    assert envs[0].session_id == "sess-1"


def test_debug_user_message_then_turn_end() -> None:
    user = normalize_record({"ts": 1, "sid": "s", "type": "user_message"})
    end = normalize_record({"ts": 2, "sid": "s", "type": "turn_end"})
    assert isinstance(user[0].event, de.UserTurn)
    assert isinstance(end[0].event, de.TurnEnd)


def test_debug_tool_call_emits_start_then_end() -> None:
    rec = {
        "ts": 100,
        "dur": 50,
        "sid": "s",
        "type": "tool_call",
        "spanId": "tc-1",
        "name": "Read",
        "attrs": {},
    }
    envs = normalize_record(rec)
    assert [type(e.event).__name__ for e in envs] == ["ToolStart", "ToolEnd"]
    assert envs[0].event.tool_id == "tc-1"  # type: ignore[union-attr]
    assert envs[0].event.tool_name == "Read"  # type: ignore[union-attr]


def test_debug_llm_request_emits_token_usage() -> None:
    rec = {
        "ts": 1,
        "sid": "s",
        "type": "llm_request",
        "attrs": {"inputTokens": 10, "outputTokens": 5},
    }
    envs = normalize_record(rec)
    assert len(envs) == 1
    ev = envs[0].event
    assert isinstance(ev, de.TokenUsage)
    assert (ev.input_tokens, ev.output_tokens) == (10, 5)


def test_debug_child_session_ref_creates_subagent_and_child() -> None:
    rec = {
        "ts": 1,
        "sid": "parent",
        "type": "child_session_ref",
        "spanId": "ref-1",
        "attrs": {
            "childSessionId": "11111111-2222-3333-4444-555555555555",
            "label": "researcher",
        },
    }
    envs = normalize_record(rec)
    assert len(envs) == 2
    assert isinstance(envs[0].event, de.SubagentStart)
    assert envs[0].session_id == "parent"
    assert isinstance(envs[1].event, de.SessionStart)
    assert envs[1].session_id == "11111111-2222-3333-4444-555555555555"
    assert envs[1].parent_agent_id == "parent"


def test_debug_child_session_ref_emits_child_when_log_file_present() -> None:
    """Real Copilot runSubagent invocations carry attrs.childLogFile and
    must be surfaced as full child sessions even when the child sid is a
    non-UUID call_xxx token (the runSubagent tool's call id)."""
    rec = {
        "ts": 1,
        "sid": "parent",
        "type": "child_session_ref",
        "spanId": "ref-1",
        "attrs": {
            "childSessionId": "call_abc123",
            "label": "runSubagent-ia-context-manager",
            "childLogFile": "runSubagent-ia-context-manager-call_abc123.jsonl",
        },
    }
    envs = normalize_record(rec)
    assert len(envs) == 2
    assert isinstance(envs[0].event, de.SubagentStart)
    assert isinstance(envs[1].event, de.SessionStart)
    assert envs[1].session_id == "call_abc123"


def test_debug_child_session_ref_skips_non_uuid_child() -> None:
    """API-side tool/use ids without childLogFile are still phantom."""
    rec = {
        "ts": 1,
        "sid": "parent",
        "type": "child_session_ref",
        "spanId": "ref-1",
        "attrs": {"childSessionId": "toolu_abc", "label": "tool"},
    }
    envs = normalize_record(rec)
    # Only the SubagentStart on the parent; no phantom child agent.
    assert len(envs) == 1
    assert isinstance(envs[0].event, de.SubagentStart)


def test_debug_unknown_record_is_ignored() -> None:
    assert normalize_record({"ts": 1, "sid": "s", "type": "discovery"}) == []
    assert normalize_record({"ts": 1, "type": "no_sid"}) == []


def test_otel_span_to_tool_pair_with_token_usage() -> None:
    doc = {
        "resourceSpans": [
            {
                "resource": {
                    "attributes": [
                        {"key": "service.name", "value": {"stringValue": "copilot"}},
                    ]
                },
                "scopeSpans": [
                    {
                        "scope": {"name": "copilot.chat"},
                        "spans": [
                            {
                                "traceId": "abc",
                                "spanId": "span-1",
                                "name": "llm.chat",
                                "startTimeUnixNano": "1000000000",
                                "endTimeUnixNano": "1500000000",
                                "attributes": [
                                    {
                                        "key": "copilot.session.id",
                                        "value": {"stringValue": "sess-otel"},
                                    },
                                    {
                                        "key": "gen_ai.usage.input_tokens",
                                        "value": {"intValue": "42"},
                                    },
                                    {
                                        "key": "gen_ai.usage.output_tokens",
                                        "value": {"intValue": "7"},
                                    },
                                ],
                            }
                        ],
                    }
                ],
            }
        ]
    }
    envs = normalize_line(json.dumps(doc))
    kinds = [type(e.event).__name__ for e in envs]
    assert kinds == ["ToolStart", "ToolEnd", "TokenUsage"]
    assert all(e.session_id == "sess-otel" for e in envs)
    tu = envs[2].event
    assert isinstance(tu, de.TokenUsage)
    assert (tu.input_tokens, tu.output_tokens) == (42, 7)


def test_otel_skips_in_flight_span() -> None:
    doc = {
        "resourceSpans": [
            {
                "scopeSpans": [
                    {
                        "spans": [
                            {
                                "traceId": "x",
                                "spanId": "y",
                                "name": "n",
                                "startTimeUnixNano": "1",
                            }
                        ]
                    }
                ]
            }
        ]
    }
    assert normalize_line(json.dumps(doc)) == []


def test_otel_invalid_json_returns_empty() -> None:
    assert normalize_line("not json") == []


# ── Subagent end correlation (debug-log path) ──────────────────────────


def _ref_record(parent_sid: str, child_sid: str, *, with_log: bool = True) -> dict:
    attrs = {
        "childSessionId": child_sid,
        "label": f"runSubagent-ia-test",
    }
    if with_log:
        attrs["childLogFile"] = f"runSubagent-ia-test-{child_sid}.jsonl"
    return {
        "ts": 1_700_000_000_000,
        "sid": parent_sid,
        "type": "child_session_ref",
        "spanId": "parent-span",
        "attrs": attrs,
    }


def test_debug_subagent_terminator_emits_subagent_end_on_parent() -> None:
    parent_sid = "parent-sid-001"
    child_sid = "call_term0123456789ABCDEF"
    # Parent registers the child.
    start_envs = normalize_record(_ref_record(parent_sid, child_sid))
    assert any(isinstance(e.event, de.SubagentStart) for e in start_envs)

    # Terminator record from the child stream.
    term = {
        "ts": 1_700_000_001_000,
        "dur": 1234,
        "sid": child_sid,
        "type": "subagent",
        "name": "ia-test",
        "spanId": "child-span",
        "attrs": {"agentName": "ia-test"},
    }
    end_envs = normalize_record(term)
    assert len(end_envs) == 1
    end_env = end_envs[0]
    assert isinstance(end_env.event, de.SubagentEnd)
    assert end_env.session_id == parent_sid  # routed to PARENT
    assert end_env.event.tool_id == f"child-{child_sid}"
    assert end_env.span_id == f"subagent-{child_sid}"

    # Idempotent: a second terminator must not re-emit.
    assert normalize_record(term) == []


def test_debug_subagent_terminator_without_registry_is_ignored() -> None:
    term = {
        "ts": 1,
        "sid": "call_unknown1234567890ABCDEF",
        "type": "subagent",
        "name": "ghost",
        "attrs": {},
    }
    assert normalize_record(term) == []


# ── OTel runSubagent emission ──────────────────────────────────────────


def test_otel_runsubagent_emits_subagent_pair_and_skips_tool_pair() -> None:
    call_id = "call_OtelABC0123456789XYZ"
    args = json.dumps(
        {
            "prompt": "do work",
            "description": "short",
            "agentName": "ia-context-manager",
        }
    )
    doc = {
        "resourceSpans": [
            {
                "scopeSpans": [
                    {
                        "spans": [
                            {
                                "traceId": "trace-1",
                                "spanId": "child-span-1",
                                "parentSpanId": "parent-span-1",
                                "name": "execute_tool runSubagent",
                                "startTimeUnixNano": "1000000000",
                                "endTimeUnixNano": "2000000000",
                                "attributes": [
                                    {
                                        "key": "copilot_chat.chat_session_id",
                                        "value": {"stringValue": "sess-otel-2"},
                                    },
                                    {
                                        "key": "gen_ai.tool.name",
                                        "value": {"stringValue": "runSubagent"},
                                    },
                                    {
                                        "key": "gen_ai.tool.call.id",
                                        "value": {"stringValue": call_id},
                                    },
                                    {
                                        "key": "gen_ai.tool.call.arguments",
                                        "value": {"stringValue": args},
                                    },
                                ],
                            }
                        ]
                    }
                ]
            }
        ]
    }
    envs = normalize_line(json.dumps(doc))
    kinds = [type(e.event).__name__ for e in envs]
    assert kinds == ["SubagentStart", "SubagentEnd"]
    start = envs[0]
    assert start.session_id == "sess-otel-2"
    assert start.span_id == f"subagent-{call_id}"
    assert start.parent_span_id == "parent-span-1"
    assert isinstance(start.event, de.SubagentStart)
    assert start.event.parent_tool_id == "parent-span-1"
    assert start.event.tool_id == f"child-{call_id}"
    assert start.event.tool_name == "ia-context-manager"


def test_otel_non_subagent_span_unchanged() -> None:
    doc = {
        "resourceSpans": [
            {
                "scopeSpans": [
                    {
                        "spans": [
                            {
                                "traceId": "t",
                                "spanId": "s1",
                                "name": "execute_tool read_file",
                                "startTimeUnixNano": "1",
                                "endTimeUnixNano": "2",
                                "attributes": [
                                    {
                                        "key": "copilot_chat.chat_session_id",
                                        "value": {"stringValue": "sess-x"},
                                    },
                                    {
                                        "key": "gen_ai.tool.name",
                                        "value": {"stringValue": "read_file"},
                                    },
                                ],
                            }
                        ]
                    }
                ]
            }
        ]
    }
    envs = normalize_line(json.dumps(doc))
    kinds = [type(e.event).__name__ for e in envs]
    assert kinds == ["ToolStart", "ToolEnd"]
