from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import require_role
from app.db import get_session
from app.models import ApiKeyRole, EvalRun, EvalRunStatus, RunItem, RunItemStatus
from app.schemas.eval_run import ReplayResponse
from app.workers import execute

router = APIRouter(dependencies=[Depends(require_role(ApiKeyRole.admin))])


@router.post(
    "/{run_item_id}/replay",
    response_model=ReplayResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def replay_run_item(
    run_item_id: UUID,
    session: AsyncSession = Depends(get_session),
) -> ReplayResponse:
    run_item = await session.get(RunItem, run_item_id)
    if run_item is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Run item not found",
        )
    await session.refresh(run_item)
    if run_item.status != RunItemStatus.dead_lettered:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"run item is {run_item.status.value}, not dead_lettered",
        )
    run = await session.get(EvalRun, run_item.eval_run_id)
    if run is not None and run.status == EvalRunStatus.cancelled:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="eval run is cancelled",
        )
    await session.execute(
        update(RunItem)
        .where(
            RunItem.id == run_item_id,
            RunItem.status == RunItemStatus.dead_lettered,
        )
        .values(
            status=RunItemStatus.queued,
            error=None,
            updated_at=func.now(),
        )
    )
    await session.execute(
        update(EvalRun)
        .where(
            EvalRun.id == run_item.eval_run_id,
            EvalRun.status == EvalRunStatus.failed,
        )
        .values(
            status=EvalRunStatus.running,
            completed_at=None,
            error=None,
        )
    )
    await session.commit()
    execute.enqueue_run_item(str(run_item_id))
    return ReplayResponse(run_item_id=run_item_id, status=RunItemStatus.queued)
