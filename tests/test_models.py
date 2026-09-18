from uuid import uuid4

from httpx import AsyncClient


async def test_create_and_list_model_endpoints(client: AsyncClient) -> None:
    response = await client.post(
        "/v1/models",
        json={
            "name": "gpt-4o-main",
            "provider": "openai",
            "model_id": "gpt-4o",
            "model_version": "2024-08-06",
            "base_url": "https://api.openai.com/v1",
        },
    )
    assert response.status_code == 201
    created = response.json()
    assert created["provider"] == "openai"
    assert created["model_id"] == "gpt-4o"
    assert created["model_version"] == "2024-08-06"

    response = await client.get("/v1/models")
    assert response.status_code == 200
    endpoints = response.json()
    assert [e["name"] for e in endpoints] == ["gpt-4o-main"]


async def test_create_local_endpoint_without_base_url(client: AsyncClient) -> None:
    response = await client.post(
        "/v1/models",
        json={
            "name": "llama-local",
            "provider": "local",
            "model_id": "llama-3.1-8b",
            "model_version": "v1",
        },
    )
    assert response.status_code == 201
    assert response.json()["base_url"] is None


async def test_get_model_endpoint_by_id(client: AsyncClient) -> None:
    response = await client.post(
        "/v1/models",
        json={
            "name": "claude-sonnet",
            "provider": "anthropic",
            "model_id": "claude-3-5-sonnet",
            "model_version": "20241022",
        },
    )
    endpoint_id = response.json()["id"]

    response = await client.get(f"/v1/models/{endpoint_id}")
    assert response.status_code == 200
    assert response.json()["provider"] == "anthropic"


async def test_get_model_endpoint_not_found(client: AsyncClient) -> None:
    response = await client.get(f"/v1/models/{uuid4()}")
    assert response.status_code == 404


async def test_create_model_endpoint_rejects_unknown_provider(client: AsyncClient) -> None:
    response = await client.post(
        "/v1/models",
        json={
            "name": "gemini",
            "provider": "google",
            "model_id": "gemini-1.5",
            "model_version": "v1",
        },
    )
    assert response.status_code == 422
