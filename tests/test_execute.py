import uuid

from app.artifacts import get_artifact
from app.models import EvalRun, EvalRunStatus, RunItem, RunItemStatus
from app.providers.base import (
    CompletionResult,
    NonRetryableProviderError,
    RetryableProviderError,
)
from app.providers.fake import FakeProvider
from app.workers.execute import RetryConfig, execute_run_item
from httpx import AsyncClient
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from tests.helpers import create_eval_run, seed_models, seed_task_set


class _FlakyProvider:
    """Raises ``exc_type`` for the first N calls, then returns ``result``."""

    def __init__(
        self,
        failures: int,
        result: CompletionResult | None = None,
        exc_type: type[Exception] = RetryableProviderError,
    ) -> None:
        self.remaining = failures
        self.result = result or CompletionResult(
            text="ok",
            prompt_tokens=10,
            completion_tokens=20,
            latency_ms=7,
        )
        self.exc_type = exc_type

    async def complete(self, prompt: str, model_id: str) -> CompletionResult:
        if self.remaining > 0:
            self.remaining -= 1
            raise self.exc_type("flaky failure")
        return self.result

    def with_base_url(self, base_url: str) -> "_FlakyProvider":
        return self


_FAST_RETRY = RetryConfig(max_attempts=5, base_delay_seconds=0.001, max_delay_seconds=0.01)


async def _seed_single_item(
    client: AsyncClient,
    session: AsyncSession,
) -> tuple[str, RunItem]:
    task_set_id = await seed_task_set(client, 1)
    model_ids = await seed_models(client, 1)
    run_id = await create_eval_run(client, task_set_id, model_ids)
    item = (
        (await session.execute(select(RunItem).where(RunItem.eval_run_id == uuid.UUID(run_id))))
        .scalars()
        .one()
    )
    return run_id, item


async def test_execute_success(
    client: AsyncClient,
    session: AsyncSession,
    session_factory,
) -> None:
    run_id, item = await _seed_single_item(client, session)

    await execute_run_item(
        str(item.id),
        provider=FakeProvider(),
        session_factory=session_factory,
    )

    run_item = await session.get(RunItem, item.id)
    await session.refresh(run_item)
    assert run_item.status == RunItemStatus.succeeded
    assert run_item.latency_ms is not None
    assert run_item.prompt_tokens > 0
    assert run_item.completion_tokens > 0
    assert run_item.attempt_count == 1
    assert run_item.artifact_key == f"artifact:{item.id}"

    run = await session.get(EvalRun, uuid.UUID(run_id))
    await session.refresh(run)
    assert run.status == EvalRunStatus.completed
    assert run.total_tokens == run_item.prompt_tokens + run_item.completion_tokens
    assert run.total_cost_usd == run_item.cost_usd

    artifact = await get_artifact(item.id)
    assert artifact is not None
    assert artifact["raw_response"]["text"].startswith("[fake:fake-model-0]")


async def test_execute_is_idempotent(
    client: AsyncClient,
    session: AsyncSession,
    session_factory,
) -> None:
    run_id, item = await _seed_single_item(client, session)
    await execute_run_item(
        str(item.id),
        provider=FakeProvider(),
        session_factory=session_factory,
    )
    run_before = await session.get(EvalRun, uuid.UUID(run_id))
    await session.refresh(run_before)

    await execute_run_item(
        str(item.id),
        provider=FakeProvider(),
        session_factory=session_factory,
    )

    run_item = await session.get(RunItem, item.id)
    await session.refresh(run_item)
    run_after = await session.get(EvalRun, uuid.UUID(run_id))
    await session.refresh(run_after)
    assert run_item.attempt_count == 1
    assert run_after.total_tokens == run_before.total_tokens
    assert run_after.total_cost_usd == run_before.total_cost_usd


async def test_execute_retries_then_succeeds(
    client: AsyncClient,
    session: AsyncSession,
    session_factory,
) -> None:
    _, item = await _seed_single_item(client, session)

    await execute_run_item(
        str(item.id),
        provider=_FlakyProvider(failures=2),
        session_factory=session_factory,
        retry=_FAST_RETRY,
    )

    run_item = await session.get(RunItem, item.id)
    await session.refresh(run_item)
    assert run_item.status == RunItemStatus.succeeded
    assert run_item.attempt_count == 3


