import uuid
from datetime import UTC, datetime
from decimal import Decimal

import pytest
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
    RunItemStatus,
    Task,
    TaskSet,
)
from app.models.enums import GraderType
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession


async def _seed_endpoint(
    session: AsyncSession,
    model_name: str,
    model_version: str = "v1",
) -> ModelEndpoint:
    endpoint = ModelEndpoint(
        id=uuid.uuid4(),
        name=model_name,
        provider=Provider.local,
        model_id=f"{model_name}-model-id",
        model_version=model_version,
    )
    session.add(endpoint)
    await session.commit()
    return endpoint


async def _seed_scored_item(
    session: AsyncSession,
    *,
    task_set: TaskSet,
    eval_run: EvalRun,
    endpoint: ModelEndpoint,
    capability: str = "math",
    det_score: Decimal | None = None,
    judge_score: Decimal | None = None,
    reviewer_label: Decimal | None = None,
    latency_ms: int = 50,
    cost_usd: Decimal = Decimal("0.001000"),
) -> None:
    task = Task(
        id=uuid.uuid4(),
        task_set_id=task_set.id,
        prompt="q",
        capability=capability,
        grader_type=GraderType.exact_match,
    )
    item = RunItem(
        id=uuid.uuid4(),
        eval_run_id=eval_run.id,
        task_id=task.id,
        model_endpoint_id=endpoint.id,
        status=RunItemStatus.succeeded,
        latency_ms=latency_ms,
        cost_usd=cost_usd,
    )
    session.add_all([task, item])
    if det_score is not None:
        session.add(
            Grade(
                id=uuid.uuid4(),
                run_item_id=item.id,
                grader=GraderKind.deterministic,
                score=det_score,
                passed=det_score >= Decimal("0.5"),
            )
        )
    if judge_score is not None:
        session.add(
            Grade(
                id=uuid.uuid4(),
                run_item_id=item.id,
                grader=GraderKind.llm_judge,
                score=judge_score,
                passed=judge_score >= Decimal("0.5"),
            )
        )
    if reviewer_label is not None:
        session.add(
            ReviewItem(
                id=uuid.uuid4(),
                run_item_id=item.id,
                reason=ReviewReason.low_confidence,
                status=ReviewStatus.resolved,
                reviewer_label=reviewer_label,
            )
        )
    await session.commit()


async def _seed_run(session: AsyncSession) -> tuple[TaskSet, EvalRun]:
    task_set = TaskSet(id=uuid.uuid4(), name=f"lb-ts-{uuid.uuid4()}")
    run = EvalRun(
        id=uuid.uuid4(),
        task_set_id=task_set.id,
        status=EvalRunStatus.completed,
    )
    session.add_all([task_set, run])
    await session.commit()
    return task_set, run


async def test_leaderboard_aggregates(
    client: AsyncClient,
    session: AsyncSession,
) -> None:
    task_set, run = await _seed_run(session)
    endpoint = await _seed_endpoint(session, "model-a")
    await _seed_scored_item(
        session,
        task_set=task_set,
        eval_run=run,
        endpoint=endpoint,
        det_score=Decimal("1.0"),
        latency_ms=50,
        cost_usd=Decimal("0.001000"),
    )
    await _seed_scored_item(
        session,
        task_set=task_set,
        eval_run=run,
        endpoint=endpoint,
        det_score=Decimal("0.8"),
        latency_ms=90,
        cost_usd=Decimal("0.001000"),
    )

    response = await client.get("/v1/leaderboard")
    assert response.status_code == 200
    rows = response.json()
    assert len(rows) == 1
    row = rows[0]
    assert row["name"] == "model-a"
    assert row["mean_score"] == pytest.approx(0.9)
    assert row["pass_rate"] == pytest.approx(1.0)
    assert row["n"] == 2
    assert row["mean_latency_ms"] == pytest.approx(70.0)
    assert row["p95_latency_ms"] == pytest.approx(88.0)
    assert row["total_cost_usd"] == pytest.approx(0.002)
    assert row["cost_per_1k"] == pytest.approx(1.0)


async def test_human_label_overrides_judge_score(
    client: AsyncClient,
    session: AsyncSession,
) -> None:
    task_set, run = await _seed_run(session)
    endpoint = await _seed_endpoint(session, "model-a")
    await _seed_scored_item(
        session,
        task_set=task_set,
        eval_run=run,
        endpoint=endpoint,
        det_score=Decimal("0.1"),
        judge_score=Decimal("0.9"),
        reviewer_label=Decimal("0.3"),
    )

    response = await client.get("/v1/leaderboard")
    row = response.json()[0]
    assert row["mean_score"] == pytest.approx(0.3)


async def test_judge_overrides_deterministic(
    client: AsyncClient,
    session: AsyncSession,
) -> None:
    task_set, run = await _seed_run(session)
    endpoint = await _seed_endpoint(session, "model-a")
    await _seed_scored_item(
        session,
        task_set=task_set,
        eval_run=run,
        endpoint=endpoint,
        det_score=Decimal("0.1"),
        judge_score=Decimal("0.9"),
    )

    response = await client.get("/v1/leaderboard")
    row = response.json()[0]
    assert row["mean_score"] == pytest.approx(0.9)


