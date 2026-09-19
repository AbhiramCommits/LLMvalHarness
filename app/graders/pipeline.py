"""Grading pipeline: run graders after a run item succeeds.

- deterministic grader (exact_match/regex) runs when the task has an
  expectation (expected_output or a regex pattern)
- LLM rubric judge runs when grader_config carries ``rubric`` criteria
- when both are configured, both run and their scores are compared

Disagreement rule: a review item is opened when
``|deterministic.score - judge.overall| > disagreement_threshold`` (reason:
judge_disagreement) or when ``judge.overall`` falls inside the low-confidence
band (reason: low_confidence).

A judge whose verdict cannot be parsed after 2 attempts records a failed
Grade (score=NULL) -- never a silent 0.0.
"""

import time
from decimal import Decimal
from typing import Any
from uuid import UUID

import structlog
from sqlalchemy import exists, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import get_settings
from app.graders.deterministic import DeterministicGrade, run_deterministic
from app.graders.llm_judge import JudgeParseError, RubricCriterion, grade
from app.metrics import GRADE_DURATION, REVIEW_ITEMS
from app.models import (
    Grade,
    GraderKind,
    ReviewItem,
    ReviewReason,
    Task,
)
from app.providers import Provider

logger = structlog.get_logger(__name__)

PASS_THRESHOLD = Decimal("0.5")

_SCORE_QUANTUM = Decimal("0.001")


def rubric_from_config(
    grader_config: dict[str, Any] | None,
) -> list[RubricCriterion] | None:
    if not grader_config:
        return None
    raw = grader_config.get("rubric")
    if not isinstance(raw, list) or not raw:
        return None
    return [RubricCriterion.model_validate(item) for item in raw]


async def grade_run_item(
    run_item_id: UUID,
    task: Task,
    output_text: str,
    *,
    provider: Provider,
    session_factory: async_sessionmaker[AsyncSession],
) -> list[Grade]:
    """Grade a succeeded run item; inserts Grade rows and any ReviewItem.

    Idempotent: if the item already has grades, they are returned unchanged.
    """
    async with session_factory() as session:
        existing = list(
            (
                await session.execute(
                    select(Grade).where(Grade.run_item_id == run_item_id).order_by(Grade.created_at)
                )
            ).scalars()
        )
        if existing:
            return existing

        grades: list[Grade] = []
        deterministic = run_deterministic(task, output_text)
        if deterministic is not None:
            grades.append(
                Grade(
                    run_item_id=run_item_id,
                    grader=GraderKind.deterministic,
                    score=deterministic.score,
                    passed=deterministic.passed,
                    rationale=deterministic.rationale,
                )
            )

        judge_score: Decimal | None = None
        rubric = rubric_from_config(task.grader_config)
        if rubric is not None:
            try:
                start = time.perf_counter()
                verdict = await grade(
                    provider,
                    prompt=task.prompt,
                    expected_output=task.expected_output,
                    output=output_text,
                    rubric=rubric,
                    model_id=get_settings().judge_model_id,
                )
                GRADE_DURATION.observe(time.perf_counter() - start)
                judge_score = Decimal(str(verdict.overall)).quantize(_SCORE_QUANTUM)
                grades.append(
                    Grade(
                        run_item_id=run_item_id,
                        grader=GraderKind.llm_judge,
                        score=judge_score,
                        passed=judge_score >= PASS_THRESHOLD,
                        rubric_scores=verdict.rubric_scores,
                        rationale=verdict.rationale,
                    )
                )
            except JudgeParseError as exc:
                logger.warning("run item %s judge grade failed: %s", run_item_id, exc)
                grades.append(
                    Grade(
                        run_item_id=run_item_id,
                        grader=GraderKind.llm_judge,
                        score=None,
                        passed=None,
                        rubric_scores=None,
                        rationale=f"judge parse failure: {exc}",
                    )
                )

        session.add_all(grades)

        review_item = None
        if deterministic is not None and judge_score is not None:
            review_item = _review_item_for_disagreement(
                run_item_id,
                deterministic,
                judge_score,
            )
        if review_item is not None:
            already = await session.scalar(
                select(exists().where(ReviewItem.run_item_id == run_item_id))
            )
            if not already:
                REVIEW_ITEMS.labels(reason=review_item.reason.value).inc()
                session.add(review_item)

        await session.commit()
        return grades


def _review_item_for_disagreement(
    run_item_id: UUID,
    deterministic: DeterministicGrade,
    judge_score: Decimal,
) -> ReviewItem | None:
    settings = get_settings()
    delta = abs(deterministic.score - judge_score)
    if delta > Decimal(str(settings.disagreement_threshold)):
        return ReviewItem(
            run_item_id=run_item_id,
            reason=ReviewReason.judge_disagreement,
        )
    if settings.low_confidence_min <= float(judge_score) <= settings.low_confidence_max:
        return ReviewItem(
            run_item_id=run_item_id,
            reason=ReviewReason.low_confidence,
        )
    return None


def authoritative_score(review_item: ReviewItem, grades: list[Grade]) -> Decimal | None:
    """Authoritative score for leaderboard scoring.

    The human reviewer label wins when present; otherwise the mean of the
    available grade scores is used. Returns None when no signal exists.
    """
    if review_item.reviewer_label is not None:
        return review_item.reviewer_label
    scores = [grade.score for grade in grades if grade.score is not None]
    if not scores:
        return None
    return (sum(scores, Decimal("0")) / len(scores)).quantize(_SCORE_QUANTUM)
