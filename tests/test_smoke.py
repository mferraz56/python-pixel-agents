"""End-to-end smoke test: post a hook event, read it back via SSE."""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

import httpx
import pytest

os.environ.setdefault("PIXEL_AGENTS_TOKEN", "test-token")
os.environ.setdefault("PIXEL_AGENTS_REPLAY_DIR", str(Path(__file__).parent / "_data"))
os.environ.setdefault("PIXEL_AGENTS_KEEPALIVE", "1")

from app.main import app  # noqa: E402


@pytest.mark.asyncio
async def test_health() -> None:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.get("/api/health")
        assert r.status_code == 200
        assert r.json()["status"] == "ok"


@pytest.mark.asyncio
async def test_hook_auth_required() -> None:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.post(
            "/api/hooks/copilot",
            json={
                "session_id": "s1",
                "event": {"kind": "sessionStart", "source": "copilot"},
            },
        )
        assert r.status_code == 401


@pytest.mark.asyncio
async def test_hook_round_trip_into_aggregator() -> None:
    transport = httpx.ASGITransport(app=app)
    headers = {"Authorization": "Bearer test-token"}
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.post(
            "/api/hooks/copilot",
            headers=headers,
            json={
                "session_id": "s-roundtrip",
                "event": {"kind": "sessionStart", "source": "copilot"},
            },
        )
        assert r.status_code == 204

        r = await client.post(
            "/api/hooks/copilot",
            headers=headers,
            json={
                "session_id": "s-roundtrip",
                "event": {"kind": "toolStart", "tool_id": "t1", "tool_name": "Read"},
            },
        )
        assert r.status_code == 204

    snapshot = await app.state.aggregator.snapshot()
    types = [m.type for m in snapshot]
    assert "existingAgents" in types
    assert "agentCreated" in types
    assert "agentToolStart" in types


@pytest.mark.asyncio
async def test_sse_emits_bootstrap_then_live() -> None:
    """Drive the SSE generator directly to avoid ASGI stream-shutdown quirks."""
    from app.api.viewer import _sse_frame
    from app.protocol import domain_events as de
    from app.protocol.viewer_messages import serialize

    bus = app.state.bus
    aggregator = app.state.aggregator

    # Seed an agent so bootstrap is non-empty.
    env = de.EventEnvelope(
        provider_id="copilot",
        session_id="s-sse",
        event=de.SessionStart(source="copilot"),
    )
    for msg in await aggregator.project(env):
        await bus.publish(serialize(msg))

    # Snapshot frame.
    snapshot = await aggregator.snapshot()
    bootstrap_frame = _sse_frame("bootstrap", [serialize(m) for m in snapshot])
    assert b"event: bootstrap" in bootstrap_frame
    assert b"existingAgents" in bootstrap_frame

    # Live frame via the bus.
    async with bus.subscribe() as queue:
        live_env = de.EventEnvelope(
            provider_id="copilot",
            session_id="s-sse",
            event=de.ToolStart(tool_id="tool-x", tool_name="Grep"),
        )
        for msg in await aggregator.project(live_env):
            await bus.publish(serialize(msg))

        msg = await asyncio.wait_for(queue.get(), timeout=2)
        live_frame = _sse_frame("message", msg)
        assert b"event: message" in live_frame
        assert b"agentToolStart" in live_frame
        assert b"toolId" in live_frame  # camelCase wire format
