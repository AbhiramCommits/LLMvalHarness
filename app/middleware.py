"""ASGI middleware: request context (request_id) and API key authentication."""

import uuid

import structlog
from fastapi import Request
from sqlalchemy import select
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from app.auth import AuthContext, key_prefix, verify_api_key
from app.db import SessionFactory
from app.models import ApiKey

_EXEMPT_PATHS = {
    "/healthz",
    "/readyz",
    "/metrics",
    "/openapi.json",
    "/docs",
    "/redoc",
    "/favicon.ico",
}

logger = structlog.get_logger(__name__)


class RequestContextMiddleware(BaseHTTPMiddleware):
    """Bind a request_id to structlog contextvars for the request duration."""

    async def dispatch(self, request: Request, call_next):
        request_id = request.headers.get("X-Request-Id") or uuid.uuid4().hex
        structlog.contextvars.bind_contextvars(request_id=request_id)
        try:
            response = await call_next(request)
            response.headers["X-Request-Id"] = request_id
            return response
        finally:
            structlog.contextvars.clear_contextvars()


class ApiKeyMiddleware(BaseHTTPMiddleware):
    """Authenticate requests against hashed keys in Postgres.

    Successful auth sets ``request.state.auth`` to an :class:`AuthContext`.
    Health/metrics endpoints are exempt.
    """

    async def dispatch(self, request: Request, call_next):
        if request.method == "OPTIONS" or request.url.path in _EXEMPT_PATHS:
            return await call_next(request)

        raw_key = _extract_key(request)
        if raw_key is None:
            logger.warning("missing_api_key", path=request.url.path)
            return JSONResponse(
                {"detail": "Missing API key"},
                status_code=401,
            )
        context = await _authenticate(request, raw_key)
        if context is None:
            logger.warning("invalid_api_key", path=request.url.path)
            return JSONResponse(
                {"detail": "Invalid API key"},
                status_code=401,
            )
        request.state.auth = context
        structlog.contextvars.bind_contextvars(api_key=context.name)
        return await call_next(request)


def _extract_key(request: Request) -> str | None:
    header = request.headers.get("X-API-Key")
    if header:
        return header.strip()
    authorization = request.headers.get("Authorization", "")
    if authorization.startswith("Bearer "):
        return authorization.removeprefix("Bearer ").strip()
    return None


async def _authenticate(request: Request, raw_key: str) -> AuthContext | None:
    factory = getattr(request.app.state, "auth_session_factory", None) or SessionFactory
    async with factory() as session:
        candidates = (
            (await session.execute(select(ApiKey).where(ApiKey.key_prefix == key_prefix(raw_key))))
            .scalars()
            .all()
        )
    for row in candidates:
        if verify_api_key(raw_key, row.key_hash):
            return AuthContext(api_key_id=row.id, name=row.name, role=row.role)
    return None
