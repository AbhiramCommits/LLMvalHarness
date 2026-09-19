from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import require_role
from app.db import get_session
from app.models import ApiKeyRole
from app.models.task import Task, TaskSet
from app.schemas.task import TaskCreate, TaskRead
from app.schemas.task_set import TaskSetCreate, TaskSetRead

router = APIRouter(dependencies=[Depends(require_role(ApiKeyRole.admin))])


@router.post("", response_model=TaskSetRead, status_code=status.HTTP_201_CREATED)
async def create_task_set(
    payload: TaskSetCreate,
    session: AsyncSession = Depends(get_session),
) -> TaskSet:
    task_set = TaskSet(name=payload.name, description=payload.description)
    session.add(task_set)
    await session.commit()
    await session.refresh(task_set)
    return task_set


@router.get("", response_model=list[TaskSetRead])
async def list_task_sets(
    session: AsyncSession = Depends(get_session),
) -> list[TaskSet]:
    result = await session.execute(select(TaskSet).order_by(TaskSet.created_at))
    return list(result.scalars().all())


@router.get("/{task_set_id}", response_model=TaskSetRead)
async def get_task_set(
    task_set_id: UUID,
    session: AsyncSession = Depends(get_session),
) -> TaskSet:
    task_set = await session.get(TaskSet, task_set_id)
    if task_set is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Task set not found",
        )
    return task_set


@router.post(
    "/{task_set_id}/tasks",
    response_model=TaskRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_task(
    task_set_id: UUID,
    payload: TaskCreate,
    session: AsyncSession = Depends(get_session),
) -> Task:
    task_set = await session.get(TaskSet, task_set_id)
    if task_set is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Task set not found",
        )
    task = Task(task_set_id=task_set_id, **payload.model_dump())
    session.add(task)
    await session.commit()
    await session.refresh(task)
    return task
