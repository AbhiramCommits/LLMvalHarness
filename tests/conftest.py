import asyncio
import uuid
from collections.abc import AsyncIterator

import asyncpg
import pytest
import pytest_asyncio
from app.config import get_settings
from app.db import get_session
from app.main import create_app
from app.models import Base
from httpx import ASGITransport, AsyncClient
from sqlalchemy import insert, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

TEST_DATABASE = "eval_test"

TEST_ADMIN_KEY = "test-admin-key-4f2a9c1e8b7d"
TEST_REVIEWER_KEY = "test-reviewer-key-9c3b1a5d2e6f"

_ENUM_TYPES = [
    "api_key_role",
    "grader_type",
    "provider",
    "eval_run_status",
    "run_item_status",
    "grader_kind",
    "review_reason",
    "review_status",
]


def _credentials() -> tuple[str, str, str, str]:
    url = make_url(get_settings().database_url)
    return str(url.username), str(url.password), str(url.host), str(url.port)


def _admin_dsn() -> str:
    username, password, host, port = _credentials()
    return f"postgresql://{username}:{password}@{host}:{port}/postgres"


def _test_dsn() -> str:
    username, password, host, port = _credentials()
    return f"postgresql+asyncpg://{username}:{password}@{host}:{port}/{TEST_DATABASE}"


async def _ensure_test_database() -> None:
    conn = await asyncpg.connect(_admin_dsn())
    try:
        exists = await conn.fetchval(
            "SELECT 1 FROM pg_database WHERE datname = $1",
            TEST_DATABASE,
        )
        if not exists:
            await conn.execute(f'CREATE DATABASE "{TEST_DATABASE}"')
    finally:
        await conn.close()


async def _reset_schema() -> None:
    engine = create_async_engine(_test_dsn())
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        for enum_type in _ENUM_TYPES:
            await conn.execute(text(f"DROP TYPE IF EXISTS {enum_type} CASCADE"))
        await conn.run_sync(Base.metadata.create_all)
    await engine.dispose()


async def _seed_api_keys() -> None:
    from app.auth import hash_api_key, key_prefix
    from app.models import ApiKey, ApiKeyRole

    engine = create_async_engine(_test_dsn())
    async with engine.begin() as conn:
        for name, raw_key, role in [
            ("test-admin", TEST_ADMIN_KEY, ApiKeyRole.admin),
            ("test-reviewer", TEST_REVIEWER_KEY, ApiKeyRole.reviewer),
        ]:
            await conn.execute(
                insert(ApiKey).values(
                    id=uuid.uuid4(),
                    name=name,
                    role=role,
                    key_prefix=key_prefix(raw_key),
                    key_hash=hash_api_key(raw_key),
                )
            )
    await engine.dispose()


@pytest.fixture(scope="session", autouse=True)
def _database() -> None:
    asyncio.run(_ensure_test_database())
    asyncio.run(_reset_schema())
    asyncio.run(_seed_api_keys())


@pytest.fixture(autouse=True)
def _no_broker(monkeypatch: pytest.MonkeyPatch) -> None:
    """Prevent tests from publishing stray Celery messages to Redis."""
    from app.workers import execute as execute_module

    monkeypatch.setattr(execute_module, "enqueue_run_item", lambda run_item_id: None)
    monkeypatch.setattr(execute_module, "publish_dead_letter", lambda run_item_id: None)


@pytest_asyncio.fixture
async def session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine(_test_dsn(), poolclass=NullPool)
    async with engine.connect() as conn:
        transaction = await conn.begin()
        factory = async_sessionmaker(
            conn,
            expire_on_commit=False,
            join_transaction_mode="create_savepoint",
        )
        async with factory() as s:
            yield s
        await transaction.rollback()
    await engine.dispose()


@pytest_asyncio.fixture
async def session_factory(session: AsyncSession) -> async_sessionmaker[AsyncSession]:
    """Session factory bound to the test connection (used by the executor)."""
    return async_sessionmaker(
        bind=session.bind,
        expire_on_commit=False,
        join_transaction_mode="create_savepoint",
    )


@pytest_asyncio.fixture
async def real_session_factory() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """Committed (non-rollback) sessions for cross-connection race tests."""
    engine = create_async_engine(_test_dsn())
    factory = async_sessionmaker(engine, expire_on_commit=False)
    yield factory
    await engine.dispose()


@pytest_asyncio.fixture
async def client(session: AsyncSession) -> AsyncIterator[AsyncClient]:
    async for c in _api_client(session, TEST_ADMIN_KEY):
        yield c


@pytest_asyncio.fixture
async def reviewer_client(session: AsyncSession) -> AsyncIterator[AsyncClient]:
    async for c in _api_client(session, TEST_REVIEWER_KEY):
        yield c


@pytest_asyncio.fixture
async def anonymous_client(session: AsyncSession) -> AsyncIterator[AsyncClient]:
    async for c in _api_client(session, None):
        yield c


async def _api_client(
    session: AsyncSession,
    api_key: str | None,
) -> AsyncIterator[AsyncClient]:
    app = create_app()
    app.state.auth_session_factory = async_sessionmaker(
        bind=session.bind,
        expire_on_commit=False,
        join_transaction_mode="create_savepoint",
    )

    async def _override_get_session() -> AsyncIterator[AsyncSession]:
        yield session

    app.dependency_overrides[get_session] = _override_get_session
    transport = ASGITransport(app=app)
    headers = {"X-API-Key": api_key} if api_key else {}
    async with AsyncClient(
        transport=transport,
        base_url="http://test",
        headers=headers,
    ) as c:
        yield c
