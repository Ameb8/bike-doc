"""Deferred session transactions with real migrated PostgreSQL and durable ADK."""

from __future__ import annotations

import asyncio
import os
import sys
from collections.abc import AsyncIterator

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from bike_doc_api.adk.sessions import (
    DIAGNOSTIC_ADK_APP_NAME,
    DIAGNOSTIC_ADK_USER_ID,
    DiagnosticADKSessionClient,
    StaleInMemoryADKSessionError,
)
from bike_doc_api.adk.storage import open_adk_session_service
from bike_doc_api.api.v1.turns import get_turn_service
from bike_doc_api.core.config import Settings
from bike_doc_api.models.bike import BikeProfile
from bike_doc_api.models.repair_session import RepairPhaseSession, RepairSession
from bike_doc_api.models.user import User
from bike_doc_api.repositories.repair_sessions import RepairPhaseSessionRepository
from bike_doc_api.schemas.turn import TurnCreate

TEST_URL = os.getenv("BIKE_DOC_API_ADK_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    TEST_URL is None, reason="requires disposable migrated PostgreSQL"
)


@pytest.fixture
async def context() -> AsyncIterator[
    tuple[Settings, async_sessionmaker[AsyncSession], str, str]
]:
    assert TEST_URL is not None
    settings = Settings(environment="test", database_url=TEST_URL)
    adk = await open_adk_session_service(settings)
    sessions = async_sessionmaker(adk.db_engine, expire_on_commit=False)
    async with sessions() as session, session.begin():
        user = User(
            auth_subject=os.urandom(16).hex(),
            email="test@example.com",
            display_name="Test",
        )
        session.add(user)
        await session.flush()
        bike = BikeProfile(user_id=user.id, display_name="Test")
        session.add(bike)
        await session.flush()
        repair = RepairSession(user_id=user.id, bike_id=bike.id)
        session.add(repair)
        await session.flush()
        phase = RepairPhaseSession(
            repair_session_id=repair.id,
            phase="diagnostic",
            diagnostic_report_schema_version="diagnostic_report.v2",
        )
        session.add(phase)
        await session.flush()
        phase_id, repair_id = phase.id, repair.id
    try:
        yield settings, sessions, phase_id, repair_id
    finally:
        async with sessions() as session, session.begin():
            phase_ids = list(
                await session.scalars(
                    select(RepairPhaseSession.id).where(
                        RepairPhaseSession.repair_session_id == repair_id
                    )
                )
            )
            await session.execute(
                text("DELETE FROM repair_sessions WHERE id=:id"), {"id": repair_id}
            )
            await session.execute(
                text("DELETE FROM bike_profiles WHERE id=:id"), {"id": bike.id}
            )
            await session.execute(
                text("DELETE FROM users WHERE id=:id"), {"id": user.id}
            )
            for retained_id in phase_ids:
                await session.execute(
                    text("DELETE FROM sessions WHERE id=:id"),
                    {"id": f"adk_diagnostic_{retained_id}"},
                )
        await adk.close()


