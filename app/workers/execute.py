"""Celery worker for executing eval run items.

Queue topology: the default queue ``evals`` carries run-item executions with
``task_acks_late`` and a prefetch multiplier of 1, so a crashed worker re-queues
its in-flight item instead of losing it. Items that exhaust all retry attempts
are marked ``dead_lettered`` in Postgres and published to ``evals.dlq`` for
retention and manual replay.

Idempotency: the finalizing UPDATE is guarded on ``status='running'`` and the
eval-run rollup applies cost/token *deltas* (new minus previously recorded),
so replaying an item can never double-count cost.
"""

import asyncio
import logging
import random
import uuid
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from celery import Celery
from sqlalchemy import exists, func, select, update
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.artifacts import set_artifact
from app.config import get_settings
from app.db import SessionFactory
from app.graders.pipeline import grade_run_item
from app.models import (
    EvalRun,
    EvalRunStatus,
    ModelEndpoint,
    RunItem,
    RunItemStatus,
    Task,
)
from app.pricing import compute_cost
from app.providers import (
    NonRetryableProviderError,
    Provider,
    RetryableProviderError,
    get_provider,
)

logger = logging.getLogger(__name__)

settings = get_settings()

celery_app = Celery(
    "llm_eval_harness",
    broker=settings.redis_url,
    backend=settings.redis_url,
)

celery_app.conf.update(
    task_default_queue="evals",
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    task_reject_on_worker_lost=True,
)


@dataclass(frozen=True)
class RetryConfig:
    max_attempts: int = 5
    base_delay_seconds: float = 0.1
    max_delay_seconds: float = 10.0

    @classmethod
    def from_settings(cls) -> "RetryConfig":
        return cls(
            max_attempts=settings.retry_max_attempts,
            base_delay_seconds=settings.retry_base_delay_ms / 1000,
            max_delay_seconds=settings.retry_max_delay_ms / 1000,
        )


def backoff_delay(attempt: int, config: RetryConfig) -> float:
    """Exponential backoff with uniform jitter in [0, delay/2)."""
    exponential = min(
        config.base_delay_seconds * (2 ** (attempt - 1)),
        config.max_delay_seconds,
    )
    return exponential + random.uniform(0, exponential / 2)


@celery_app.task(name="evals.execute_run_item")
def execute_run_item_task(run_item_id: str) -> dict[str, Any]:
    """Celery entrypoint; bridges the sync task to the async executor.

    Each invocation gets a fresh event loop AND a fresh async engine: pooled
    asyncpg connections are bound to the loop that created them, and
    ``asyncio.run`` creates a new loop per call, so a process-level engine
    cannot be reused across task invocations.
    """

    async def _run() -> dict[str, Any]:
        engine = create_async_engine(settings.database_url, pool_pre_ping=True)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        try:
            return await execute_run_item(run_item_id, session_factory=factory)
        finally:
            await engine.dispose()

    return asyncio.run(_run())


@celery_app.task(name="evals.dead_letter")
def dead_letter_task(run_item_id: str) -> None:
    logger.warning("run item %s dead-lettered (retries exhausted)", run_item_id)


def enqueue_run_item(run_item_id: str) -> None:
    """Publish a run-item execution to the ``evals`` queue."""
    execute_run_item_task.apply_async(args=[run_item_id], queue="evals")


def publish_dead_letter(run_item_id: str) -> None:
    """Publish a dead-letter notice to the ``evals.dlq`` queue."""
    celery_app.send_task("evals.dead_letter", args=[run_item_id], queue="evals.dlq")


