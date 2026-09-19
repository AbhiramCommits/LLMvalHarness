import uuid
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import require_role
from app.db import get_session
from app.models import (
    ApiKeyRole,
    EvalRun,
    EvalRunStatus,
    ModelEndpoint,
    RunItem,
    RunItemStatus,
    Task,
    TaskSet,
)
from app.schemas.eval_run import (
    DeadLetterRead,
    EvalRunAccepted,
    EvalRunCreate,
    EvalRunRead,
    ModelAggregate,
    StatusCounts,
)
from app.workers import execute

router = APIRouter(dependencies=[Depends(require_role(ApiKeyRole.admin))])


@router.post("", response_model=EvalRunAccepted, status_code=status.HTTP_202_ACCEPTED)
async def create_eval_run(
    payload: EvalRunCreate,
    session: AsyncSession = Depends(get_session),
) -> EvalRunAccepted:
    task_set = await session.get(TaskSet, payload.task_set_id)
    if task_set is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Task set not found",
        )

    endpoint_ids = list(dict.fromkeys(payload.model_endpoint_ids))
    endpoints = (
        (await session.execute(select(ModelEndpoint).where(ModelEndpoint.id.in_(endpoint_ids))))
        .scalars()
        .all()
    )
    found_ids = {endpoint.id for endpoint in endpoints}
    missing = [endpoint_id for endpoint_id in endpoint_ids if endpoint_id not in found_ids]
    if missing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"unknown model endpoint ids: {', '.join(str(m) for m in missing)}",
        )

    tasks = (
        (await session.execute(select(Task).where(Task.task_set_id == payload.task_set_id)))
        .scalars()
        .all()
    )

    now = datetime.now(UTC)
    run = EvalRun(
        id=uuid.uuid4(),
        task_set_id=payload.task_set_id,
        status=EvalRunStatus.running,
        started_at=now,
    )
    if not tasks or not endpoints:
        run.status = EvalRunStatus.completed
        run.completed_at = now
        session.add(run)
        await session.commit()
        await session.refresh(run)
        return EvalRunAccepted(id=run.id, status=run.status)

    run_items = [
        RunItem(eval_run_id=run.id, task_id=task.id, model_endpoint_id=endpoint.id)
        for task in tasks
        for endpoint in endpoints
    ]
    session.add(run)
    session.add_all(run_items)
    await session.flush()
    run_item_ids = [str(item.id) for item in run_items]
    await session.commit()

    for run_item_id in run_item_ids:
        execute.enqueue_run_item(run_item_id)
    return EvalRunAccepted(id=run.id, status=run.status)


@router.get("/{eval_run_id}", response_model=EvalRunRead)
async def get_eval_run(
    eval_run_id: UUID,
    session: AsyncSession = Depends(get_session),
) -> EvalRunRead:
    run = await session.get(EvalRun, eval_run_id)
    if run is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Eval run not found",
        )
    await session.refresh(run)
    return await _read_eval_run(session, run)


@router.post("/{eval_run_id}/cancel", response_model=EvalRunRead)
async def cancel_eval_run(
    eval_run_id: UUID,
    session: AsyncSession = Depends(get_session),
) -> EvalRunRead:
    run = await session.get(EvalRun, eval_run_id)
    if run is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Eval run not found",
        )
    await session.refresh(run)
    if run.status in (EvalRunStatus.completed, EvalRunStatus.failed):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"eval run already {run.status.value}",
        )
    if run.status != EvalRunStatus.cancelled:
        await session.execute(
            update(EvalRun)
            .where(
                EvalRun.id == eval_run_id,
                EvalRun.status.in_([EvalRunStatus.pending, EvalRunStatus.running]),
            )
            .values(status=EvalRunStatus.cancelled, completed_at=func.now())
        )
        await session.execute(
            update(RunItem)
            .where(
                RunItem.eval_run_id == eval_run_id,
                RunItem.status == RunItemStatus.queued,
            )
            .values(
                status=RunItemStatus.failed,
                error="eval run cancelled",
                updated_at=func.now(),
            )
        )
        await session.commit()
        await session.refresh(run)
    return await _read_eval_run(session, run)


