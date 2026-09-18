import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from app.artifacts import set_artifact
from app.models import (
    EvalRun,
    EvalRunStatus,
    Grade,
    GraderKind,
    ModelEndpoint,
    Provider,
    ReviewItem,
    ReviewReason,
    ReviewStatus,
    RunItem,
    Task,
    TaskSet,
)
from app.models.enums import GraderType
from httpx import AsyncClient
from sqlalchemy import delete, func, update
from sqlalchemy.ext.asyncio import AsyncSession


async def _seed_review(
    session: AsyncSession,
    *,
    capability: str = "math",
    review_status: ReviewStatus = ReviewStatus.open,
    created_at: datetime | None = None,
    prompt: str = "What is 2+2?",
    output: str | None = "4",
    with_grades: bool = True,
    claimed_at: datetime | None = None,
    resolved_at: datetime | None = None,
) -> ReviewItem:
    task_set = TaskSet(id=uuid.uuid4(), name=f"ts-{uuid.uuid4()}")
    task = Task(
        id=uuid.uuid4(),
        task_set_id=task_set.id,
        prompt=prompt,
        capability=capability,
        grader_type=GraderType.exact_match,
        expected_output="4",
    )
    endpoint = ModelEndpoint(
        id=uuid.uuid4(),
        name=f"m-{uuid.uuid4()}",
        provider=Provider.local,
        model_id="fake-model",
        model_version="v1",
    )
    run = EvalRun(id=uuid.uuid4(), task_set_id=task_set.id, status=EvalRunStatus.running)
    item = RunItem(
        id=uuid.uuid4(),
        eval_run_id=run.id,
        task_id=task.id,
        model_endpoint_id=endpoint.id,
    )
    review = ReviewItem(
        id=uuid.uuid4(),
        run_item_id=item.id,
        reason=ReviewReason.low_confidence,
        status=review_status,
        created_at=created_at,
        claimed_at=claimed_at,
        resolved_at=resolved_at,
    )
    session.add_all([task_set, task, endpoint, run, item, review])
    if with_grades:
        session.add_all(
            [
                Grade(
                    run_item_id=item.id,
                    grader=GraderKind.deterministic,
                    score=Decimal("1.0"),
                    passed=True,
                    rationale="exact match",
                ),
                Grade(
                    run_item_id=item.id,
                    grader=GraderKind.llm_judge,
                    score=Decimal("0.5"),
                    passed=True,
                    rubric_scores={"accuracy": 0.5},
                    rationale="borderline",
                ),
            ]
        )
    await session.commit()
    if output is not None:
        await set_artifact(
            item.id,
            {"request": {}, "raw_response": {"text": output}, "judge_raw": None},
        )
    return review


async def test_list_empty(client: AsyncClient) -> None:
    response = await client.get("/v1/review-queue")
    assert response.status_code == 200
    assert response.json() == []


async def test_list_includes_prompt_output_and_grades(
    client: AsyncClient,
    session: AsyncSession,
) -> None:
    await _seed_review(session)
    response = await client.get("/v1/review-queue")
    assert response.status_code == 200
    items = response.json()
    assert len(items) == 1
    item = items[0]
    assert item["capability"] == "math"
    assert item["prompt"] == "What is 2+2?"
    assert item["model_output"] == "4"
    assert [g["grader"] for g in item["grades"]] == ["deterministic", "llm_judge"]
    assert [g["score"] for g in item["grades"]] == [1.0, 0.5]
    assert item["grades"][1]["rationale"] == "borderline"


async def test_status_filter(client: AsyncClient, session: AsyncSession) -> None:
    await _seed_review(session, review_status=ReviewStatus.resolved)

    response = await client.get("/v1/review-queue")
    assert response.json() == []

    response = await client.get("/v1/review-queue", params={"status": "resolved"})
    assert len(response.json()) == 1


async def test_capability_filter(client: AsyncClient, session: AsyncSession) -> None:
    await _seed_review(session, capability="math")
    await _seed_review(session, capability="coding")

    response = await client.get("/v1/review-queue", params={"capability": "coding"})
    items = response.json()
    assert len(items) == 1
    assert items[0]["capability"] == "coding"


async def test_limit_and_oldest_first(client: AsyncClient, session: AsyncSession) -> None:
    older = datetime(2026, 1, 1, tzinfo=UTC)
    newer = datetime(2026, 1, 2, tzinfo=UTC)
    old_review = await _seed_review(session, created_at=older, prompt="old")
    await _seed_review(session, created_at=newer, prompt="new")

    response = await client.get("/v1/review-queue", params={"limit": 1})
    items = response.json()
    assert len(items) == 1
    assert items[0]["review_item_id"] == str(old_review.id)
    assert items[0]["prompt"] == "old"


