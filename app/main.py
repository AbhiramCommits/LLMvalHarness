from fastapi import FastAPI

from app.api.v1.router import api_router
from app.config import get_settings
from app.schemas.common import HealthStatus


def create_app() -> FastAPI:
    settings = get_settings()
    application = FastAPI(
        title=settings.app_name,
        version="0.1.0",
        debug=settings.debug,
    )
    application.include_router(api_router, prefix="/v1")

    @application.get("/healthz", response_model=HealthStatus)
    async def healthz() -> HealthStatus:
        return HealthStatus(status="ok")

    return application


app = create_app()
