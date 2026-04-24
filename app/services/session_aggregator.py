"""Live state aggregator + ViewerMessage projector.

Holds the minimum state required to:
  1. Project incoming `EventEnvelope` instances into one or more
     `ViewerMessage` instances for live broadcast.
  2. Rebuild a bootstrap snapshot for a freshly connected SSE client.

State is in-memory only; persistence is the replay store's job.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

from ..protocol import domain_events as de
from ..protocol import viewer_messages as vm


@dataclass
class _AgentState:
    numeric_id: int
    provider_id: str
    session_id: str
    folder_name: str | None = None
    parent_agent_numeric_id: int | None = None
    status: str = "active"  # 'active' | 'waiting'
    active_tools: dict[str, str] = field(default_factory=dict)  # tool_id -> status label
    permission_pending: bool = False
    input_tokens: int = 0
    output_tokens: int = 0


class SessionAggregator:
    """Maps (provider_id, session_id, agent_id?) → numeric viewer agent id."""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._next_id: int = 1
        # Key: (provider_id, session_id, agent_id-or-None)
        self._agents: dict[tuple[str, str, str | None], _AgentState] = {}

    async def project(self, env: de.EventEnvelope) -> list[vm.ViewerMessage]:
        async with self._lock:
            return self._project_locked(env)

    async def snapshot(self) -> list[vm.ViewerMessage]:
        """Build the bootstrap message burst for a new SSE client."""
        async with self._lock:
            messages: list[vm.ViewerMessage] = []
            ids = [a.numeric_id for a in self._agents.values()]
            folder_names = {
                a.numeric_id: a.folder_name for a in self._agents.values() if a.folder_name
            }
            messages.append(
                vm.ExistingAgentsMessage(agents=ids, folder_names=folder_names or None)
            )
            for agent in self._agents.values():
                messages.append(
                    vm.AgentCreatedMessage(
                        id=agent.numeric_id,
                        folder_name=agent.folder_name,
                        parent_agent_id=agent.parent_agent_numeric_id,
                    )
                )
                messages.append(
                    vm.AgentStatusMessage(id=agent.numeric_id, status=agent.status)  # type: ignore[arg-type]
                )
                for tool_id, status in agent.active_tools.items():
                    messages.append(
                        vm.AgentToolStartMessage(
                            id=agent.numeric_id, tool_id=tool_id, status=status
                        )
                    )
                if agent.permission_pending:
                    messages.append(vm.AgentToolPermissionMessage(id=agent.numeric_id))
                if agent.input_tokens or agent.output_tokens:
                    messages.append(
                        vm.AgentTokenUsageMessage(
                            id=agent.numeric_id,
                            input_tokens=agent.input_tokens,
                            output_tokens=agent.output_tokens,
                        )
                    )
            return messages

    # ── internals ─────────────────────────────────────────────

    def _key(self, env: de.EventEnvelope) -> tuple[str, str, str | None]:
        return (env.provider_id, env.session_id, env.agent_id)

    def _ensure_agent(self, env: de.EventEnvelope) -> tuple[_AgentState, bool]:
        key = self._key(env)
        existing = self._agents.get(key)
        if existing is not None:
            return existing, False
        parent_numeric: int | None = None
        if env.parent_agent_id is not None:
            parent_key = (env.provider_id, env.session_id, env.parent_agent_id)
            parent = self._agents.get(parent_key)
            if parent is not None:
                parent_numeric = parent.numeric_id
        state = _AgentState(
            numeric_id=self._next_id,
            provider_id=env.provider_id,
            session_id=env.session_id,
            folder_name=f"{env.provider_id}/{env.session_id[:8]}",
            parent_agent_numeric_id=parent_numeric,
        )
        self._next_id += 1
        self._agents[key] = state
        return state, True

    def _project_locked(self, env: de.EventEnvelope) -> list[vm.ViewerMessage]:
        evt = env.event
        out: list[vm.ViewerMessage] = []

        # sessionStart and any first-touch event creates the agent.
        agent, created = self._ensure_agent(env)
        if created:
            out.append(
                vm.AgentCreatedMessage(
                    id=agent.numeric_id,
                    folder_name=agent.folder_name,
                    parent_agent_id=agent.parent_agent_numeric_id,
                )
            )
            out.append(vm.AgentStatusMessage(id=agent.numeric_id, status="active"))

        if isinstance(evt, de.SessionStart):
            return out  # creation already handled

        if isinstance(evt, de.SessionEnd):
            out.append(vm.AgentClosedMessage(id=agent.numeric_id))
            self._agents.pop(self._key(env), None)
            return out

        if isinstance(evt, de.ToolStart):
            agent.active_tools[evt.tool_id] = evt.tool_name
            agent.status = "active"
            out.append(
                vm.AgentToolStartMessage(
                    id=agent.numeric_id,
                    tool_id=evt.tool_id,
                    status=evt.tool_name,
                    tool_name=evt.tool_name,
                )
            )
            return out

        if isinstance(evt, de.ToolEnd):
            agent.active_tools.pop(evt.tool_id, None)
            out.append(vm.AgentToolDoneMessage(id=agent.numeric_id, tool_id=evt.tool_id))
            return out

        if isinstance(evt, de.TurnEnd):
            agent.status = "waiting"
            out.append(vm.AgentStatusMessage(id=agent.numeric_id, status="waiting"))
            return out

        if isinstance(evt, de.UserTurn):
            agent.status = "active"
            out.append(vm.AgentStatusMessage(id=agent.numeric_id, status="active"))
            return out

        if isinstance(evt, de.SubagentStart):
            out.append(
                vm.SubagentToolStartMessage(
                    id=agent.numeric_id,
                    parent_tool_id=evt.parent_tool_id,
                    tool_id=evt.tool_id,
                    status=evt.tool_name,
                )
            )
            return out

        if isinstance(evt, de.SubagentEnd):
            out.append(
                vm.SubagentToolDoneMessage(
                    id=agent.numeric_id,
                    parent_tool_id=evt.parent_tool_id,
                    tool_id=evt.tool_id,
                )
            )
            return out

        if isinstance(evt, de.PermissionRequest):
            agent.permission_pending = True
            out.append(vm.AgentToolPermissionMessage(id=agent.numeric_id))
            return out

        if isinstance(evt, de.TokenUsage):
            agent.input_tokens += evt.input_tokens
            agent.output_tokens += evt.output_tokens
            out.append(
                vm.AgentTokenUsageMessage(
                    id=agent.numeric_id,
                    input_tokens=agent.input_tokens,
                    output_tokens=agent.output_tokens,
                )
            )
            return out

        # Progress / SubagentTurnEnd / unknown: pass through generically.
        out.append(
            vm.GenericViewerMessage.of(
                "domainEvent",
                providerId=env.provider_id,
                sessionId=env.session_id,
                agentNumericId=agent.numeric_id,
                event=evt.model_dump(by_alias=False),
            )
        )
        return out


def parse_envelope(payload: dict[str, Any]) -> de.EventEnvelope:
    """Validate raw POST body into a normalized envelope."""
    return de.EventEnvelope.model_validate(payload)