async def test_claim_then_conflict(client: AsyncClient, session: AsyncSession) -> None:
    review = await _seed_review(session)

    response = await client.post(f"/v1/review-queue/{review.id}/claim")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "claimed"
    assert body["claimed_at"] is not None

    response = await client.post(f"/v1/review-queue/{review.id}/claim")
    assert response.status_code == 409


async def test_claim_unknown_item(client: AsyncClient) -> None:
    response = await client.post(f"/v1/review-queue/{uuid.uuid4()}/claim")
    assert response.status_code == 404


async def test_resolve_requires_claim(client: AsyncClient, session: AsyncSession) -> None:
    review = await _seed_review(session)
    response = await client.post(
        f"/v1/review-queue/{review.id}/resolve",
        json={"label": 0.8, "notes": "looks right"},
    )
    assert response.status_code == 409


async def test_resolve_records_human_label(client: AsyncClient, session: AsyncSession) -> None:
    review = await _seed_review(session)
    await client.post(f"/v1/review-queue/{review.id}/claim")

    response = await client.post(
        f"/v1/review-queue/{review.id}/resolve",
        json={"label": 0.75, "notes": "close enough"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "resolved"
    assert body["reviewer_label"] == 0.75
    assert body["reviewer_notes"] == "close enough"
    assert body["resolved_at"] is not None

    response = await client.post(
        f"/v1/review-queue/{review.id}/resolve",
        json={"label": 0.1},
    )
    assert response.status_code == 409


async def test_stats(client: AsyncClient, session: AsyncSession) -> None:
    t0 = datetime(2026, 1, 1, tzinfo=UTC)
    await _seed_review(
        session,
        review_status=ReviewStatus.resolved,
        claimed_at=t0,
        resolved_at=t0 + timedelta(seconds=120),
    )
    await _seed_review(session, review_status=ReviewStatus.claimed, claimed_at=t0)
    await _seed_review(session)

    response = await client.get("/v1/review-queue/stats")
    assert response.status_code == 200
    body = response.json()
    assert body["open"] == 1
    assert body["claimed"] == 1
    assert body["resolved"] == 1
    assert body["mean_time_to_resolve_ms"] == 120000.0


async def test_claim_race_exactly_one_winner(real_session_factory) -> None:
    async with real_session_factory() as s:
        task_set = TaskSet(id=uuid.uuid4(), name=f"race-ts-{uuid.uuid4()}")
        task = Task(
            id=uuid.uuid4(),
            task_set_id=task_set.id,
            prompt="race prompt",
            capability="race",
            grader_type=GraderType.exact_match,
        )
        endpoint = ModelEndpoint(
            id=uuid.uuid4(),
            name=f"race-m-{uuid.uuid4()}",
            provider=Provider.local,
            model_id="race-model",
            model_version="v1",
        )
        run = EvalRun(id=uuid.uuid4(), task_set_id=task_set.id, status=EvalRunStatus.running)
        item = RunItem(
            id=uuid.uuid4(),
            eval_run_id=run.id,
            task_id=task.id,
            model_endpoint_id=endpoint.id,
        )
        review = ReviewItem(
            id=uuid.uuid4(),
            run_item_id=item.id,
            reason=ReviewReason.low_confidence,
            status=ReviewStatus.open,
        )
        s.add_all([task_set, task, endpoint, run, item, review])
        await s.commit()
        review_id = review.id
        task_set_id = task_set.id
        endpoint_id = endpoint.id
        task_id = task.id
        run_id = run.id

    try:

        async def claim() -> int:
            async with real_session_factory() as s:
                result = await s.execute(
                    update(ReviewItem)
                    .where(
                        ReviewItem.id == review_id,
                        ReviewItem.status == ReviewStatus.open,
                    )
                    .values(status=ReviewStatus.claimed, claimed_at=func.now())
                )
                await s.commit()
                return result.rowcount

        results = await asyncio.gather(claim(), claim(), claim())
        assert sorted(results) == [0, 0, 1]

        async with real_session_factory() as s:
            row = await s.get(ReviewItem, review_id)
            assert row.status == ReviewStatus.claimed
            assert row.claimed_at is not None
    finally:
        async with real_session_factory() as s:
            await s.execute(delete(RunItem).where(RunItem.eval_run_id == run_id))
            await s.execute(delete(EvalRun).where(EvalRun.id == run_id))
            await s.execute(delete(Task).where(Task.id == task_id))
            await s.execute(delete(ModelEndpoint).where(ModelEndpoint.id == endpoint_id))
            await s.execute(delete(TaskSet).where(TaskSet.id == task_set_id))
            await s.commit()
