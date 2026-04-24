"""Outbound ViewerMessage contract.

Mirrors `pixel-agents/shared/messages.ts` for wire compatibility with the
existing remote viewer. Field names use camelCase on the wire (matching the
TypeScript contract) while staying snake_case in Python via aliases.

Only the subset needed for v1 is modeled as concrete types; anything else
falls into the open `GenericViewerMessage` shape.
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel


class _MsgBase(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        extra="allow",
    )


class AgentCreatedMessage(_MsgBase):
    type: Literal["agentCreated"] = "agentCreated"
    id: int
    folder_name: str | None = None
    is_external: bool | None = None
    parent_agent_id: int | None = None


class AgentClosedMessage(_MsgBase):
    type: Literal["agentClosed"] = "agentClosed"
    id: int


class AgentToolStartMessage(_MsgBase):
    type: Literal["agentToolStart"] = "agentToolStart"
    id: int
    tool_id: str
    status: str
    tool_name: str | None = None


class AgentToolDoneMessage(_MsgBase):
    type: Literal["agentToolDone"] = "agentToolDone"
    id: int
    tool_id: str


class AgentStatusMessage(_MsgBase):
    type: Literal["agentStatus"] = "agentStatus"
    id: int
    status: Literal["active", "waiting"]


class AgentToolPermissionMessage(_MsgBase):
    type: Literal["agentToolPermission"] = "agentToolPermission"
    id: int


class SubagentToolStartMessage(_MsgBase):
    type: Literal["subagentToolStart"] = "subagentToolStart"
    id: int
    parent_tool_id: str
    tool_id: str
    status: str


class SubagentToolDoneMessage(_MsgBase):
    type: Literal["subagentToolDone"] = "subagentToolDone"
    id: int
    parent_tool_id: str
    tool_id: str


class AgentTokenUsageMessage(_MsgBase):
    type: Literal["agentTokenUsage"] = "agentTokenUsage"
    id: int
    input_tokens: int
    output_tokens: int


class ExistingAgentsMessage(_MsgBase):
    type: Literal["existingAgents"] = "existingAgents"
    agents: list[int]
    folder_names: dict[int, str] | None = Field(default=None)


class GenericViewerMessage(_MsgBase):
    """Open-shape escape hatch used for forward-compatible message types."""

    type: str

    # Allow arbitrary extra fields; pydantic v2 keeps them via extra="allow".
    @classmethod
    def of(cls, type_: str, **fields: Any) -> "GenericViewerMessage":
        return cls(type=type_, **fields)


ViewerMessage = (
    AgentCreatedMessage
    | AgentClosedMessage
    | AgentToolStartMessage
    | AgentToolDoneMessage
    | AgentStatusMessage
    | AgentToolPermissionMessage
    | SubagentToolStartMessage
    | SubagentToolDoneMessage
    | AgentTokenUsageMessage
    | ExistingAgentsMessage
    | GenericViewerMessage
)


def serialize(message: ViewerMessage) -> dict[str, Any]:
    """Render a viewer message to its on-the-wire dict (camelCase)."""
    return message.model_dump(by_alias=True, exclude_none=True)
