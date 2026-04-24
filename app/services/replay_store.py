"""Append-only JSONL replay store.

v1 persistence is intentionally simple: one file per provider+session,
one normalized envelope per line. A future ReplayStore implementation can
swap this for SQLite/DuckDB/object-storage without touching callers.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

from ..protocol.domain_events import EventEnvelope


class ReplayStore:
    def __init__(self, base_dir: Path) -> None:
        self._base = base_dir
        self._base.mkdir(parents=True, exist_ok=True)
        self._lock = asyncio.Lock()

    def _path_for(self, provider_id: str, session_id: str) -> Path:
        safe_provider = "".join(c if c.isalnum() or c in "-_" else "_" for c in provider_id)
        safe_session = "".join(c if c.isalnum() or c in "-_" else "_" for c in session_id)
        return self._base / f"{safe_provider}__{safe_session}.jsonl"

    async def append(self, envelope: EventEnvelope) -> None:
        path = self._path_for(envelope.provider_id, envelope.session_id)
        line = envelope.model_dump_json() + "\n"
        async with self._lock:
            await asyncio.to_thread(self._write, path, line)

    @staticmethod
    def _write(path: Path, line: str) -> None:
        with path.open("a", encoding="utf-8") as f:
            f.write(line)

    def list_sessions(self) -> list[tuple[str, str]]:
        out: list[tuple[str, str]] = []
        for p in self._base.glob("*.jsonl"):
            stem = p.stem
            if "__" not in stem:
                continue
            provider, session = stem.split("__", 1)
            out.append((provider, session))
        return out

    def read(self, provider_id: str, session_id: str) -> list[EventEnvelope]:
        path = self._path_for(provider_id, session_id)
        if not path.exists():
            return []
        envelopes: list[EventEnvelope] = []
        with path.open("r", encoding="utf-8") as f:
            for raw in f:
                raw = raw.strip()
                if not raw:
                    continue
                envelopes.append(EventEnvelope.model_validate_json(raw))
        return envelopes
