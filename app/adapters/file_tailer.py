"""Shared async file tailer.

Watches a single file by path; reads any appended bytes line-by-line and
yields each complete line. Survives file truncation and rotation by reopening
when the inode/size shrinks.
"""
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path


async def tail_lines(
    path: Path,
    *,
    poll_seconds: float = 0.5,
    from_start: bool = True,
) -> AsyncIterator[str]:
    """Yield successive newline-terminated records appended to ``path``.

    The file may not exist yet. Lines are decoded as UTF-8 with replacement.
    """
    fp = None
    pending = ""
    try:
        while True:
            if fp is None:
                if not path.exists():
                    await asyncio.sleep(poll_seconds)
                    continue
                fp = await asyncio.to_thread(path.open, "rb")
                if not from_start:
                    await asyncio.to_thread(fp.seek, 0, 2)

            chunk = await asyncio.to_thread(fp.read, 65536)
            if chunk:
                pending += chunk.decode("utf-8", errors="replace")
                while "\n" in pending:
                    line, pending = pending.split("\n", 1)
                    line = line.rstrip("\r")
                    if line:
                        yield line
                continue

            # No new data — check for truncation/rotation.
            try:
                cur_pos = await asyncio.to_thread(fp.tell)
                size = await asyncio.to_thread(lambda: path.stat().st_size)
            except FileNotFoundError:
                await asyncio.to_thread(fp.close)
                fp = None
                pending = ""
                await asyncio.sleep(poll_seconds)
                continue
            if size < cur_pos:
                await asyncio.to_thread(fp.close)
                fp = None
                pending = ""
                continue
            await asyncio.sleep(poll_seconds)
    finally:
        if fp is not None:
            await asyncio.to_thread(fp.close)
