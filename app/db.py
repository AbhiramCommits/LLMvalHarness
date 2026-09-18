from collections.abc import AsyncIterator
from typing import Any

from sqlalchemy import Result
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.config import get_settings

engine = create_async_engine(get_settings().database_url, pool_pre_ping=True)

SessionFactory = async_sessionmaker(engine, expire_on_commit=False)


async def get_session() -> AsyncIterator[AsyncSession]:
    async with SessionFactory() as session:
        yield session


def updated_rowcount(result: Result[Any]) -> int:
    """Rowcount of a DML result.

    SQLAlchemy types ``execute(dml)`` as ``Result``, but the underlying object
    is a CursorResult which carries the rowcount attribute.
    """
    return int(result.rowcount)  # type: ignore[attr-defined]
