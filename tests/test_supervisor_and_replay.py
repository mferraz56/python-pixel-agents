"""Adapter supervisor + replay HTTP endpoint tests."""
from __future__ import annotations

import os
from pathlib import Path

import httpx
import pytest

os.environ.setdefault("PIXEL_AGENTS_TOKEN", "test-token")
os.environ.setdefault("PIXEL_AGENTS_REPLAY_DIR", str(Path(__file__).parent / "_data_replay"))
os.environ.setdefault("PIXEL_AGENTS_KEEPALIVE", "1")

from app.main import app  # noqa: E402
from app.protocol import domain_events as de  # noqa: E402
from app.services.adapter_supervisor import AdapterSupervisor  # noqa: E402
from app.services.event_bus import EventBus  # noqa: E402
from app.services.replay_store import ReplayStore  # noqa: E402
from app.services.session_aggregator import SessionAggregator  # noqa: E402


@pytest.mark.asyncio
async def test_supervisor_dedups_same_span_across_providers(tmp_path: Path) -> None:
    agg = SessionAggregator()
    bus = EventBus()
    replay = ReplayStore(tmp_path)
    sup = AdapterSupervisor(aggregator=agg, bus=bus, replay=replay)

    env_a = de.EventEnvelope(
        provider_id="copilot",
        session_id="s",
        span_id="span-1",
        event=de.TokenUsage(input_tokens=100, output_tokens=10),
    )
    env_b = de.EventEnvelope(
        provider_id="copilot",
        session_id="s",
        span_id="span-1",
        event=de.TokenUsage(input_tokens=100, output_tokens=10),
    )

    await sup._ingest(env_a)
    await sup._ingest(env_b)

    snapshot = await agg.snapshot()
    token_msgs = [m for m in snapshot if m.type == "agentTokenUsage"]
    assert len(token_msgs) == 1
    assert token_msgs[0].input_tokens == 100  # not 200
    assert token_msgs[0].output_tokens == 10


@pytest.mark.asyncio
async def test_supervisor_does_not_dedup_when_span_id_missing(tmp_path: Path) -> None:
    agg = SessionAggregator()
    bus = EventBus()
    replay = ReplayStore(tmp_path)
    sup = AdapterSupervisor(aggregator=agg, bus=bus, replay=replay)

    for _ in range(2):
        await sup._ingest(
            de.EventEnvelope(
                provider_id="copilot",
                session_id="s",
                event=de.TokenUsage(input_tokens=50, output_tokens=5),
            )
        )

    snapshot = await agg.snapshot()
    token_msgs = [m for m in snapshot if m.type == "agentTokenUsage"]
    assert token_msgs[0].input_tokens == 100  # accumulated, no dedup possible


@pytest.mark.asyncio
async def test_replay_endpoint_round_trip() -> None:
    transport = httpx.ASGITransport(app=app)
    headers = {"Authorization": "Bearer test-token"}
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.post(
            "/api/hooks/copilot",
            headers=headers,
            json={
                "session_id": "s-replay",
                "event": {"kind": "sessionStart", "source": "copilot"},
            },
        )
        assert r.status_code == 204
        r = await client.post(
            "/api/hooks/copilot",
            headers=headers,
            json={
                "session_id": "s-replay",
                "event": {"kind": "toolStart", "tool_id": "t1", "tool_name": "Read"},
            },
        )
        assert r.status_code == 204

        # Listing
        r = await client.get("/api/viewer/sessions", headers=headers)
        assert r.status_code == 200
        sessions = r.json()["sessions"]
        assert any(
            s["providerId"] == "copilot" and s["sessionId"] == "s-replay" for s in sessions
        )

        # Replay
        r = await client.get("/api/viewer/replay/copilot/s-replay", headers=headers)
        assert r.status_code == 200
        body = r.json()
        assert body["envelopeCount"] >= 2
        types = [m["type"] for m in body["messages"]]
        # Replay returns the full project() stream, not the snapshot.
        assert "agentCreated" in types
        assert "agentStatus" in types
        assert "agentToolStart" in types

        # 404 for unknown
        r = await client.get("/api/viewer/replay/copilot/nope", headers=headers)
        assert r.status_code == 404


@pytest.mark.asyncio
async def test_replay_listing_requires_auth() -> None:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.get("/api/viewer/sessions")
        assert r.status_code == 401


@pytest.mark.asyncio
async def test_supervisor_dedups_subagent_start_across_adapters(tmp_path: Path) -> None:
    """OTel and debug-log adapters both emit SubagentStart with the same
    synthetic span_id ``subagent-{call_id}``. The supervisor's
    ``_is_duplicate`` must collapse them."""
    agg = SessionAggregator()
    bus = EventBus()
    replay = ReplayStore(tmp_path)
    sup = AdapterSupervisor(aggregator=agg, bus=bus, replay=replay)

    call_id = "call_dedup0123456789ABCDEF"
    span = f"subagent-{call_id}"

    env = de.EventEnvelope(
        provider_id="copilot",
        session_id="parent-sid",
        span_id=span,
        event=de.SubagentStart(
            parent_tool_id="parent-span",
            tool_id=f"child-{call_id}",
            tool_name="ia-test",
        ),
    )

    assert sup._is_duplicate(env) is False  # first sighting
    assert sup._is_duplicate(env) is True   # second collapses


@pytest.mark.asyncio
async def test_replay_emits_subagent_start_and_done() -> None:
    transport = httpx.ASGITransport(app=app)
    headers = {"Authorization": "Bearer test-token"}
    sid = "s-subagent-replay"
    call_id = "call_replay0123456789ABCDEF"
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        await client.post(
            "/api/hooks/copilot",
            headers=headers,
            json={
                "session_id": sid,
                "event": {"kind": "sessionStart", "source": "copilot"},
            },
        )
        await client.post(
            "/api/hooks/copilot",
            headers=headers,
            json={
                "session_id": sid,
                "span_id": f"subagent-{call_id}",
                "event": {
                    "kind": "subagentStart",
                    "parent_tool_id": "parent-span",
                    "tool_id": f"child-{call_id}",
                    "tool_name": "ia-test",
                },
            },
        )
        await client.post(
            "/api/hooks/copilot",
            headers=headers,
            json={
                "session_id": sid,
                "span_id": f"subagent-{call_id}",
                "event": {
                    "kind": "subagentEnd",
                    "parent_tool_id": "parent-span",
                    "tool_id": f"child-{call_id}",
                },
            },
        )

        r = await client.get(f"/api/viewer/replay/copilot/{sid}", headers=headers)
        assert r.status_code == 200
        types = [m["type"] for m in r.json()["messages"]]
        assert "subagentToolStart" in types
        assert "subagentToolDone" in types
        assert types.index("subagentToolStart") < types.index("subagentToolDone")
