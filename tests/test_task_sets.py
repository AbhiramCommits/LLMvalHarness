from uuid import uuid4

from httpx import AsyncClient


async def test_create_and_list_task_sets(client: AsyncClient) -> None:
    response = await client.post(
        "/v1/task-sets",
        json={"name": "arithmetic", "description": "basic math problems"},
    )
    assert response.status_code == 201
    created = response.json()
    assert created["name"] == "arithmetic"
    assert created["description"] == "basic math problems"
    assert created["id"]

    response = await client.get("/v1/task-sets")
    assert response.status_code == 200
    task_sets = response.json()
    assert [ts["name"] for ts in task_sets] == ["arithmetic"]


async def test_get_task_set_by_id(client: AsyncClient) -> None:
    response = await client.post("/v1/task-sets", json={"name": "qa"})
    task_set_id = response.json()["id"]

    response = await client.get(f"/v1/task-sets/{task_set_id}")
    assert response.status_code == 200
    assert response.json()["name"] == "qa"


async def test_get_task_set_not_found(client: AsyncClient) -> None:
    response = await client.get(f"/v1/task-sets/{uuid4()}")
    assert response.status_code == 404


async def test_create_task_under_task_set(client: AsyncClient) -> None:
    response = await client.post("/v1/task-sets", json={"name": "qa"})
    task_set_id = response.json()["id"]

    response = await client.post(
        f"/v1/task-sets/{task_set_id}/tasks",
        json={
            "prompt": "What is 2 + 2?",
            "capability": "arithmetic",
            "expected_output": "4",
            "grader_type": "exact_match",
            "grader_config": {"strip_whitespace": True},
        },
    )
    assert response.status_code == 201
    task = response.json()
    assert task["task_set_id"] == task_set_id
    assert task["prompt"] == "What is 2 + 2?"
    assert task["expected_output"] == "4"
    assert task["grader_type"] == "exact_match"
    assert task["grader_config"] == {"strip_whitespace": True}


async def test_create_task_llm_judge(client: AsyncClient) -> None:
    response = await client.post("/v1/task-sets", json={"name": "writing"})
    task_set_id = response.json()["id"]

    response = await client.post(
        f"/v1/task-sets/{task_set_id}/tasks",
        json={
            "prompt": "Summarize the following article.",
            "capability": "summarization",
            "grader_type": "llm_judge",
            "grader_config": {"rubric": {"coverage": 0.5, "concision": 0.5}},
        },
    )
    assert response.status_code == 201
    assert response.json()["grader_type"] == "llm_judge"


async def test_create_task_requires_existing_task_set(client: AsyncClient) -> None:
    response = await client.post(
        f"/v1/task-sets/{uuid4()}/tasks",
        json={"prompt": "p", "capability": "c", "grader_type": "regex"},
    )
    assert response.status_code == 404


async def test_create_task_rejects_unknown_grader_type(client: AsyncClient) -> None:
    response = await client.post("/v1/task-sets", json={"name": "qa"})
    task_set_id = response.json()["id"]

    response = await client.post(
        f"/v1/task-sets/{task_set_id}/tasks",
        json={"prompt": "p", "capability": "c", "grader_type": "semantic"},
    )
    assert response.status_code == 422


async def test_create_task_set_requires_name(client: AsyncClient) -> None:
    response = await client.post("/v1/task-sets", json={"description": "no name"})
    assert response.status_code == 422
