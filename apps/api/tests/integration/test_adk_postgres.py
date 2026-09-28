"""Pinned ADK/PostgreSQL migration and cross-instance compatibility.

Run after ``BIKE_DOC_API_DATABASE_URL=$TEST_URL uv run alembic upgrade head`` with
``BIKE_DOC_API_ADK_TEST_DATABASE_URL=$TEST_URL uv run pytest
tests/integration/test_adk_postgres.py``.
Use a disposable database; the version-failure check temporarily changes metadata.
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys

import pytest
from google.adk.events import Event
from google.adk.events.event_actions import EventActions
from google.genai import types
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from bike_doc_api.adk.runner import (
    DiagnosticRunner,
    DiagnosticRunnerAssistantMessageCompleted,
    DiagnosticRunnerRecoverableError,
    DiagnosticRunnerRequest,
)
from bike_doc_api.adk.sessions import (
    DIAGNOSTIC_ADK_APP_NAME,
    DIAGNOSTIC_ADK_USER_ID,
    DiagnosticADKSessionClient,
    StaleInMemoryADKSessionError,
    ensure_adk_session_available,
)
from bike_doc_api.adk.storage import open_adk_session_service
from bike_doc_api.core.config import Settings
from bike_doc_api.main import create_app
from bike_doc_api.models._ids import generate_prefixed_ulid
from bike_doc_api.schemas.common import RepairSessionPhase

TEST_URL = os.getenv("BIKE_DOC_API_ADK_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    TEST_URL is None, reason="requires disposable migrated PostgreSQL"
)


@pytest.mark.asyncio
async def test_migrated_storage_resumes_state_and_events_across_app_lifecycles(
    caplog: pytest.LogCaptureFixture,
) -> None:
    assert TEST_URL is not None
    caplog.set_level(logging.INFO)
    settings = Settings(environment="test", database_url=TEST_URL)
    first_app = create_app(settings)
    async with first_app.router.lifespan_context(first_app):
        first = first_app.state.adk_session_service
        async with first.db_engine.connect() as connection:
            version = await connection.scalar(
                text(
                    "SELECT value FROM adk_internal_metadata WHERE key='schema_version'"
                )
            )
            assert version == "1"
        client = DiagnosticADKSessionClient(first)
        bound_id = await client.create_session(
            repair_session_id="rs_integration", phase=RepairSessionPhase.DIAGNOSTIC
        )
        original = await first.get_session(
            app_name=DIAGNOSTIC_ADK_APP_NAME,
            user_id=DIAGNOSTIC_ADK_USER_ID,
            session_id=bound_id,
        )
        assert original is not None
        await first.append_event(
            original,
            Event(
                invocation_id="inv_integration",
                author="user",
                content=types.Content(parts=[types.Part.from_text(text="test turn")]),
                actions=EventActions(state_delta={"checkpoint": "persisted"}),
            ),
        )

    second_app = create_app(settings)
    child_code = """
import asyncio
import sys
from bike_doc_api.adk.sessions import DIAGNOSTIC_ADK_APP_NAME, DIAGNOSTIC_ADK_USER_ID
from bike_doc_api.adk.storage import open_adk_session_service
from bike_doc_api.core.config import Settings

async def check():
    service = await open_adk_session_service(
        Settings(environment='test', database_url=sys.argv[1])
    )
    try:
        session = await service.get_session(
            app_name=DIAGNOSTIC_ADK_APP_NAME,
            user_id=DIAGNOSTIC_ADK_USER_ID,
            session_id=sys.argv[2],
        )
        assert session is not None
        assert session.state['checkpoint'] == 'persisted'
        assert len(session.events) == 1
    finally:
        await service.close()

