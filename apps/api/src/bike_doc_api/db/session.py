"""Database session wiring."""

from collections.abc import AsyncIterator
from functools import lru_cache

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from bike_doc_api.core.config import get_settings
from bike_doc_api.db.pool_observation import observe_pool


def get_sessionmaker(
    database_url: str,
    pool_size: int | None = None,
    pool_timeout: float | None = None,
    role: str = "api",
) -> async_sessionmaker[AsyncSession]:
    """Return a cached async sessionmaker for a database URL."""

    settings = get_settings()
    return _sessionmaker(
        database_url,
        settings.api_pool_size if pool_size is None else pool_size,
        settings.database_pool_timeout_seconds
        if pool_timeout is None
        else pool_timeout,
        role,
    )


@lru_cache
def _sessionmaker(
    database_url: str, pool_size: int, pool_timeout: float, role: str
) -> async_sessionmaker[AsyncSession]:
    engine = create_async_engine(
        database_url,
        pool_pre_ping=True,
        pool_size=pool_size,
        max_overflow=0,
        pool_timeout=pool_timeout,
    )
    observe_pool(engine, role)
    return async_sessionmaker(engine, expire_on_commit=False)


SessionLocal = get_sessionmaker(get_settings().database_url)


async def get_session_for_database_url(
    database_url: str,
    *,
    pool_size: int | None = None,
    pool_timeout: float | None = None,
) -> AsyncIterator[AsyncSession]:
    """Yield an async database session for a configured database URL."""

    sessionmaker = get_sessionmaker(database_url, pool_size, pool_timeout)
    async with sessionmaker() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def get_session() -> AsyncIterator[AsyncSession]:
    """Yield an async database session."""
    async for session in get_session_for_database_url(get_settings().database_url):
        yield session
