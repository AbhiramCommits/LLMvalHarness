from httpx import AsyncClient


async def seed_task_set(client: AsyncClient, n_tasks: int, name: str = "test-set") -> str:
    response = await client.post("/v1/task-sets", json={"name": name})
    response.raise_for_status()
    task_set_id = response.json()["id"]
    for i in range(n_tasks):
        response = await client.post(
            f"/v1/task-sets/{task_set_id}/tasks",
            json={
                "prompt": f"prompt {i}",
                "capability": "test",
                "grader_type": "exact_match",
            },
        )
        response.raise_for_status()
    return task_set_id


async def seed_models(client: AsyncClient, n_models: int) -> list[str]:
    model_ids = []
    for j in range(n_models):
        response = await client.post(
            "/v1/models",
            json={
                "name": f"fake-model-{j}",
                "provider": "local",
                "model_id": f"fake-model-{j}",
                "model_version": "v1",
            },
        )
        response.raise_for_status()
        model_ids.append(response.json()["id"])
    return model_ids


async def create_eval_run(
    client: AsyncClient,
    task_set_id: str,
    model_ids: list[str],
) -> str:
    response = await client.post(
        "/v1/eval-runs",
        json={"task_set_id": task_set_id, "model_endpoint_ids": model_ids},
    )
    response.raise_for_status()
    return response.json()["id"]