async def test_execute_exhausted_retries_dead_letters(
    client: AsyncClient,
    session: AsyncSession,
    session_factory,
) -> None:
    run_id, item = await _seed_single_item(client, session)

    await execute_run_item(
        str(item.id),
        provider=_FlakyProvider(failures=99),
        session_factory=session_factory,
        retry=RetryConfig(max_attempts=3, base_delay_seconds=0.001, max_delay_seconds=0.01),
    )

    run_item = await session.get(RunItem, item.id)
    await session.refresh(run_item)
    assert run_item.status == RunItemStatus.dead_lettered
    assert run_item.attempt_count == 3
    assert "exhausted" in (run_item.error or "")

    response = await client.get(f"/v1/eval-runs/{run_id}/dead-letters")
    assert response.status_code == 200
    dead_letters = response.json()
    assert [letter["run_item_id"] for letter in dead_letters] == [str(item.id)]


async def test_execute_non_retryable_error_fails(
    client: AsyncClient,
    session: AsyncSession,
    session_factory,
) -> None:
    _, item = await _seed_single_item(client, session)

    await execute_run_item(
        str(item.id),
        provider=_FlakyProvider(failures=1, exc_type=NonRetryableProviderError),
        session_factory=session_factory,
        retry=_FAST_RETRY,
    )

    run_item = await session.get(RunItem, item.id)
    await session.refresh(run_item)
    assert run_item.status == RunItemStatus.failed
    assert run_item.attempt_count == 1


async def test_execute_skips_cancelled_run(
    client: AsyncClient,
    session: AsyncSession,
    session_factory,
) -> None:
    run_id, item = await _seed_single_item(client, session)
    await session.execute(
        update(RunItem).where(RunItem.id == item.id).values(status=RunItemStatus.running)
    )
    await session.commit()

    response = await client.post(f"/v1/eval-runs/{run_id}/cancel")
    assert response.status_code == 200

    await execute_run_item(
        str(item.id),
        provider=FakeProvider(),
        session_factory=session_factory,
    )

    run_item = await session.get(RunItem, item.id)
    await session.refresh(run_item)
    assert run_item.status == RunItemStatus.failed
    assert run_item.error == "eval run cancelled"


async def test_replay_dead_lettered_item(
    client: AsyncClient,
    session: AsyncSession,
    session_factory,
) -> None:
    run_id, item = await _seed_single_item(client, session)
    await execute_run_item(
        str(item.id),
        provider=_FlakyProvider(failures=99),
        session_factory=session_factory,
        retry=RetryConfig(max_attempts=2, base_delay_seconds=0.001, max_delay_seconds=0.01),
    )

    response = await client.post(f"/v1/dead-letters/{item.id}/replay")
    assert response.status_code == 202
    assert response.json()["status"] == "queued"

    run_item = await session.get(RunItem, item.id)
    await session.refresh(run_item)
    assert run_item.status == RunItemStatus.queued
    assert run_item.error is None

    await execute_run_item(
        str(item.id),
        provider=FakeProvider(),
        session_factory=session_factory,
    )

    run_item = await session.get(RunItem, item.id)
    await session.refresh(run_item)
    assert run_item.status == RunItemStatus.succeeded

    run = await session.get(EvalRun, uuid.UUID(run_id))
    await session.refresh(run)
    assert run.total_tokens == run_item.prompt_tokens + run_item.completion_tokens
    assert run.total_cost_usd == run_item.cost_usd


async def test_replay_rejects_non_dead_lettered(
    client: AsyncClient,
    session: AsyncSession,
) -> None:
    _, item = await _seed_single_item(client, session)
    response = await client.post(f"/v1/dead-letters/{item.id}/replay")
    assert response.status_code == 409


async def test_replay_unknown_item(client: AsyncClient) -> None:
    response = await client.post(f"/v1/dead-letters/{uuid.uuid4()}/replay")
    assert response.status_code == 404
