from fastapi import APIRouter

from app.api.v1 import model_endpoints, task_sets

api_router = APIRouter()
api_router.include_router(task_sets.router, prefix="/task-sets", tags=["task-sets"])
api_router.include_router(model_endpoints.router, prefix="/models", tags=["models"])
