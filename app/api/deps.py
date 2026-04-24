"""Shared dependency accessors for app.state-managed singletons."""
from __future__ import annotations

from fastapi import FastAPI

from ..services.event_bus import EventBus
from ..services.replay_store import ReplayStore
from ..services.session_aggregator import SessionAggregator


def get_bus(app: FastAPI) -> EventBus:
    return app.state.bus


def get_aggregator(app: FastAPI) -> SessionAggregator:
    return app.state.aggregator


def get_replay_store(app: FastAPI) -> ReplayStore:
    return app.state.replay
