"""Adapter supervisor: runs configured adapters as background tasks."""
from __future__ import annotations

import asyncio
import logging
import time
from collections import OrderedDict
from pathlib import Path

from ..adapters.copilot_debug_adapter import run_debug_log_adapter
from ..adapters.copilot_otel_adapter import run_otel_file_adapter
from ..protocol import domain_events as de
from ..protocol.viewer_messages import serialize
from .event_bus import EventBus
from .replay_store import ReplayStore
from .session_aggregator import SessionAggregator

log = logging.getLogger(__name__)

# Dedup window for cross-provider envelopes that share (session_id, span_id, kind).
# Both the OTel and debug-log adapters emit the same logical event for the same
# span; keep only the first one within this window.
_DEDUP_TTL_SECONDS = 300.0
_DEDUP_MAX_ENTRIES = 4096


class AdapterSupervisor:
    def __init__(
        self,
        *,
        aggregator: SessionAggregator,
        bus: EventBus,
        replay: ReplayStore,
    ) -> None:
        self._agg = aggregator
        self._bus = bus
        self._replay = replay
        self._tasks: list[asyncio.Task[None]] = []
        # key -> first-seen monotonic timestamp
        self._seen: OrderedDict[tuple[str, str, str], float] = OrderedDict()

    def _is_duplicate(self, env: de.EventEnvelope) -> bool:
        if not env.span_id:
            return False
        key = (env.session_id, env.span_id, env.event.kind)
        now = time.monotonic()
        # opportunistic eviction
        cutoff = now - _DEDUP_TTL_SECONDS
        while self._seen and next(iter(self._seen.values())) < cutoff:
            self._seen.popitem(last=False)
        if key in self._seen:
            return True
        self._seen[key] = now
        if len(self._seen) > _DEDUP_MAX_ENTRIES:
            self._seen.popitem(last=False)
        return False

    async def _ingest(self, env: de.EventEnvelope) -> None:
        try:
            if self._is_duplicate(env):
                return
            await self._replay.append(env)
            for msg in await self._agg.project(env):
                await self._bus.publish(serialize(msg))
        except Exception:
            log.exception("adapter ingestion failed for %s/%s", env.provider_id, env.session_id)

    def start(
        self,
        *,
        debug_log_dir: Path | None,
        otel_traces_file: Path | None,
        from_start: bool,
    ) -> None:
        if debug_log_dir is not None:
            log.info("starting copilot debug-log adapter on %s", debug_log_dir)
            self._tasks.append(
                asyncio.create_task(
                    run_debug_log_adapter(
                        debug_log_dir, self._ingest, from_start=from_start
                    ),
                    name="adapter:copilot-debug",
                )
            )
        if otel_traces_file is not None:
            log.info("starting copilot otel adapter on %s", otel_traces_file)
            self._tasks.append(
                asyncio.create_task(
                    run_otel_file_adapter(
                        otel_traces_file, self._ingest, from_start=from_start
                    ),
                    name="adapter:copilot-otel",
                )
            )

    async def stop(self) -> None:
        for t in self._tasks:
            t.cancel()
        for t in self._tasks:
            try:
                await t
            except (asyncio.CancelledError, Exception):
                pass
        self._tasks.clear()
