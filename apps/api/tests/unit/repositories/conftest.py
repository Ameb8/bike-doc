"""Central migrated PostgreSQL setup for repository tests."""

import os
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Final

import pytest
import pytest_asyncio
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from bike_doc_api.core.config import get_settings

TEST_DATABASE_URL_ENV: Final = "BIKE_DOC_API_TEST_DATABASE_URL"


def _test_database_url() -> str:
    database_url = os.environ.get(TEST_DATABASE_URL_ENV)
    if not database_url:
        pytest.skip(f"{TEST_DATABASE_URL_ENV} is not configured")
    return database_url


@pytest.fixture(scope="session")
def migrated_test_database() -> None:
    """Run Alembic once against the dedicated repository test database."""
    database_url = _test_database_url()
    api_root = Path(__file__).resolve().parents[3]
    # A programmatic config preserves the application loggers in the shared
    # pytest process; env.py otherwise invokes fileConfig and disables them.
    alembic_config = Config()
    alembic_config.set_main_option("sqlalchemy.url", database_url)
    alembic_config.set_main_option(
        "script_location", str(api_root / "src/bike_doc_api/db/migrations")
    )
    os.environ["BIKE_DOC_API_DATABASE_URL"] = database_url
    get_settings.cache_clear()
    command.upgrade(alembic_config, "head")


@pytest_asyncio.fixture
async def db_session(migrated_test_database: None) -> AsyncIterator[AsyncSession]:
    """Yield an isolated async session for repository tests."""
    engine = create_async_engine(_test_database_url(), pool_pre_ping=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.execute(
            text(
                """
                TRUNCATE
                  background_jobs,
                  artifact_refs,
                  repair_session_events,
                  repair_turns,
                  phase_reports,
                  repair_phase_sessions,
                  repair_sessions,
                  bike_profiles,
                  users
                CASCADE;
                """,
            ),
        )
    async with session_factory() as session:
        yield session
        await session.rollback()
    await engine.dispose()
