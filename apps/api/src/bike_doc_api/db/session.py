"""Database session wiring."""

from collections.abc import AsyncIterator
from functools import lru_cache

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from bike_doc_api.core.config import get_settings


@lru_cache
def get_sessionmaker(database_url: str) -> async_sessionmaker[AsyncSession]:
    """Return a cached async sessionmaker for a database URL."""

    engine = create_async_engine(database_url, pool_pre_ping=True)
    return async_sessionmaker(engine, expire_on_commit=False)


SessionLocal = get_sessionmaker(get_settings().database_url)


async def get_session_for_database_url(
    database_url: str,
) -> AsyncIterator[AsyncSession]:
    """Yield an async database session for a configured database URL."""

    sessionmaker = get_sessionmaker(database_url)
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