async def test_deterministic_score_used_when_no_judge(
    client: AsyncClient,
    session: AsyncSession,
) -> None:
    task_set, run = await _seed_run(session)
    endpoint = await _seed_endpoint(session, "model-a")
    await _seed_scored_item(
        session,
        task_set=task_set,
        eval_run=run,
        endpoint=endpoint,
        det_score=Decimal("1.0"),
    )

    response = await client.get("/v1/leaderboard")
    row = response.json()[0]
    assert row["mean_score"] == pytest.approx(1.0)


async def test_leaderboard_filters_by_task_set(
    client: AsyncClient,
    session: AsyncSession,
) -> None:
    task_set_a, run_a = await _seed_run(session)
    task_set_b, run_b = await _seed_run(session)
    endpoint_a = await _seed_endpoint(session, "model-a")
    endpoint_b = await _seed_endpoint(session, "model-b")
    await _seed_scored_item(
        session,
        task_set=task_set_a,
        eval_run=run_a,
        endpoint=endpoint_a,
        det_score=Decimal("1.0"),
    )
    await _seed_scored_item(
        session,
        task_set=task_set_b,
        eval_run=run_b,
        endpoint=endpoint_b,
        det_score=Decimal("1.0"),
    )

    response = await client.get(
        "/v1/leaderboard",
        params={"task_set_id": str(task_set_a.id)},
    )
    names = [row["name"] for row in response.json()]
    assert names == ["model-a"]


async def test_leaderboard_filters_by_capability(
    client: AsyncClient,
    session: AsyncSession,
) -> None:
    task_set, run = await _seed_run(session)
    endpoint = await _seed_endpoint(session, "model-a")
    await _seed_scored_item(
        session,
        task_set=task_set,
        eval_run=run,
        endpoint=endpoint,
        capability="math",
        det_score=Decimal("1.0"),
    )
    await _seed_scored_item(
        session,
        task_set=task_set,
        eval_run=run,
        endpoint=endpoint,
        capability="coding",
        det_score=Decimal("1.0"),
    )

    response = await client.get("/v1/leaderboard", params={"capability": "coding"})
    rows = response.json()
    assert len(rows) == 1
    assert rows[0]["n"] == 1


async def test_history_tracks_versions_over_time(
    client: AsyncClient,
    session: AsyncSession,
) -> None:
    task_set = TaskSet(id=uuid.uuid4(), name=f"h-ts-{uuid.uuid4()}")
    session.add(task_set)
    await session.commit()

    run_old = EvalRun(
        id=uuid.uuid4(),
        task_set_id=task_set.id,
        status=EvalRunStatus.completed,
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    session.add(run_old)
    await session.commit()
    endpoint_v1 = await _seed_endpoint(session, "model-a", model_version="v1")
    await _seed_scored_item(
        session,
        task_set=task_set,
        eval_run=run_old,
        endpoint=endpoint_v1,
        det_score=Decimal("0.6"),
    )

    run_new = EvalRun(
        id=uuid.uuid4(),
        task_set_id=task_set.id,
        status=EvalRunStatus.completed,
        created_at=datetime(2026, 2, 1, tzinfo=UTC),
    )
    session.add(run_new)
    await session.commit()
    endpoint_v2 = await _seed_endpoint(session, "model-a", model_version="v2")
    await _seed_scored_item(
        session,
        task_set=task_set,
        eval_run=run_new,
        endpoint=endpoint_v2,
        det_score=Decimal("0.95"),
    )

    response = await client.get(
        "/v1/leaderboard/history",
        params={"model_name": "model-a"},
    )
    assert response.status_code == 200
    points = response.json()
    assert [p["model_version"] for p in points] == ["v1", "v2"]
    assert points[0]["mean_score"] == pytest.approx(0.6)
    assert points[1]["mean_score"] == pytest.approx(0.95)
    assert points[0]["evaluated_at"] < points[1]["evaluated_at"]


async def test_capabilities_breakdown(
    client: AsyncClient,
    session: AsyncSession,
) -> None:
    task_set, run = await _seed_run(session)
    endpoint = await _seed_endpoint(session, "model-a")
    await _seed_scored_item(
        session,
        task_set=task_set,
        eval_run=run,
        endpoint=endpoint,
        capability="math",
        det_score=Decimal("1.0"),
    )
    await _seed_scored_item(
        session,
        task_set=task_set,
        eval_run=run,
        endpoint=endpoint,
        capability="math",
        det_score=Decimal("0.6"),
    )
    await _seed_scored_item(
        session,
        task_set=task_set,
        eval_run=run,
        endpoint=endpoint,
        capability="coding",
        det_score=Decimal("0.0"),
    )

    response = await client.get(
        "/v1/leaderboard/capabilities",
        params={"model_endpoint_id": str(endpoint.id)},
    )
    assert response.status_code == 200
    rows = {row["capability"]: row for row in response.json()}
    assert rows["math"]["n"] == 2
    assert rows["math"]["mean_score"] == pytest.approx(0.8)
    assert rows["math"]["pass_rate"] == pytest.approx(1.0)
    assert rows["coding"]["n"] == 1
    assert rows["coding"]["mean_score"] == pytest.approx(0.0)


async def test_history_requires_model_name(client: AsyncClient) -> None:
    response = await client.get("/v1/leaderboard/history")
    assert response.status_code == 422


async def test_capabilities_requires_endpoint_id(client: AsyncClient) -> None:
    response = await client.get("/v1/leaderboard/capabilities")
    assert response.status_code == 422