asyncio.run(check())
"""
    child = await asyncio.create_subprocess_exec(
        sys.executable,
        "-c",
        child_code,
        TEST_URL,
        bound_id,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await child.communicate()
    assert child.returncode == 0, stderr.decode()

    async with second_app.router.lifespan_context(second_app):
        second = second_app.state.adk_session_service
        assert second is not first
        await ensure_adk_session_available(second, adk_session_id=bound_id)
        resumed = await second.get_session(
            app_name=DIAGNOSTIC_ADK_APP_NAME,
            user_id=DIAGNOSTIC_ADK_USER_ID,
            session_id=bound_id,
        )
        assert resumed is not None
        assert resumed.id == bound_id
        assert resumed.state["checkpoint"] == "persisted"
        assert len(resumed.events) == 1
        assert resumed.events[0].content.parts[0].text == "test turn"

        async def fake_model(
            _request: DiagnosticRunnerRequest,
        ) -> tuple[DiagnosticRunnerAssistantMessageCompleted, ...]:
            return (
                DiagnosticRunnerAssistantMessageCompleted(
                    message_id="msg_test", full_text="Check the chain."
                ),
            )

        runner = DiagnosticRunner(invoker=fake_model, session_service=second)
        request = DiagnosticRunnerRequest(
            user_id="usr_integration",
            user_skill_level="beginner",
            repair_session_id="rs_integration",
            turn_id="turn_integration",
            diagnostic_session_id="phs_integration",
            adk_session_id=bound_id,
            message_text="Chain skips",
            artifact_ids=(),
            bike_profile=None,
        )
        result = await runner.run(request)
        assert result.events == (
            DiagnosticRunnerAssistantMessageCompleted(
                message_id="msg_test", full_text="Check the chain."
            ),
        )
        missing_result = await runner.run(
            DiagnosticRunnerRequest(
                user_id=request.user_id,
                user_skill_level=request.user_skill_level,
                repair_session_id=request.repair_session_id,
                turn_id=request.turn_id,
                diagnostic_session_id=request.diagnostic_session_id,
                adk_session_id="missing_bound_id",
                message_text=request.message_text,
                artifact_ids=(),
                bike_profile=None,
            )
        )
        assert len(missing_result.events) == 1
        assert isinstance(missing_result.events[0], DiagnosticRunnerRecoverableError)
        assert missing_result.events[0].code == "diagnostic_session_unavailable"
        user_id = generate_prefixed_ulid("usr_")
        bike_id = generate_prefixed_ulid("bike_")
        repair_id = generate_prefixed_ulid("rs_")
        phase_id = generate_prefixed_ulid("phs_")
        async with second.db_engine.begin() as connection:
            await connection.execute(
                text(
                    "INSERT INTO users (id, auth_subject, email, display_name) "
                    "VALUES (:id, :subject, 'test@example.com', 'Test')"
                ),
                {"id": user_id, "subject": user_id},
            )
            await connection.execute(
                text(
                    "INSERT INTO bike_profiles (id, user_id, display_name) "
                    "VALUES (:id, :user_id, 'Test')"
                ),
                {"id": bike_id, "user_id": user_id},
            )
            await connection.execute(
                text(
                    "INSERT INTO repair_sessions (id, user_id, bike_id) "
                    "VALUES (:id, :user_id, :bike_id)"
                ),
                {"id": repair_id, "user_id": user_id, "bike_id": bike_id},
            )
            await connection.execute(
                text(
                    "INSERT INTO repair_phase_sessions "
                    "(id, repair_session_id, phase, adk_session_id) "
                    "VALUES (:id, :repair_id, 'diagnostic', :adk_id)"
                ),
                {"id": phase_id, "repair_id": repair_id, "adk_id": bound_id},
            )
        with pytest.raises(DBAPIError, match="referenced by a retained phase session"):
            await second.delete_session(
                app_name=DIAGNOSTIC_ADK_APP_NAME,
                user_id=DIAGNOSTIC_ADK_USER_ID,
                session_id=bound_id,
            )
        async with second.db_engine.begin() as connection:
            await connection.execute(
                text(
                    "UPDATE repair_phase_sessions SET status='closed', "
                    "closed_at=now() WHERE id=:id"
                ),
                {"id": phase_id},
            )
        with pytest.raises(DBAPIError, match="referenced by a retained phase session"):
            await second.delete_session(
                app_name=DIAGNOSTIC_ADK_APP_NAME,
                user_id=DIAGNOSTIC_ADK_USER_ID,
                session_id=bound_id,
            )
        with pytest.raises(StaleInMemoryADKSessionError):
            await ensure_adk_session_available(
                second, adk_session_id="missing_bound_id"
            )
        assert (
            await second.get_session(
                app_name=DIAGNOSTIC_ADK_APP_NAME,
                user_id=DIAGNOSTIC_ADK_USER_ID,
                session_id="missing_bound_id",
            )
            is None
        )
        assert bound_id not in str(result.events)
        assert "persisted" not in str(result.events)

    routine_logs = caplog.text
    assert bound_id not in routine_logs
    assert "test turn" not in routine_logs
    assert "persisted" not in routine_logs


@pytest.mark.asyncio
async def test_startup_rejects_wrong_schema_version_without_replacing_it() -> None:
    assert TEST_URL is not None
    settings = Settings(environment="test", database_url=TEST_URL)
    service = await open_adk_session_service(settings)
    try:
        async with service.db_engine.begin() as connection:
            await connection.execute(
                text(
                    "UPDATE adk_internal_metadata SET value='0' "
                    "WHERE key='schema_version'"
                )
            )
        with pytest.raises(
            RuntimeError, match="Incompatible ADK session schema version"
        ):
            await open_adk_session_service(settings)
        async with service.db_engine.connect() as connection:
            assert (
                await connection.scalar(
                    text(
                        "SELECT value FROM adk_internal_metadata "
                        "WHERE key='schema_version'"
                    )
                )
                == "0"
            )
    finally:
        async with service.db_engine.begin() as connection:
            await connection.execute(
                text(
                    "UPDATE adk_internal_metadata SET value='1' "
                    "WHERE key='schema_version'"
                )
            )
        await service.close()


@pytest.mark.asyncio
async def test_startup_rejects_partial_schema_without_recreating_index() -> None:
    assert TEST_URL is not None
    settings = Settings(environment="test", database_url=TEST_URL)
    service = await open_adk_session_service(settings)
    try:
        async with service.db_engine.begin() as connection:
            await connection.execute(text("DROP INDEX idx_events_app_user_session_ts"))
        with pytest.raises(RuntimeError, match="Incomplete ADK event index"):
            await open_adk_session_service(settings)
        async with service.db_engine.connect() as connection:
            assert (
                await connection.scalar(
                    text(
                        "SELECT count(*) FROM pg_indexes WHERE "
                        "indexname='idx_events_app_user_session_ts'"
                    )
                )
                == 0
            )
    finally:
        async with service.db_engine.begin() as connection:
            await connection.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS idx_events_app_user_session_ts "
                    "ON events (app_name, user_id, session_id, timestamp DESC)"
                )
            )
        await service.close()
