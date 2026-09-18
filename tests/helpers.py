import json

from app.providers.base import CompletionResult
from httpx import AsyncClient


class ScriptedProvider:
    """Returns scripted responses in order; repeats the last one forever."""

    def __init__(self, responses: list[str] | str) -> None:
        self.responses = [responses] if isinstance(responses, str) else responses
        self.calls = 0

    async def complete(self, prompt: str, model_id: str) -> CompletionResult:
        index = min(self.calls, len(self.responses) - 1)
        self.calls += 1
        return CompletionResult(
            text=self.responses[index],
            prompt_tokens=1,
            completion_tokens=1,
            latency_ms=1,
        )

    def with_base_url(self, base_url: str) -> "ScriptedProvider":
        return self


def judge_json(
    overall: float,
    rubric_scores: dict[str, float] | None = None,
    rationale: str = "looks correct",
) -> str:
    return json.dumps(
        {
            "rubric_scores": rubric_scores or {"accuracy": overall},
            "overall": overall,
            "rationale": rationale,
        }
    )


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