async def execute_run_item(
    run_item_id: str,
    *,
    provider: Provider | None = None,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
    retry: RetryConfig | None = None,
) -> dict[str, Any]:
    """Execute one run item end to end (provider call, artifact, cost rollup)."""
    factory = session_factory or SessionFactory
    provider = provider or get_provider()
    retry_config = retry or RetryConfig.from_settings()
    rid = uuid.UUID(run_item_id)

    async with factory() as session:
        row = (
            await session.execute(
                select(RunItem, Task, ModelEndpoint, EvalRun.status)
                .join(Task, Task.id == RunItem.task_id)
                .join(ModelEndpoint, ModelEndpoint.id == RunItem.model_endpoint_id)
                .join(EvalRun, EvalRun.id == RunItem.eval_run_id)
                .where(RunItem.id == rid)
            )
        ).first()
        if row is None:
            raise ValueError(f"run item {rid} not found")
        run_item, task, endpoint, run_status = row
        eval_run_id = run_item.eval_run_id

    if run_status == EvalRunStatus.cancelled:
        await _mark_cancelled(factory, rid)
        return {"status": RunItemStatus.failed.value, "reason": "cancelled"}

    if run_item.status == RunItemStatus.succeeded:
        logger.info("run item %s already succeeded; skipping (idempotent)", rid)
        return {"status": RunItemStatus.succeeded.value, "reason": "already-succeeded"}

    if run_item.status not in (RunItemStatus.queued, RunItemStatus.dead_lettered):
        logger.info("run item %s is %s; nothing to do", rid, run_item.status.value)
        return {"status": run_item.status.value, "reason": "not-executable"}

    prev_cost = run_item.cost_usd or Decimal("0")
    prev_tokens = (run_item.prompt_tokens or 0) + (run_item.completion_tokens or 0)

    effective_provider = (
        provider.with_base_url(endpoint.base_url) if endpoint.base_url else provider
    )

    last_error: Exception | None = None
    result = None
    for attempt in range(1, retry_config.max_attempts + 1):
        await _mark_running(factory, rid)
        try:
            result = await effective_provider.complete(task.prompt, endpoint.model_id)
            last_error = None
            break
        except RetryableProviderError as exc:
            last_error = exc
            logger.warning(
                "run item %s attempt %d/%d failed: %s",
                rid,
                attempt,
                retry_config.max_attempts,
                exc,
            )
            if attempt < retry_config.max_attempts:
                await asyncio.sleep(backoff_delay(attempt, retry_config))
        except NonRetryableProviderError as exc:
            await _mark_failed(factory, rid, str(exc))
            await _maybe_complete_run(factory, eval_run_id)
            return {"status": RunItemStatus.failed.value, "reason": "non-retryable"}

    if result is None:
        error_text = f"exhausted {retry_config.max_attempts} attempts: {last_error}"
        await _mark_dead_lettered(factory, rid, error_text)
        publish_dead_letter(str(rid))
        await _maybe_complete_run(factory, eval_run_id)
        return {"status": RunItemStatus.dead_lettered.value, "reason": "retries-exhausted"}

    async with factory() as session:
        still_running = await session.scalar(
            select(EvalRun.status).where(EvalRun.id == eval_run_id)
        )
    if still_running == EvalRunStatus.cancelled:
        await _mark_cancelled(factory, rid)
        return {"status": RunItemStatus.failed.value, "reason": "cancelled"}

    cost = compute_cost(endpoint.model_id, result.prompt_tokens, result.completion_tokens)
    artifact = {
        "request": {
            "prompt": task.prompt,
            "model_id": endpoint.model_id,
            "base_url": endpoint.base_url,
        },
        "raw_response": {
            "text": result.text,
            "prompt_tokens": result.prompt_tokens,
            "completion_tokens": result.completion_tokens,
            "latency_ms": result.latency_ms,
        },
        "judge_raw": None,
    }
    artifact_key = await set_artifact(rid, artifact)

    # Write the artifact to Redis first, then persist run_item + eval_run
    # rollup in one transaction. The status guard means only one execution of
    # an item ever wins, and delta-based rollup keeps replays idempotent.
    new_tokens = result.prompt_tokens + result.completion_tokens
    delta_cost = cost - prev_cost
    delta_tokens = new_tokens - prev_tokens
    async with factory() as session:
        async with session.begin():
            updated = await session.execute(
                update(RunItem)
                .where(RunItem.id == rid, RunItem.status == RunItemStatus.running)
                .values(
                    status=RunItemStatus.succeeded,
                    latency_ms=result.latency_ms,
                    prompt_tokens=result.prompt_tokens,
                    completion_tokens=result.completion_tokens,
                    cost_usd=cost,
                    error=None,
                    artifact_key=artifact_key,
                    updated_at=func.now(),
                )
            )
            if updated.rowcount == 0:
                logger.warning("run item %s already finalized; skipping rollup", rid)
                return {"status": "skipped", "reason": "already-finalized"}
            await session.execute(
                update(EvalRun)
                .where(EvalRun.id == eval_run_id)
                .values(
                    total_cost_usd=EvalRun.total_cost_usd + delta_cost,
                    total_tokens=EvalRun.total_tokens + delta_tokens,
                )
            )

    # Grading runs after the item is finalized. A judge parse failure records
    # a failed Grade; any other pipeline error fails the item explicitly.
    grading_error: Exception | None = None
    try:
        await grade_run_item(
            rid,
            task,
            result.text,
            provider=provider,
            session_factory=factory,
        )
    except Exception as exc:
        grading_error = exc
        logger.exception("grading failed for run item %s", rid)
        await _mark_failed(factory, rid, f"grading pipeline failed: {exc}")

    await _maybe_complete_run(factory, eval_run_id)

    if grading_error is not None:
        return {"status": RunItemStatus.failed.value, "reason": "grading-failed"}

    logger.info(
        "run item %s succeeded (latency_ms=%d, cost=$%s)",
        rid,
        result.latency_ms,
        cost,
    )
    return {"status": RunItemStatus.succeeded.value}


