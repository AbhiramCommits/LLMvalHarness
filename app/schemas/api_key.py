from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field

from app.models.enums import ApiKeyRole


class ApiKeyCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    role: ApiKeyRole


class ApiKeyRead(BaseModel):
    id: UUID
    name: str
    role: ApiKeyRole
    created_at: datetime


class ApiKeyCreated(ApiKeyRead):
    key: str
