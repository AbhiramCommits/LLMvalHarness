from fastapi import APIRouter

from app.api.v1 import dead_letters, eval_runs, model_endpoints, task_sets

api_router = APIRouter()
api_router.include_router(task_sets.router, prefix="/task-sets", tags=["task-sets"])
api_router.include_router(model_endpoints.router, prefix="/models", tags=["models"])
api_router.include_router(eval_runs.router, prefix="/eval-runs", tags=["eval-runs"])
api_router.include_router(dead_letters.router, prefix="/dead-letters", tags=["dead-letters"])