async def _maybe_complete_run(
    factory: async_sessionmaker[AsyncSession],
    eval_run_id: uuid.UUID,
) -> None:
    """Mark the eval run terminal once no queued/running items remain."""
    async with factory() as session:
        async with session.begin():
            remaining = await session.scalar(
                select(func.count())
                .select_from(RunItem)
                .where(
                    RunItem.eval_run_id == eval_run_id,
                    RunItem.status.in_([RunItemStatus.queued, RunItemStatus.running]),
                )
            )
            if remaining == 0:
                await session.execute(
                    update(EvalRun)
                    .where(
                        EvalRun.id == eval_run_id,
                        EvalRun.status == EvalRunStatus.running,
                        exists().where(
                            RunItem.eval_run_id == eval_run_id,
                            RunItem.status == RunItemStatus.dead_lettered,
                        ),
                    )
                    .values(status=EvalRunStatus.failed, completed_at=func.now())
                )
                await session.execute(
                    update(EvalRun)
                    .where(
                        EvalRun.id == eval_run_id,
                        EvalRun.status == EvalRunStatus.running,
                    )
                    .values(status=EvalRunStatus.completed, completed_at=func.now())
                )


async def _mark_running(factory: async_sessionmaker[AsyncSession], rid: uuid.UUID) -> None:
    async with factory() as session:
        async with session.begin():
            await session.execute(
                update(RunItem)
                .where(RunItem.id == rid)
                .values(
                    status=RunItemStatus.running,
                    attempt_count=RunItem.attempt_count + 1,
                    updated_at=func.now(),
                )
            )


async def _mark_failed(
    factory: async_sessionmaker[AsyncSession],
    rid: uuid.UUID,
    error: str,
) -> None:
    async with factory() as session:
        async with session.begin():
            await session.execute(
                update(RunItem)
                .where(RunItem.id == rid)
                .values(
                    status=RunItemStatus.failed,
                    error=error,
                    updated_at=func.now(),
                )
            )


async def _mark_dead_lettered(
    factory: async_sessionmaker[AsyncSession],
    rid: uuid.UUID,
    error: str,
) -> None:
    async with factory() as session:
        async with session.begin():
            await session.execute(
                update(RunItem)
                .where(RunItem.id == rid)
                .values(
                    status=RunItemStatus.dead_lettered,
                    error=error,
                    updated_at=func.now(),
                )
            )


async def _mark_cancelled(
    factory: async_sessionmaker[AsyncSession],
    rid: uuid.UUID,
) -> None:
    async with factory() as session:
        async with session.begin():
            await session.execute(
                update(RunItem)
                .where(RunItem.id == rid, RunItem.status != RunItemStatus.succeeded)
                .values(
                    status=RunItemStatus.failed,
                    error="eval run cancelled",
                    updated_at=func.now(),
                )
            )
