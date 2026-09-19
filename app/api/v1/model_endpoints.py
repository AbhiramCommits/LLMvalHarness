from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import require_role
from app.db import get_session
from app.models import ApiKeyRole
from app.models.model_endpoint import ModelEndpoint
from app.schemas.model_endpoint import ModelEndpointCreate, ModelEndpointRead

router = APIRouter(dependencies=[Depends(require_role(ApiKeyRole.admin))])


@router.post("", response_model=ModelEndpointRead, status_code=status.HTTP_201_CREATED)
async def create_model_endpoint(
    payload: ModelEndpointCreate,
    session: AsyncSession = Depends(get_session),
) -> ModelEndpoint:
    endpoint = ModelEndpoint(**payload.model_dump())
    session.add(endpoint)
    await session.commit()
    await session.refresh(endpoint)
    return endpoint


@router.get("", response_model=list[ModelEndpointRead])
async def list_model_endpoints(
    session: AsyncSession = Depends(get_session),
) -> list[ModelEndpoint]:
    result = await session.execute(select(ModelEndpoint).order_by(ModelEndpoint.created_at))
    return list(result.scalars().all())


@router.get("/{endpoint_id}", response_model=ModelEndpointRead)
async def get_model_endpoint(
    endpoint_id: UUID,
    session: AsyncSession = Depends(get_session),
) -> ModelEndpoint:
    endpoint = await session.get(ModelEndpoint, endpoint_id)
    if endpoint is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Model endpoint not found",
        )
    return endpoint
