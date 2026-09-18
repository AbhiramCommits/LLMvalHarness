from datetime import datetime
from uuid import UUID

from pydantic import BaseModel


class LeaderboardRow(BaseModel):
    model_endpoint_id: UUID
    name: str
    mean_score: float
    pass_rate: float
    n: int
    mean_latency_ms: float | None
    p95_latency_ms: float | None
    total_cost_usd: float
    cost_per_1k: float


class HistoryPoint(BaseModel):
    model_version: str
    eval_run_id: UUID
    evaluated_at: datetime
    mean_score: float
    n: int


class CapabilityRow(BaseModel):
    capability: str
    mean_score: float
    pass_rate: float
    n: int
