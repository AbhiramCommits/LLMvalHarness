from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import GraderType


class TaskCreate(BaseModel):
    prompt: str = Field(min_length=1)
    capability: str = Field(min_length=1)
    expected_output: str | None = None
    grader_type: GraderType
    grader_config: dict[str, Any] | None = None


class TaskRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    task_set_id: UUID
    prompt: str
    capability: str
    expected_output: str | None
    grader_type: GraderType
    grader_config: dict[str, Any] | None
    created_at: datetime