async def test_migration_roundtrip_and_null_constraints(context: tuple) -> None:
    _, sessions, phase_id, repair_id = context

    async def migrate(target: str, command: str) -> int:
        env = dict(os.environ, BIKE_DOC_API_DATABASE_URL=TEST_URL)
        child = await asyncio.create_subprocess_exec(
            sys.executable,
            "-m",
            "alembic",
            command,
            target,
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await child.communicate()
        assert child.returncode is not None
        return child.returncode

    async with sessions() as session, session.begin():
        session.add(RepairPhaseSession(repair_session_id=repair_id, phase="planning"))
        await session.flush()  # Multiple null IDs coexist.
        for value in ("", "   "):
            with pytest.raises(IntegrityError):
                async with session.begin_nested():
                    phase = await session.get(RepairPhaseSession, phase_id)
                    phase.adk_session_id = value
                    await session.flush()
    assert await migrate("0010", "downgrade") == 0
    async with sessions() as session:
        index = await session.scalar(
            text(
                "SELECT indexdef FROM pg_indexes "
                "WHERE indexname='ux_repair_phase_sessions_adk_session'"
            )
        )
        assert "WHERE" not in index
    assert await migrate("head", "upgrade") == 0
    # The earlier nullable migration must refuse downgrade with retained nulls.
    assert await migrate("0009", "downgrade") != 0
    async with sessions() as session, session.begin():
        await session.execute(
            text(
                "UPDATE repair_phase_sessions SET adk_session_id=id "
                "WHERE repair_session_id=:id"
            ),
            {"id": repair_id},
        )
        with pytest.raises(IntegrityError):
            async with session.begin_nested():
                await session.execute(
                    text(
                        "UPDATE repair_phase_sessions SET adk_session_id=:phase_id "
                        "WHERE repair_session_id=:id AND phase='planning'"
                    ),
                    {"phase_id": phase_id, "id": repair_id},
                )
    assert await migrate("0009", "downgrade") == 0
    assert await migrate("head", "upgrade") == 0
    async with sessions() as session:
        index = await session.scalar(
            text(
                "SELECT indexdef FROM pg_indexes "
                "WHERE indexname='ux_repair_phase_sessions_adk_session'"
            )
        )
        assert "WHERE (adk_session_id IS NOT NULL)" in index


async def test_concurrent_independent_clients_bind_same_identity(
    context: tuple,
) -> None:
    settings, sessions, phase_id, _ = context
    first = await open_adk_session_service(settings)
    second = await open_adk_session_service(settings)
    barrier = asyncio.Barrier(2)

    async def execute(service: object) -> str:
        async with sessions() as session, session.begin():
            phase = await session.get(RepairPhaseSession, phase_id)
            assert phase.adk_session_id is None
            await barrier.wait()
            return await DiagnosticADKSessionClient(service).ensure_or_resume(
                phase_session=phase,
                phase_sessions=RepairPhaseSessionRepository(session),
            )

    try:
        one, two = await asyncio.gather(execute(first), execute(second))
        assert one == two == f"adk_diagnostic_{phase_id}"
        async with sessions() as session:
            phase = await session.get(RepairPhaseSession, phase_id)
            assert phase.adk_session_id == one
            assert phase.diagnostic_report_schema_version == "diagnostic_report.v2"
            assert (
                await DiagnosticADKSessionClient(second).ensure_or_resume(
                    phase_session=phase,
                    phase_sessions=RepairPhaseSessionRepository(session),
                )
                == one
            )
            assert (
                await session.scalar(
                    text("SELECT count(*) FROM sessions WHERE id=:id"), {"id": one}
                )
                == 1
            )
    finally:
        await first.close()
        await second.close()


@pytest.mark.parametrize("crash_point", ["before_binding", "before_commit"])
async def test_lost_binding_commit_then_resume_in_another_process(
    context: tuple,
    monkeypatch: pytest.MonkeyPatch,
    crash_point: str,
) -> None:
    settings, sessions, phase_id, _ = context
    first = await open_adk_session_service(settings)
    try:
        async with sessions() as session:
            phase = await session.get(RepairPhaseSession, phase_id)
            identity = f"adk_diagnostic_{phase_id}"
            client = DiagnosticADKSessionClient(first)
            repo = RepairPhaseSessionRepository(session)
            if crash_point == "before_binding":

                async def crash(**_: object) -> str:
                    raise RuntimeError("injected loss after ADK creation")

                with monkeypatch.context() as patch:
                    patch.setattr(repo, "bind_adk_session_if_null", crash)
                    with pytest.raises(RuntimeError, match="injected loss"):
                        await client.ensure_or_resume(
                            phase_session=phase, phase_sessions=repo
                        )
            else:
                assert (
                    await client.ensure_or_resume(
                        phase_session=phase, phase_sessions=repo
                    )
                    == identity
                )
            await session.rollback()  # Durable ADK creation survived; app commit lost.
        async with sessions() as session:
            phase = await session.get(RepairPhaseSession, phase_id)
            assert phase.adk_session_id is None
    finally:
        await first.close()

    child_code = """
import asyncio, sys
from sqlalchemy.ext.asyncio import async_sessionmaker
from bike_doc_api.adk.sessions import DiagnosticADKSessionClient
from bike_doc_api.adk.storage import open_adk_session_service
from bike_doc_api.core.config import Settings
from bike_doc_api.models.repair_session import RepairPhaseSession
from bike_doc_api.repositories.repair_sessions import RepairPhaseSessionRepository
async def run():
    adk = await open_adk_session_service(
        Settings(environment='test', database_url=sys.argv[1]))
    try:
        sessions = async_sessionmaker(adk.db_engine, expire_on_commit=False)
        async with sessions() as session, session.begin():
            phase = await session.get(RepairPhaseSession, sys.argv[2])
            identity = await DiagnosticADKSessionClient(adk).ensure_or_resume(
                phase_session=phase,
                phase_sessions=RepairPhaseSessionRepository(session))
            assert identity == sys.argv[3]
    finally:
        await adk.close()
asyncio.run(run())
"""
    for _ in range(2):  # Recovery followed by exact bound resume in another process.
        child = await asyncio.create_subprocess_exec(
            sys.executable,
            "-c",
            child_code,
            TEST_URL,
            phase_id,
            identity,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await child.communicate()
        assert child.returncode == 0, stderr.decode()
    async with sessions() as session:
        assert (
            await session.scalar(
                text("SELECT count(*) FROM sessions WHERE id=:id"), {"id": identity}
            )
            == 1
        )


async def test_repository_preserves_winner_and_missing_binding_fails_closed(
    context: tuple,
) -> None:
    settings, sessions, phase_id, _ = context
    async with sessions() as session, session.begin():
        repo = RepairPhaseSessionRepository(session)
        assert (
            await repo.bind_adk_session_if_null(
                phase_session_id=phase_id, adk_session_id="confirmed_missing"
            )
            == "confirmed_missing"
        )
    adk = await open_adk_session_service(settings)
    try:
        async with sessions() as session, session.begin():
            repo = RepairPhaseSessionRepository(session)
            assert (
                await repo.bind_adk_session_if_null(
                    phase_session_id=phase_id, adk_session_id="replacement"
                )
                == "confirmed_missing"
            )
            phase = await repo.get(phase_id)
            with pytest.raises(StaleInMemoryADKSessionError):
                await DiagnosticADKSessionClient(adk).ensure_or_resume(
                    phase_session=phase, phase_sessions=repo
                )
        for identity in (
            "confirmed_missing",
            "replacement",
            f"adk_diagnostic_{phase_id}",
        ):
            assert (
                await adk.get_session(
                    app_name=DIAGNOSTIC_ADK_APP_NAME,
                    user_id=DIAGNOSTIC_ADK_USER_ID,
                    session_id=identity,
                )
                is None
            )
    finally:
        await adk.close()


@pytest.mark.parametrize("selection", ["legacy", "durable_queue"])
async def test_acceptance_ignores_unavailable_adk_and_snapshots_new_phase(
    context: tuple,
    monkeypatch: pytest.MonkeyPatch,
    selection: str,
) -> None:
    settings, sessions, phase_id, repair_id = context
    from bike_doc_api.adk import sessions as adk_sessions
    from bike_doc_api.api import deps

    def unavailable(*_: object, **__: object) -> None:
        raise AssertionError("acceptance must not access unavailable ADK")

    monkeypatch.setattr(deps, "get_adk_session_service", unavailable)
    monkeypatch.setattr(
        adk_sessions.DiagnosticADKSessionClient, "create_session", unavailable
    )
    async with sessions() as session, session.begin():
        await session.execute(
            text("DELETE FROM repair_phase_sessions WHERE id=:id"), {"id": phase_id}
        )
    async with sessions() as session:
        repair = await session.get(RepairSession, repair_id)
        user = await session.get(User, repair.user_id)
        service = get_turn_service(
            session,
            settings.model_copy(
                update={
                    "profile_inference_execution": selection,
                }
            ),
        )
        accepted = await service.accept_turn(
            current_user=user,
            repair_session_id=repair_id,
            request=TurnCreate.model_validate(
                {
                    "schema_version": "ai_turn.v1",
                    "client_turn_id": "deferred",
                    "message": {"text": "Chain skips", "artifact_ids": []},
                }
            ),
        )
        assert "adk" not in str(accepted.model_dump()).lower()
    async with sessions() as session:
        phase = await session.scalar(
            select(RepairPhaseSession).where(
                RepairPhaseSession.repair_session_id == repair_id
            )
        )
        assert phase.id.startswith("phs_")
        assert phase.adk_session_id is None
        assert phase.diagnostic_report_schema_version == "diagnostic_report.v2"


async def test_legacy_executor_initializes_and_missing_binding_terminalizes(
    context: tuple,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from unittest.mock import AsyncMock

    from bike_doc_api.adk import background
    from bike_doc_api.models.event import RepairSessionEvent

    settings, sessions, phase_id, repair_id = context
    async with sessions() as session:
        repair = await session.get(RepairSession, repair_id)
        user = await session.get(User, repair.user_id)
        accepted = await get_turn_service(session, settings).accept_turn(
            current_user=user,
            repair_session_id=repair_id,
            request=TurnCreate.model_validate(
                {
                    "schema_version": "ai_turn.v1",
                    "client_turn_id": "legacy",
                    "message": {"text": "Chain skips", "artifact_ids": []},
                }
            ),
        )
    adk = await open_adk_session_service(settings)
    monkeypatch.setattr(background, "get_settings", lambda: settings)
    monkeypatch.setattr(background, "get_adk_session_service", lambda: adk)
    monkeypatch.setattr(
        background, "validate_diagnostic_runtime_configuration", lambda _: None
    )
    orchestrator = AsyncMock()
    orchestrator.process_turn.return_value = None
    monkeypatch.setattr(
        background, "_build_background_orchestrator", lambda **_: orchestrator
    )
    try:
        await background.execute_diagnostic_turn_background(
            user.id, repair_id, accepted.turn_id
        )
        orchestrator.process_turn.assert_awaited_once()
        async with sessions() as session, session.begin():
            phase = await session.get(RepairPhaseSession, phase_id)
            assert phase.adk_session_id == f"adk_diagnostic_{phase_id}"
            # Simulate confirmed missing storage without deleting retained ADK
            # data (the retention guard intentionally prevents that operation).
            phase.adk_session_id = "missing_legacy_binding"
        await background.execute_diagnostic_turn_background(
            user.id, repair_id, accepted.turn_id
        )
        orchestrator.process_turn.assert_awaited_once()  # Failure before orchestration.
        async with sessions() as session:
            phase = await session.get(RepairPhaseSession, phase_id)
            assert phase.adk_session_id == "missing_legacy_binding"
            repair = await session.get(RepairSession, repair_id)
            assert repair.status == "awaiting_user"
            events = list(
                await session.scalars(
                    select(RepairSessionEvent)
                    .where(RepairSessionEvent.repair_session_id == repair_id)
                    .order_by(RepairSessionEvent.sequence)
                )
            )
            assert [event.type for event in events] == [
                "turn.started",
                "error",
                "turn.completed",
            ]
            assert events[1].data["code"] == "diagnostic_processing_error"
            assert events[1].data["retryable"] is True
            assert "adk" not in str([event.data for event in events]).lower()
    finally:
        await adk.close()


@pytest.mark.parametrize("selection", ["legacy", "durable_queue"])
async def test_phase_creation_rolls_back_with_acceptance_commit_failure(
    context: tuple,
    monkeypatch: pytest.MonkeyPatch,
    selection: str,
) -> None:
    from bike_doc_api.core.errors import ServerError
    from bike_doc_api.models.event import RepairSessionEvent
    from bike_doc_api.models.repair_session import RepairTurn

    settings, sessions, phase_id, repair_id = context
    async with sessions() as session, session.begin():
        await session.execute(
            text("DELETE FROM repair_phase_sessions WHERE id=:id"), {"id": phase_id}
        )
    async with sessions() as session:
        repair = await session.get(RepairSession, repair_id)
        user = await session.get(User, repair.user_id)
        service = get_turn_service(
            session,
            settings.model_copy(
                update={
                    "profile_inference_execution": selection,
                }
            ),
        )

        async def fail_commit() -> None:
            raise RuntimeError("injected acceptance commit loss")

        # TurnService captured the session callback at composition time.
        monkeypatch.setattr(service, "_commit", fail_commit)
        with pytest.raises(ServerError):
            await service.accept_turn(
                current_user=user,
                repair_session_id=repair_id,
                request=TurnCreate.model_validate(
                    {
                        "schema_version": "ai_turn.v1",
                        "client_turn_id": "rollback",
                        "message": {"text": "Chain skips", "artifact_ids": []},
                    }
                ),
            )
    async with sessions() as session:
        for model in (RepairPhaseSession, RepairTurn, RepairSessionEvent):
            assert (
                list(
                    await session.scalars(
                        select(model).where(model.repair_session_id == repair_id)
                    )
                )
                == []
            )
        repair = await session.get(RepairSession, repair_id)
        assert repair.status == "created"
