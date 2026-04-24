"""Runtime configuration loaded from environment variables."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    token: str
    host: str
    port: int
    replay_dir: Path
    keepalive_seconds: float
    debug_log_dir: Path | None
    otel_traces_file: Path | None
    adapters_replay_from_start: bool

    @classmethod
    def from_env(cls) -> "Settings":
        def _opt_path(name: str) -> Path | None:
            v = os.environ.get(name)
            return Path(v) if v else None

        return cls(
            token=os.environ.get("PIXEL_AGENTS_TOKEN", "dev-token-change-me"),
            host=os.environ.get("PIXEL_AGENTS_HOST", "0.0.0.0"),
            port=int(os.environ.get("PIXEL_AGENTS_PORT", "8765")),
            replay_dir=Path(os.environ.get("PIXEL_AGENTS_REPLAY_DIR", "./data/replay")),
            keepalive_seconds=float(os.environ.get("PIXEL_AGENTS_KEEPALIVE", "30")),
            debug_log_dir=_opt_path("PIXEL_AGENTS_DEBUG_LOG_DIR"),
            otel_traces_file=_opt_path("PIXEL_AGENTS_OTEL_TRACES_FILE"),
            adapters_replay_from_start=os.environ.get(
                "PIXEL_AGENTS_ADAPTERS_REPLAY_FROM_START", "false"
            ).lower()
            in ("1", "true", "yes"),
        )


settings = Settings.from_env()
