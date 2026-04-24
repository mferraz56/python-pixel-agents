"""GET /api/viewer/events — SSE stream with bootstrap + live messages."""
from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse

from ..auth import require_token
from ..config import settings
from ..protocol.viewer_messages import serialize
from ..services.session_aggregator import SessionAggregator
from .deps import get_aggregator, get_bus, get_replay_store as get_replay

router = APIRouter()


def _sse_frame(event: str, payload: object) -> bytes:
    return f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n".encode("utf-8")


@router.get("/api/viewer/events")
async def viewer_events(
    request: Request,
    _: None = Depends(require_token),
) -> StreamingResponse:
    aggregator = get_aggregator(request.app)
    bus = get_bus(request.app)

    async def stream() -> AsyncIterator[bytes]:
        snapshot = await aggregator.snapshot()
        yield _sse_frame("bootstrap", [serialize(m) for m in snapshot])
        async with bus.subscribe() as queue:
            while True:
                if await request.is_disconnected():
                    return
                try:
                    msg = await asyncio.wait_for(queue.get(), timeout=settings.keepalive_seconds)
                except asyncio.TimeoutError:
                    yield b":keepalive\n\n"
                    continue
                yield _sse_frame("message", msg)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


@router.get("/api/viewer/sessions")
async def list_sessions(
    request: Request,
    _: None = Depends(require_token),
) -> JSONResponse:
    """List all persisted (provider, session) pairs available for replay."""
    replay = get_replay(request.app)
    sessions = [
        {"providerId": provider, "sessionId": session}
        for provider, session in replay.list_sessions()
    ]
    return JSONResponse({"sessions": sessions})


@router.get("/api/viewer/replay/{provider_id}/{session_id}")
async def replay_session(
    provider_id: str,
    session_id: str,
    request: Request,
    _: None = Depends(require_token),
) -> JSONResponse:
    """Replay a persisted session as the full ordered viewer-message stream.

    The aggregator is replayed *in isolation* over the persisted envelopes
    so the response is deterministic and independent of live state. Returns
    every projected message (including transient ones like subagentToolStart)
    so the viewer can recreate the full timeline, not just the terminal state.
    """
    replay = get_replay(request.app)
    envelopes = replay.read(provider_id, session_id)
    if not envelopes:
        raise HTTPException(status_code=404, detail="session not found")
    aggregator = SessionAggregator()
    messages: list = []
    for env in envelopes:
        for msg in await aggregator.project(env):
            messages.append(msg)
    return JSONResponse(
        {
            "providerId": provider_id,
            "sessionId": session_id,
            "envelopeCount": len(envelopes),
            "messages": [serialize(m) for m in messages],
        }
    )
