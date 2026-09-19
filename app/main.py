from fastapi import FastAPI
from fastapi.responses import JSONResponse, Response
from prometheus_client import CONTENT_TYPE_LATEST
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from app.api.v1.router import api_router
from app.artifacts import get_redis
from app.config import get_settings
from app.logging import configure_logging
from app.metrics import render_metrics
from app.middleware import ApiKeyMiddleware, RequestContextMiddleware
from app.schemas.common import CheckStatus, HealthStatus, ReadyStatus


def create_app() -> FastAPI:
    configure_logging()
    settings = get_settings()
    application = FastAPI(
        title=settings.app_name,
        version="0.1.0",
        debug=settings.debug,
    )
    application.add_middleware(RequestContextMiddleware)
    application.add_middleware(ApiKeyMiddleware)
    application.include_router(api_router, prefix="/v1")

    @application.get("/healthz", response_model=HealthStatus)
    async def healthz() -> HealthStatus:
        return HealthStatus(status="ok")

    @application.get("/readyz")
    async def readyz() -> JSONResponse:
        checks = await _readiness_checks()
        ready = ReadyStatus(
            status="ok" if all(c.status == "ok" for c in checks.values()) else "degraded",
            checks=checks,
        )
        status_code = 200 if ready.status == "ok" else 503
        return JSONResponse(ready.model_dump(), status_code=status_code)

    @application.get("/metrics")
    async def metrics() -> Response:
        return Response(render_metrics(), media_type=CONTENT_TYPE_LATEST)

    return application


async def _readiness_checks() -> dict[str, CheckStatus]:
    checks: dict[str, CheckStatus] = {}
    # A fresh engine per check: pooled asyncpg connections are bound to the
    # event loop that created them, and readiness probes may run on any loop.
    try:
        probe_engine = create_async_engine(
            get_settings().database_url,
            poolclass=NullPool,
        )
        try:
            async with probe_engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
        finally:
            await probe_engine.dispose()
        checks["postgres"] = CheckStatus(status="ok")
    except Exception as exc:  # pragma: no cover - depends on infra state
        checks["postgres"] = CheckStatus(status="down", error=str(exc))
    try:
        await get_redis().ping()
        checks["redis"] = CheckStatus(status="ok")
        checks["broker"] = CheckStatus(status="ok")
    except Exception as exc:  # pragma: no cover - depends on infra state
        checks["redis"] = CheckStatus(status="down", error=str(exc))
        checks["broker"] = CheckStatus(status="down", error=str(exc))
    return checks


app = create_app()
