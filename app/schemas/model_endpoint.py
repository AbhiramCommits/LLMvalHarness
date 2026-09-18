from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import Provider


class ModelEndpointCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    provider: Provider
    model_id: str = Field(min_length=1)
    model_version: str = Field(min_length=1)
    base_url: str | None = None


class ModelEndpointRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    provider: Provider
    model_id: str
    model_version: str
    base_url: str | None
    created_at: datetime
