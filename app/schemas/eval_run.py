from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field

from app.models.enums import EvalRunStatus, RunItemStatus


class EvalRunCreate(BaseModel):
    task_set_id: UUID
    model_endpoint_ids: list[UUID] = Field(min_length=1)


class EvalRunAccepted(BaseModel):
    id: UUID
    status: EvalRunStatus


class StatusCounts(BaseModel):
    queued: int = 0
    running: int = 0
    succeeded: int = 0
    failed: int = 0
    dead_lettered: int = 0


class ModelAggregate(BaseModel):
    model_endpoint_id: UUID
    name: str
    counts: StatusCounts
    mean_latency_ms: float | None
    total_cost_usd: float


class EvalRunRead(BaseModel):
    id: UUID
    task_set_id: UUID
    status: EvalRunStatus
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    total_cost_usd: float
    total_tokens: int
    models: list[ModelAggregate]


class DeadLetterRead(BaseModel):
    run_item_id: UUID
    eval_run_id: UUID
    task_id: UUID
    model_endpoint_id: UUID
    attempt_count: int
    error: str | None
    created_at: datetime


class ReplayResponse(BaseModel):
    run_item_id: UUID
    status: RunItemStatus
