import uuid

from app.models import EvalRunStatus, RunItem, RunItemStatus
from app.providers.fake import FakeProvider
from app.workers.execute import execute_run_item
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tests.helpers import create_eval_run, seed_models, seed_task_set


async def test_create_eval_run_fans_out_items(
    client: AsyncClient,
    session: AsyncSession,
) -> None:
    task_set_id = await seed_task_set(client, 3)
    model_ids = await seed_models(client, 2)

    response = await client.post(
        "/v1/eval-runs",
        json={"task_set_id": task_set_id, "model_endpoint_ids": model_ids},
    )
    assert response.status_code == 202
    run_id = response.json()["id"]

    items = (
        (await session.execute(select(RunItem).where(RunItem.eval_run_id == uuid.UUID(run_id))))
        .scalars()
        .all()
    )
    assert len(items) == 6
    assert all(item.status == RunItemStatus.queued for item in items)

    response = await client.get(f"/v1/eval-runs/{run_id}")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "running"
    assert {model["name"] for model in body["models"]} == {
        "fake-model-0",
        "fake-model-1",
    }
    for model in body["models"]:
        assert model["counts"]["queued"] == 3


async def test_create_eval_run_unknown_task_set(client: AsyncClient) -> None:
    response = await client.post(
        "/v1/eval-runs",
        json={"task_set_id": str(uuid.uuid4()), "model_endpoint_ids": [str(uuid.uuid4())]},
    )
    assert response.status_code == 404


async def test_create_eval_run_unknown_endpoint(client: AsyncClient) -> None:
    task_set_id = await seed_task_set(client, 1)
    model_ids = await seed_models(client, 1)

    response = await client.post(
        "/v1/eval-runs",
        json={
            "task_set_id": task_set_id,
            "model_endpoint_ids": [*model_ids, str(uuid.uuid4())],
        },
    )
    assert response.status_code == 400


async def test_create_eval_run_requires_model_ids(client: AsyncClient) -> None:
    task_set_id = await seed_task_set(client, 1)
    response = await client.post(
        "/v1/eval-runs",
        json={"task_set_id": task_set_id, "model_endpoint_ids": []},
    )
    assert response.status_code == 422


async def test_get_eval_run_not_found(client: AsyncClient) -> None:
    response = await client.get(f"/v1/eval-runs/{uuid.uuid4()}")
    assert response.status_code == 404


async def test_cancel_eval_run(client: AsyncClient, session: AsyncSession) -> None:
    task_set_id = await seed_task_set(client, 2)
    model_ids = await seed_models(client, 1)
    run_id = await create_eval_run(client, task_set_id, model_ids)

    response = await client.post(f"/v1/eval-runs/{run_id}/cancel")
    assert response.status_code == 200
    assert response.json()["status"] == "cancelled"

    items = (
        (await session.execute(select(RunItem).where(RunItem.eval_run_id == uuid.UUID(run_id))))
        .scalars()
        .all()
    )
    assert all(item.status == RunItemStatus.failed for item in items)
    assert all(item.error == "eval run cancelled" for item in items)


async def test_get_eval_run_aggregates_after_execution(
    client: AsyncClient,
    session: AsyncSession,
    session_factory,
) -> None:
    task_set_id = await seed_task_set(client, 1)
    model_ids = await seed_models(client, 2)
    run_id = await create_eval_run(client, task_set_id, model_ids)

    items = (
        (await session.execute(select(RunItem).where(RunItem.eval_run_id == uuid.UUID(run_id))))
        .scalars()
        .all()
    )
    for item in items:
        await execute_run_item(
            str(item.id),
            provider=FakeProvider(),
            session_factory=session_factory,
        )

    response = await client.get(f"/v1/eval-runs/{run_id}")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "completed"
    assert len(body["models"]) == 2
    for model in body["models"]:
        assert model["counts"]["succeeded"] == 1
        assert model["mean_latency_ms"] is not None
        assert model["total_cost_usd"] == 0.0
    assert body["total_tokens"] > 0


async def test_cancel_completed_run_conflicts(client: AsyncClient) -> None:
    task_set_id = await seed_task_set(client, 0)
    model_ids = await seed_models(client, 1)
    run_id = await create_eval_run(client, task_set_id, model_ids)

    response = await client.get(f"/v1/eval-runs/{run_id}")
    assert response.json()["status"] == EvalRunStatus.completed.value

    response = await client.post(f"/v1/eval-runs/{run_id}/cancel")
    assert response.status_code == 409
