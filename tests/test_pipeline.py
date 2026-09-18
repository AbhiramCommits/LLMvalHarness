import uuid
from decimal import Decimal

from app.graders.pipeline import authoritative_score, grade_run_item
from app.models import (
    EvalRun,
    EvalRunStatus,
    Grade,
    GraderKind,
    ModelEndpoint,
    Provider,
    ReviewItem,
    ReviewReason,
    RunItem,
    RunItemStatus,
    Task,
    TaskSet,
)
from app.models.enums import GraderType
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tests.helpers import ScriptedProvider, judge_json

RUBRIC_CONFIG = {
    "rubric": [
        {"name": "accuracy", "description": "is it correct", "weight": 1.0},
        {"name": "clarity", "description": "is it clear", "weight": 1.0},
    ]
}

TWO_CRITERIA_SCORES = {"accuracy": 0.9, "clarity": 0.7}


async def _seed_item(
    session: AsyncSession,
    *,
    grader_type: GraderType = GraderType.exact_match,
    expected_output: str | None = "4",
    grader_config: dict | None = None,
) -> tuple[Task, RunItem]:
    task_set = TaskSet(id=uuid.uuid4(), name=f"ts-{uuid.uuid4()}")
    task = Task(
        id=uuid.uuid4(),
        task_set_id=task_set.id,
        prompt="What is 2+2?",
        capability="math",
        grader_type=grader_type,
        expected_output=expected_output,
        grader_config=grader_config,
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
    session.add_all([task_set, task, endpoint, run, item])
    await session.commit()
    return task, item


async def _grades(session: AsyncSession, run_item_id: uuid.UUID) -> list[Grade]:
    return list(
        (
            await session.execute(
                select(Grade).where(Grade.run_item_id == run_item_id).order_by(Grade.created_at)
            )
        ).scalars()
    )


async def _review_items(session: AsyncSession, run_item_id: uuid.UUID) -> list[ReviewItem]:
    return list(
        (
            await session.execute(select(ReviewItem).where(ReviewItem.run_item_id == run_item_id))
        ).scalars()
    )


async def test_deterministic_only_grade(session: AsyncSession, session_factory) -> None:
    task, item = await _seed_item(session, expected_output="4")
    grades = await grade_run_item(
        item.id,
        task,
        "4",
        provider=ScriptedProvider("unused"),
        session_factory=session_factory,
    )
    assert [g.grader for g in grades] == [GraderKind.deterministic]
    assert grades[0].score == Decimal("1.0") and grades[0].passed is True
    assert await _review_items(session, item.id) == []


async def test_deterministic_failure_scores_zero(session: AsyncSession, session_factory) -> None:
    task, item = await _seed_item(session, expected_output="4")
    grades = await grade_run_item(
        item.id,
        task,
        "5",
        provider=ScriptedProvider("unused"),
        session_factory=session_factory,
    )
    assert grades[0].score == Decimal("0.0") and grades[0].passed is False


async def test_llm_judge_only_grade(session: AsyncSession, session_factory) -> None:
    task, item = await _seed_item(
        session,
        grader_type=GraderType.llm_judge,
        expected_output=None,
        grader_config=RUBRIC_CONFIG,
    )
    provider = ScriptedProvider(judge_json(0.8, TWO_CRITERIA_SCORES))
    grades = await grade_run_item(
        item.id,
        task,
        "the answer is 4",
        provider=provider,
        session_factory=session_factory,
    )
    assert [g.grader for g in grades] == [GraderKind.llm_judge]
    assert grades[0].score == Decimal("0.800")
    assert grades[0].passed is True
    assert grades[0].rubric_scores == TWO_CRITERIA_SCORES
    assert await _review_items(session, item.id) == []


async def test_both_graders_disagreement_creates_review(
    session: AsyncSession,
    session_factory,
) -> None:
    task, item = await _seed_item(
        session,
        grader_type=GraderType.exact_match,
        expected_output="4",
        grader_config=RUBRIC_CONFIG,
    )
    provider = ScriptedProvider(judge_json(0.1, {"accuracy": 0.1, "clarity": 0.1}))
    grades = await grade_run_item(
        item.id,
        task,
        "4",
        provider=provider,
        session_factory=session_factory,
    )
    assert {g.grader for g in grades} == {GraderKind.deterministic, GraderKind.llm_judge}
    reviews = await _review_items(session, item.id)
    assert len(reviews) == 1
    assert reviews[0].reason == ReviewReason.judge_disagreement


async def test_low_confidence_band_creates_review(
    session: AsyncSession,
    session_factory,
) -> None:
    task, item = await _seed_item(
        session,
        grader_type=GraderType.exact_match,
        expected_output="4",
        grader_config=RUBRIC_CONFIG,
    )
    provider = ScriptedProvider(judge_json(0.5, {"accuracy": 0.5, "clarity": 0.5}))
    await grade_run_item(
        item.id,
        task,
        "4",
        provider=provider,
        session_factory=session_factory,
    )
    reviews = await _review_items(session, item.id)
    assert len(reviews) == 1
    assert reviews[0].reason == ReviewReason.low_confidence


async def test_judge_parse_failure_records_failed_grade(
    session: AsyncSession,
    session_factory,
) -> None:
    task, item = await _seed_item(
        session,
        grader_type=GraderType.exact_match,
        expected_output="4",
        grader_config=RUBRIC_CONFIG,
    )
    provider = ScriptedProvider(["garbage", "still garbage"])
    grades = await grade_run_item(
        item.id,
        task,
        "4",
        provider=provider,
        session_factory=session_factory,
    )
    judge = next(g for g in grades if g.grader == GraderKind.llm_judge)
    assert judge.score is None
    assert judge.passed is None
    assert "parse failure" in (judge.rationale or "")
    deterministic = next(g for g in grades if g.grader == GraderKind.deterministic)
    assert deterministic.score == Decimal("1.0")
    assert await _review_items(session, item.id) == []


async def test_pipeline_is_idempotent(session: AsyncSession, session_factory) -> None:
    task, item = await _seed_item(
        session,
        grader_type=GraderType.exact_match,
        expected_output="4",
        grader_config=RUBRIC_CONFIG,
    )
    provider = ScriptedProvider(judge_json(0.9, {"accuracy": 0.9, "clarity": 0.9}))
    await grade_run_item(item.id, task, "4", provider=provider, session_factory=session_factory)
    grades_again = await grade_run_item(
        item.id,
        task,
        "4",
        provider=provider,
        session_factory=session_factory,
    )
    assert len(grades_again) == 2
    assert len(await _grades(session, item.id)) == 2
    assert len(await _review_items(session, item.id)) <= 1


async def test_execute_runs_grading_pipeline(client, session, session_factory) -> None:
    from app.workers.execute import execute_run_item

    from tests.helpers import create_eval_run, seed_models

    response = await client.post("/v1/task-sets", json={"name": "judge-set"})
    task_set_id = response.json()["id"]
    response = await client.post(
        f"/v1/task-sets/{task_set_id}/tasks",
        json={
            "prompt": "Is the sky blue?",
            "capability": "qa",
            "grader_type": "llm_judge",
            "grader_config": RUBRIC_CONFIG,
        },
    )
    assert response.status_code == 201
    model_ids = await seed_models(client, 1)
    run_id = await create_eval_run(client, task_set_id, model_ids)

    item = (
        (await session.execute(select(RunItem).where(RunItem.eval_run_id == uuid.UUID(run_id))))
        .scalars()
        .one()
    )

    provider = ScriptedProvider(judge_json(0.7, {"accuracy": 0.7, "clarity": 0.7}))
    await execute_run_item(
        str(item.id),
        provider=provider,
        session_factory=session_factory,
    )

    run_item = await session.get(RunItem, item.id)
    await session.refresh(run_item)
    assert run_item.status == RunItemStatus.succeeded
    grades = await _grades(session, item.id)
    assert [g.grader for g in grades] == [GraderKind.llm_judge]
    assert grades[0].score == Decimal("0.700")

    run = await session.get(EvalRun, uuid.UUID(run_id))
    await session.refresh(run)
    assert run.status == EvalRunStatus.completed


def test_authoritative_score_prefers_human_label() -> None:
    review = ReviewItem(reviewer_label=Decimal("0.900"))
    assert authoritative_score(review, []) == Decimal("0.900")


def test_authoritative_score_means_grades() -> None:
    review = ReviewItem(reviewer_label=None)
    grades = [Grade(score=Decimal("0.800")), Grade(score=Decimal("0.600"))]
    assert authoritative_score(review, grades) == Decimal("0.700")


def test_authoritative_score_none_without_signal() -> None:
    review = ReviewItem(reviewer_label=None)
    assert authoritative_score(review, [Grade(score=None)]) is None
