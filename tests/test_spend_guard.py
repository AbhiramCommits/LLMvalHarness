import uuid
from decimal import Decimal

from app.models import EvalRun, EvalRunStatus, RunItem, RunItemStatus
from app.providers.base import CompletionResult
from app.workers.execute import execute_run_item
from httpx import AsyncClient
from prometheus_client import REGISTRY
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tests.helpers import ScriptedProvider, create_eval_run, seed_models, seed_task_set


class _CostlyProvider:
    async def complete(self, prompt: str, model_id: str) -> CompletionResult:
        return CompletionResult(
            text="answer",
            prompt_tokens=100_000,
            completion_tokens=100_000,
            latency_ms=5,
        )

    def with_base_url(self, base_url: str) -> "_CostlyProvider":
        return self


async def _seed_run_with_priced_model(
    client: AsyncClient,
    session: AsyncSession,
    n_tasks: int = 2,
) -> tuple[str, list[RunItem]]:
    task_set_id = await seed_task_set(client, n_tasks, name=f"spend-{uuid.uuid4().hex[:8]}")
    response = await client.post(
        "/v1/models",
        json={
            "name": "gpt-4o-priced",
            "provider": "openai",
            "model_id": "gpt-4o",
            "model_version": "v1",
        },
    )
    model_id = response.json()["id"]
    run_id = await create_eval_run(client, task_set_id, [model_id])
    items = (
        (await session.execute(select(RunItem).where(RunItem.eval_run_id == uuid.UUID(run_id))))
        .scalars()
        .all()
    )
    return run_id, list(items)


async def test_spend_limit_aborts_run(
    client: AsyncClient,
    session: AsyncSession,
    session_factory,
) -> None:
    run_id, items = await _seed_run_with_priced_model(client, session, n_tasks=2)

    result = await execute_run_item(
        str(items[0].id),
        provider=_CostlyProvider(),
        session_factory=session_factory,
        max_spend_usd=Decimal("0.01"),
    )
    assert result["status"] == RunItemStatus.succeeded.value
    assert result["reason"] == "run-aborted"

    run = await session.get(EvalRun, uuid.UUID(run_id))
    assert run is not None
    await session.refresh(run)
    assert run.status == EvalRunStatus.failed
    assert "spend" in (run.error or "")

    first = await session.get(RunItem, items[0].id)
    assert first is not None
    await session.refresh(first)
    assert first.status == RunItemStatus.succeeded

    second = await session.get(RunItem, items[1].id)
    assert second is not None
    await session.refresh(second)
    assert second.status == RunItemStatus.failed
    assert "aborted" in (second.error or "")


async def test_worker_skips_items_of_aborted_run(
    client: AsyncClient,
    session: AsyncSession,
    session_factory,
) -> None:
    run_id, items = await _seed_run_with_priced_model(client, session, n_tasks=2)

    await execute_run_item(
        str(items[0].id),
        provider=_CostlyProvider(),
        session_factory=session_factory,
        max_spend_usd=Decimal("0.01"),
    )

    result = await execute_run_item(
        str(items[1].id),
        provider=ScriptedProvider("unused"),
        session_factory=session_factory,
    )
    assert result["reason"] == "run-aborted"

    run = await session.get(EvalRun, uuid.UUID(run_id))
    assert run is not None
    await session.refresh(run)
    assert run.status == EvalRunStatus.failed


async def test_spend_limit_not_exceeded_within_budget(
    client: AsyncClient,
    session: AsyncSession,
    session_factory,
) -> None:
    run_id, items = await _seed_run_with_priced_model(client, session, n_tasks=2)

    for item in items:
        await execute_run_item(
            str(item.id),
            provider=_CostlyProvider(),
            session_factory=session_factory,
            max_spend_usd=Decimal("1000.00"),
        )

    run = await session.get(EvalRun, uuid.UUID(run_id))
    assert run is not None
    await session.refresh(run)
    assert run.status == EvalRunStatus.completed


async def test_metrics_endpoint_exposes_names(anonymous_client: AsyncClient) -> None:
    response = await anonymous_client.get("/metrics")
    assert response.status_code == 200
    body = response.text
    for metric in (
        "eval_items_total",
        "dead_letters_total",
        "review_items_total",
        "provider_latency_seconds",
        "grade_duration_seconds",
        "queue_depth",
    ):
        assert metric in body


async def test_eval_items_counter_incremented(
    client: AsyncClient,
    session: AsyncSession,
    session_factory,
) -> None:
    task_set_id = await seed_task_set(client, 1, name=f"metric-{uuid.uuid4().hex[:8]}")
    model_ids = await seed_models(client, 1)
    run_id = await create_eval_run(client, task_set_id, model_ids)
    item = (
        (await session.execute(select(RunItem).where(RunItem.eval_run_id == uuid.UUID(run_id))))
        .scalars()
        .one()
    )

    await execute_run_item(
        str(item.id),
        provider=ScriptedProvider("ok"),
        session_factory=session_factory,
    )

    succeeded = REGISTRY.get_sample_value(
        "eval_items_total",
        {"status": "succeeded", "model": "fake-model-0"},
    )
    assert succeeded is not None and succeeded >= 1.0

    latency = REGISTRY.get_sample_value(
        "provider_latency_seconds_count",
        {"provider": "fake"},
    )
    assert latency is not None and latency >= 1.0
