from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field

from app.models.enums import GraderKind, ReviewReason, ReviewStatus


class GradeRead(BaseModel):
    grader: GraderKind
    score: float | None
    passed: bool | None
    rubric_scores: dict[str, Any] | None
    rationale: str | None


class ReviewQueueItem(BaseModel):
    review_item_id: UUID
    run_item_id: UUID
    reason: ReviewReason
    status: ReviewStatus
    capability: str
    prompt: str
    expected_output: str | None
    model_output: str | None
    grades: list[GradeRead]
    created_at: datetime


class ReviewItemRead(BaseModel):
    id: UUID
    run_item_id: UUID
    reason: ReviewReason
    status: ReviewStatus
    reviewer_label: float | None
    reviewer_notes: str | None
    claimed_at: datetime | None
    resolved_at: datetime | None
    created_at: datetime


class ResolveRequest(BaseModel):
    label: float = Field(ge=0.0, le=1.0)
    notes: str | None = None


class ReviewStats(BaseModel):
    open: int
    claimed: int
    resolved: int
    mean_time_to_resolve_ms: float | None
