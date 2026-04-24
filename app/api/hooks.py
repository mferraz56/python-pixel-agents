"""POST /api/hooks/{provider_id} — inbound normalized envelope."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request, Response, status

from ..auth import require_token
from ..protocol.viewer_messages import serialize
from ..services.session_aggregator import parse_envelope
from .deps import get_aggregator, get_bus, get_replay_store

router = APIRouter()


@router.post("/api/hooks/{provider_id}", status_code=status.HTTP_204_NO_CONTENT)
async def receive_hook(
    provider_id: str,
    request: Request,
    _: None = Depends(require_token),
) -> Response:
    payload: dict[str, Any] = await request.json()
    payload.setdefault("provider_id", provider_id)
    envelope = parse_envelope(payload)

    replay = get_replay_store(request.app)
    aggregator = get_aggregator(request.app)
    bus = get_bus(request.app)

    await replay.append(envelope)
    messages = await aggregator.project(envelope)
    for msg in messages:
        await bus.publish(serialize(msg))

    return Response(status_code=status.HTTP_204_NO_CONTENT)
