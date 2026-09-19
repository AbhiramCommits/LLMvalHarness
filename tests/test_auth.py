import uuid

from app.auth import hash_api_key, key_prefix, verify_api_key
from app.models import ApiKey
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession


async def test_anonymous_request_rejected(anonymous_client: AsyncClient) -> None:
    response = await anonymous_client.get("/v1/task-sets")
    assert response.status_code == 401


async def test_invalid_key_rejected(anonymous_client: AsyncClient) -> None:
    response = await anonymous_client.get(
        "/v1/task-sets",
        headers={"X-API-Key": "not-a-real-key"},
    )
    assert response.status_code == 401


async def test_bearer_token_accepted(anonymous_client: AsyncClient) -> None:
    from tests.conftest import TEST_ADMIN_KEY

    response = await anonymous_client.get(
        "/v1/task-sets",
        headers={"Authorization": f"Bearer {TEST_ADMIN_KEY}"},
    )
    assert response.status_code == 200


async def test_admin_can_access_everything(client: AsyncClient) -> None:
    assert (await client.get("/v1/task-sets")).status_code == 200
    assert (await client.get("/v1/models")).status_code == 200
    assert (await client.get("/v1/review-queue")).status_code == 200
    assert (await client.get("/v1/leaderboard")).status_code == 200
    assert (await client.get("/v1/api-keys")).status_code == 200


async def test_reviewer_can_access_review_and_leaderboard(
    reviewer_client: AsyncClient,
) -> None:
    assert (await reviewer_client.get("/v1/review-queue")).status_code == 200
    assert (await reviewer_client.get("/v1/leaderboard")).status_code == 200
    assert (await reviewer_client.get("/v1/review-queue/stats")).status_code == 200


async def test_reviewer_blocked_from_admin_endpoints(
    reviewer_client: AsyncClient,
) -> None:
    assert (await reviewer_client.get("/v1/task-sets")).status_code == 403
    assert (await reviewer_client.get("/v1/models")).status_code == 403
    assert (await reviewer_client.get("/v1/api-keys")).status_code == 403
    response = await reviewer_client.post(
        "/v1/eval-runs",
        json={"task_set_id": str(uuid.uuid4()), "model_endpoint_ids": [str(uuid.uuid4())]},
    )
    assert response.status_code == 403


async def test_health_endpoints_exempt_from_auth(anonymous_client: AsyncClient) -> None:
    assert (await anonymous_client.get("/healthz")).status_code == 200
    assert (await anonymous_client.get("/readyz")).status_code == 200
    assert (await anonymous_client.get("/metrics")).status_code == 200


async def test_create_api_key_returns_raw_key_once(
    client: AsyncClient,
    session: AsyncSession,
) -> None:
    response = await client.post(
        "/v1/api-keys",
        json={"name": "ci-bot", "role": "admin"},
    )
    assert response.status_code == 201
    body = response.json()
    raw_key = body["key"]
    assert len(raw_key) > 20

    rows = (await session.execute(select(ApiKey).where(ApiKey.name == "ci-bot"))).scalars().all()
    assert len(rows) == 1
    stored = rows[0]
    assert stored.key_hash != raw_key
    assert stored.key_prefix == key_prefix(raw_key)
    assert verify_api_key(raw_key, stored.key_hash)
    assert not verify_api_key("wrong-key", stored.key_hash)

    response = await client.get("/v1/api-keys")
    keys = {row["name"] for row in response.json()}
    assert "ci-bot" in keys
    assert all("key" not in row for row in response.json())


async def test_created_key_can_authenticate(
    client: AsyncClient,
    anonymous_client: AsyncClient,
) -> None:
    response = await client.post(
        "/v1/api-keys",
        json={"name": "fresh-key", "role": "reviewer"},
    )
    raw_key = response.json()["key"]

    response = await anonymous_client.get(
        "/v1/review-queue",
        headers={"X-API-Key": raw_key},
    )
    assert response.status_code == 200


async def test_reviewer_cannot_create_api_keys(reviewer_client: AsyncClient) -> None:
    response = await reviewer_client.post(
        "/v1/api-keys",
        json={"name": "sneaky", "role": "admin"},
    )
    assert response.status_code == 403


async def test_delete_api_key(client: AsyncClient, session: AsyncSession) -> None:
    response = await client.post(
        "/v1/api-keys",
        json={"name": "to-delete", "role": "reviewer"},
    )
    key_id = response.json()["id"]

    response = await client.delete(f"/v1/api-keys/{key_id}")
    assert response.status_code == 204

    rows = (await session.execute(select(ApiKey).where(ApiKey.name == "to-delete"))).scalars().all()
    assert rows == []


def test_hash_api_key_round_trip() -> None:
    raw = "secret-key-value"
    stored = hash_api_key(raw)
    assert stored.startswith("pbkdf2_sha256$")
    assert verify_api_key(raw, stored)
    assert not verify_api_key("secret-key-value2", stored)
    assert not verify_api_key(raw, "garbage")