@router.get("/{eval_run_id}/dead-letters", response_model=list[DeadLetterRead])
async def list_dead_letters(
    eval_run_id: UUID,
    session: AsyncSession = Depends(get_session),
) -> list[DeadLetterRead]:
    run = await session.get(EvalRun, eval_run_id)
    if run is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Eval run not found",
        )
    rows = (
        (
            await session.execute(
                select(RunItem)
                .where(
                    RunItem.eval_run_id == eval_run_id,
                    RunItem.status == RunItemStatus.dead_lettered,
                )
                .order_by(RunItem.created_at)
            )
        )
        .scalars()
        .all()
    )
    return [
        DeadLetterRead(
            run_item_id=row.id,
            eval_run_id=row.eval_run_id,
            task_id=row.task_id,
            model_endpoint_id=row.model_endpoint_id,
            attempt_count=row.attempt_count,
            error=row.error,
            created_at=row.created_at,
        )
        for row in rows
    ]


async def _read_eval_run(session: AsyncSession, run: EvalRun) -> EvalRunRead:
    count_rows = (
        await session.execute(
            select(RunItem.model_endpoint_id, RunItem.status, func.count())
            .where(RunItem.eval_run_id == run.id)
            .group_by(RunItem.model_endpoint_id, RunItem.status)
        )
    ).all()
    agg_rows = (
        await session.execute(
            select(
                RunItem.model_endpoint_id,
                func.avg(RunItem.latency_ms),
                func.sum(RunItem.cost_usd),
            )
            .where(RunItem.eval_run_id == run.id)
            .group_by(RunItem.model_endpoint_id)
        )
    ).all()

    model_ids = {row[0] for row in count_rows} | {row[0] for row in agg_rows}
    endpoints_by_id: dict[UUID, ModelEndpoint] = {}
    if model_ids:
        endpoints = (
            (await session.execute(select(ModelEndpoint).where(ModelEndpoint.id.in_(model_ids))))
            .scalars()
            .all()
        )
        endpoints_by_id = {endpoint.id: endpoint for endpoint in endpoints}

    counts_by_model: dict[UUID, dict[RunItemStatus, int]] = {}
    for model_id, item_status, count in count_rows:
        counts_by_model.setdefault(model_id, {})[item_status] = count
    latency_by_model = {row[0]: row[1] for row in agg_rows}
    cost_by_model = {row[0]: row[2] for row in agg_rows}

    aggregates = []
    for model_id in model_ids:
        endpoint = endpoints_by_id.get(model_id)
        per_status = counts_by_model.get(model_id, {})
        mean_latency = latency_by_model.get(model_id)
        total_cost = cost_by_model.get(model_id) or Decimal("0")
        aggregates.append(
            ModelAggregate(
                model_endpoint_id=model_id,
                name=endpoint.name if endpoint else "unknown",
                counts=StatusCounts(
                    queued=per_status.get(RunItemStatus.queued, 0),
                    running=per_status.get(RunItemStatus.running, 0),
                    succeeded=per_status.get(RunItemStatus.succeeded, 0),
                    failed=per_status.get(RunItemStatus.failed, 0),
                    dead_lettered=per_status.get(RunItemStatus.dead_lettered, 0),
                ),
                mean_latency_ms=float(mean_latency) if mean_latency is not None else None,
                total_cost_usd=float(total_cost),
            )
        )
    aggregates.sort(key=lambda aggregate: aggregate.name)

    return EvalRunRead(
        id=run.id,
        task_set_id=run.task_set_id,
        status=run.status,
        created_at=run.created_at,
        started_at=run.started_at,
        completed_at=run.completed_at,
        total_cost_usd=float(run.total_cost_usd),
        total_tokens=run.total_tokens,
        models=aggregates,
    )
