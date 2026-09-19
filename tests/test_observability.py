from httpx import AsyncClient


async def test_readyz_reports_all_checks(anonymous_client: AsyncClient) -> None:
    response = await anonymous_client.get("/readyz")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert set(body["checks"]) == {"postgres", "redis", "broker"}
    assert all(check["status"] == "ok" for check in body["checks"].values())


async def test_readyz_degrades_when_checks_fail(
    anonymous_client: AsyncClient,
    monkeypatch,
) -> None:
    from app import main as main_module
    from app.schemas.common import CheckStatus

    async def _failing_checks() -> dict[str, CheckStatus]:
        return {
            "postgres": CheckStatus(status="down", error="boom"),
            "redis": CheckStatus(status="down", error="boom"),
            "broker": CheckStatus(status="down", error="boom"),
        }

    monkeypatch.setattr(main_module, "_readiness_checks", _failing_checks)

    response = await anonymous_client.get("/readyz")
    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "degraded"
