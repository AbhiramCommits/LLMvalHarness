from decimal import Decimal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.artifacts import get_artifact
from app.db import get_session, updated_rowcount
from app.models import Grade, ReviewItem, ReviewStatus, RunItem, Task
from app.schemas.review import (
    GradeRead,
    ResolveRequest,
    ReviewItemRead,
    ReviewQueueItem,
    ReviewStats,
)

router = APIRouter()


@router.get("", response_model=list[ReviewQueueItem])
async def list_review_queue(
    review_status: ReviewStatus = Query(default=ReviewStatus.open, alias="status"),
    capability: str | None = None,
    limit: int = Query(default=50, ge=1, le=500),
    session: AsyncSession = Depends(get_session),
) -> list[ReviewQueueItem]:
    stmt = (
        select(ReviewItem, RunItem, Task)
        .join(RunItem, RunItem.id == ReviewItem.run_item_id)
        .join(Task, Task.id == RunItem.task_id)
        .where(ReviewItem.status == review_status)
    )
    if capability:
        stmt = stmt.where(Task.capability == capability)
    stmt = stmt.order_by(ReviewItem.created_at.asc(), ReviewItem.id.asc()).limit(limit)
    rows = (await session.execute(stmt)).all()

    run_item_ids = [run_item.id for _, run_item, _ in rows]
    grades_by_item: dict[UUID, list[Grade]] = {}
    if run_item_ids:
        grade_rows = (
            (
                await session.execute(
                    select(Grade)
                    .where(Grade.run_item_id.in_(run_item_ids))
                    .order_by(Grade.created_at)
                )
            )
            .scalars()
            .all()
        )
        for grade_row in grade_rows:
            grades_by_item.setdefault(grade_row.run_item_id, []).append(grade_row)

    items: list[ReviewQueueItem] = []
    for review_item, run_item, task in rows:
        artifact = await get_artifact(run_item.id)
        model_output = None
        if artifact:
            model_output = (artifact.get("raw_response") or {}).get("text")
        items.append(
            ReviewQueueItem(
                review_item_id=review_item.id,
                run_item_id=run_item.id,
                reason=review_item.reason,
                status=review_item.status,
                capability=task.capability,
                prompt=task.prompt,
                expected_output=task.expected_output,
                model_output=model_output,
                grades=[
                    GradeRead(
                        grader=grade_row.grader,
                        score=float(grade_row.score) if grade_row.score is not None else None,
                        passed=grade_row.passed,
                        rubric_scores=grade_row.rubric_scores,
                        rationale=grade_row.rationale,
                    )
                    for grade_row in grades_by_item.get(run_item.id, [])
                ],
                created_at=review_item.created_at,
            )
        )
    return items


@router.get("/stats", response_model=ReviewStats)
async def review_queue_stats(
    session: AsyncSession = Depends(get_session),
) -> ReviewStats:
    rows = (
        await session.execute(select(ReviewItem.status, func.count()).group_by(ReviewItem.status))
    ).all()
    counts = {item_status: 0 for item_status in ReviewStatus}
    for item_status, count in rows:
        counts[item_status] = count

    mean_seconds = await session.scalar(
        select(
            func.avg(func.extract("epoch", ReviewItem.resolved_at - ReviewItem.claimed_at))
        ).where(
            ReviewItem.resolved_at.isnot(None),
            ReviewItem.claimed_at.isnot(None),
        )
    )
    return ReviewStats(
        open=counts[ReviewStatus.open],
        claimed=counts[ReviewStatus.claimed],
        resolved=counts[ReviewStatus.resolved],
        mean_time_to_resolve_ms=(float(mean_seconds) * 1000 if mean_seconds is not None else None),
    )


@router.post("/{review_item_id}/claim", response_model=ReviewItemRead)
async def claim_review_item(
    review_item_id: UUID,
    session: AsyncSession = Depends(get_session),
) -> ReviewItemRead:
    # Conditional UPDATE is the atomic primitive: exactly one concurrent
    # claimer observes rowcount=1.
    result = await session.execute(
        update(ReviewItem)
        .where(ReviewItem.id == review_item_id, ReviewItem.status == ReviewStatus.open)
        .values(status=ReviewStatus.claimed, claimed_at=func.now())
    )
    await session.commit()
    if updated_rowcount(result) == 1:
        review_item = await session.get(ReviewItem, review_item_id)
        assert review_item is not None
        await session.refresh(review_item)
        return _review_item_read(review_item)
    review_item = await session.get(ReviewItem, review_item_id)
    if review_item is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Review item not found",
        )
    raise HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail=f"review item is {review_item.status.value}, cannot claim",
    )


@router.post("/{review_item_id}/resolve", response_model=ReviewItemRead)
async def resolve_review_item(
    review_item_id: UUID,
    payload: ResolveRequest,
    session: AsyncSession = Depends(get_session),
) -> ReviewItemRead:
    label = Decimal(str(round(payload.label, 3)))
    result = await session.execute(
        update(ReviewItem)
        .where(ReviewItem.id == review_item_id, ReviewItem.status == ReviewStatus.claimed)
        .values(
            status=ReviewStatus.resolved,
            reviewer_label=label,
            reviewer_notes=payload.notes,
            resolved_at=func.now(),
        )
    )
    await session.commit()
    if updated_rowcount(result) == 1:
        review_item = await session.get(ReviewItem, review_item_id)
        assert review_item is not None
        await session.refresh(review_item)
        return _review_item_read(review_item)
    review_item = await session.get(ReviewItem, review_item_id)
    if review_item is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Review item not found",
        )
    raise HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail=f"review item is {review_item.status.value}; resolve requires claimed",
    )


def _review_item_read(review_item: ReviewItem) -> ReviewItemRead:
    return ReviewItemRead(
        id=review_item.id,
        run_item_id=review_item.run_item_id,
        reason=review_item.reason,
        status=review_item.status,
        reviewer_label=(
            float(review_item.reviewer_label) if review_item.reviewer_label is not None else None
        ),
        reviewer_notes=review_item.reviewer_notes,
        claimed_at=review_item.claimed_at,
        resolved_at=review_item.resolved_at,
        created_at=review_item.created_at,
    )
