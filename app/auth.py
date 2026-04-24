"""Token-based authentication helper (constant-time compare)."""
from __future__ import annotations

import hmac

from fastapi import HTTPException, Request, status

from .config import settings


def _extract_token(request: Request) -> str | None:
    auth = request.headers.get("authorization")
    if auth and auth.lower().startswith("bearer "):
        return auth[7:].strip()
    qtoken = request.query_params.get("token")
    if qtoken:
        return qtoken
    return None


def require_token(request: Request) -> None:
    provided = _extract_token(request) or ""
    if not hmac.compare_digest(provided, settings.token):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid token")
