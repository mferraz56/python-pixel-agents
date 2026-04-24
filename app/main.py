"""FastAPI application entrypoint for pixel-agents-python."""
from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from .api import hooks, viewer
from .config import settings
from .services.adapter_supervisor import AdapterSupervisor
from .services.event_bus import EventBus
from .services.replay_store import ReplayStore
from .services.session_aggregator import SessionAggregator

VIEWER_DIR = Path(__file__).resolve().parent.parent / "viewer-ui"
VIEWER_DIST = VIEWER_DIR / "dist"


@asynccontextmanager
async def _lifespan(app: FastAPI):
    supervisor: AdapterSupervisor = app.state.supervisor
    supervisor.start(
        debug_log_dir=settings.debug_log_dir,
        otel_traces_file=settings.otel_traces_file,
        from_start=settings.adapters_replay_from_start,
    )
    try:
        yield
    finally:
        await supervisor.stop()


def create_app() -> FastAPI:
    app = FastAPI(title="pixel-agents-python", version="0.2.0", lifespan=_lifespan)
    # Eager init of singletons (no async needed). Adapter background tasks
    # are owned by the lifespan handler so httpx.ASGITransport users that
    # skip lifespan still get a fully functional API surface.
    app.state.bus = EventBus()
    app.state.aggregator = SessionAggregator()
    app.state.replay = ReplayStore(settings.replay_dir)
    app.state.supervisor = AdapterSupervisor(
        aggregator=app.state.aggregator,
        bus=app.state.bus,
        replay=app.state.replay,
    )
    app.include_router(hooks.router)
    app.include_router(viewer.router)
    return app


app = create_app()


@app.get("/api/health")
async def health() -> JSONResponse:
    return JSONResponse({"status": "ok", "version": app.version})


@app.get("/")
async def root() -> RedirectResponse:
    return RedirectResponse(url="/viewer/")


# Prefer the built SPA bundle when available; otherwise fall back to the
# placeholder source directory so `viewer-ui/index.html` still works in dev.
_viewer_root = VIEWER_DIST if VIEWER_DIST.exists() else VIEWER_DIR
if _viewer_root.exists():
    app.mount("/viewer", StaticFiles(directory=str(_viewer_root), html=True), name="viewer")
