import secrets
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import hash_api_key, key_prefix, require_role
from app.db import get_session
from app.models import ApiKey, ApiKeyRole
from app.schemas.api_key import ApiKeyCreate, ApiKeyCreated, ApiKeyRead

router = APIRouter(dependencies=[Depends(require_role(ApiKeyRole.admin))])


@router.post("", response_model=ApiKeyCreated, status_code=status.HTTP_201_CREATED)
async def create_api_key(
    payload: ApiKeyCreate,
    session: AsyncSession = Depends(get_session),
) -> ApiKeyCreated:
    raw_key = secrets.token_urlsafe(32)
    api_key = ApiKey(
        name=payload.name,
        role=payload.role,
        key_prefix=key_prefix(raw_key),
        key_hash=hash_api_key(raw_key),
    )
    session.add(api_key)
    await session.commit()
    await session.refresh(api_key)
    return ApiKeyCreated(
        id=api_key.id,
        name=api_key.name,
        role=api_key.role,
        created_at=api_key.created_at,
        key=raw_key,
    )


@router.get("", response_model=list[ApiKeyRead])
async def list_api_keys(
    session: AsyncSession = Depends(get_session),
) -> list[ApiKeyRead]:
    rows = (await session.execute(select(ApiKey).order_by(ApiKey.created_at))).scalars().all()
    return [
        ApiKeyRead(
            id=row.id,
            name=row.name,
            role=row.role,
            created_at=row.created_at,
        )
        for row in rows
    ]


@router.delete("/{api_key_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_api_key(
    api_key_id: UUID,
    session: AsyncSession = Depends(get_session),
) -> None:
    api_key = await session.get(ApiKey, api_key_id)
    if api_key is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="API key not found",
        )
    await session.delete(api_key)
    await session.commit()
