"""In-process pub/sub for ViewerMessage broadcast.

Designed to be replaceable by Redis Streams or NATS without changing
producers or SSE consumers. Each subscriber gets its own bounded queue;
slow subscribers drop oldest items rather than blocking the publisher.
"""
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any


class EventBus:
    def __init__(self, *, max_queue: int = 256) -> None:
        self._max_queue = max_queue
        self._subscribers: set[asyncio.Queue[dict[str, Any]]] = set()
        self._lock = asyncio.Lock()

    async def publish(self, message: dict[str, Any]) -> None:
        async with self._lock:
            targets = list(self._subscribers)
        for q in targets:
            if q.full():
                # Drop oldest to keep up; slow client must reconnect for full state.
                try:
                    q.get_nowait()
                except asyncio.QueueEmpty:
                    pass
            q.put_nowait(message)

    @asynccontextmanager
    async def subscribe(self) -> AsyncIterator[asyncio.Queue[dict[str, Any]]]:
        q: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=self._max_queue)
        async with self._lock:
            self._subscribers.add(q)
        try:
            yield q
        finally:
            async with self._lock:
                self._subscribers.discard(q)
