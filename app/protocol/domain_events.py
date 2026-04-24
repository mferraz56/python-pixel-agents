"""Normalized internal domain events.

Mirrors the AgentEvent union from
`pixel-agents/server/src/provider.ts` but is the source of truth for this
service. Every adapter (Copilot OTel, Copilot debug-log, future providers)
must produce values from this module; the projector turns them into outbound
ViewerMessage instances.

The envelope is provider/session-scoped so the aggregator can correlate
across providers without leaking provider-specific shapes downstream.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated, Any, Literal, Union

from pydantic import BaseModel, ConfigDict, Field


# ── Discriminated union of agent events ───────────────────────


class _EventBase(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ToolStart(_EventBase):
    kind: Literal["toolStart"] = "toolStart"
    tool_id: str
    tool_name: str
    input: Any | None = None


class ToolEnd(_EventBase):
    kind: Literal["toolEnd"] = "toolEnd"
    tool_id: str


class TurnEnd(_EventBase):
    kind: Literal["turnEnd"] = "turnEnd"


class UserTurn(_EventBase):
    kind: Literal["userTurn"] = "userTurn"


class SubagentStart(_EventBase):
    kind: Literal["subagentStart"] = "subagentStart"
    parent_tool_id: str
    tool_id: str
    tool_name: str
    input: Any | None = None


class SubagentEnd(_EventBase):
    kind: Literal["subagentEnd"] = "subagentEnd"
    parent_tool_id: str
    tool_id: str


class SubagentTurnEnd(_EventBase):
    kind: Literal["subagentTurnEnd"] = "subagentTurnEnd"
    parent_tool_id: str


class Progress(_EventBase):
    kind: Literal["progress"] = "progress"
    tool_id: str
    data: Any


class PermissionRequest(_EventBase):
    kind: Literal["permissionRequest"] = "permissionRequest"


class SessionStart(_EventBase):
    kind: Literal["sessionStart"] = "sessionStart"
    source: str | None = None


class SessionEnd(_EventBase):
    kind: Literal["sessionEnd"] = "sessionEnd"
    reason: str | None = None


class TokenUsage(_EventBase):
    """Not part of the original AgentEvent union but commonly emitted."""

    kind: Literal["tokenUsage"] = "tokenUsage"
    input_tokens: int = 0
    output_tokens: int = 0


AgentEvent = Annotated[
    Union[
        ToolStart,
        ToolEnd,
        TurnEnd,
        UserTurn,
        SubagentStart,
        SubagentEnd,
        SubagentTurnEnd,
        Progress,
        PermissionRequest,
        SessionStart,
        SessionEnd,
        TokenUsage,
    ],
    Field(discriminator="kind"),
]


# ── Envelope ──────────────────────────────────────────────────


class EventEnvelope(BaseModel):
    """Normalized event as it travels through the internal pipeline."""

    model_config = ConfigDict(extra="forbid")

    provider_id: str
    session_id: str
    agent_id: str | None = None
    parent_agent_id: str | None = None
    span_id: str | None = None
    parent_span_id: str | None = None
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    event: AgentEvent
