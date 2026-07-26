"""Diagnostic persistence repository tests."""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Final

import pytest
import pytest_asyncio
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from bike_doc_api.core.config import get_settings
from bike_doc_api.models.artifact import ArtifactRef
from bike_doc_api.models.bike import BikeProfile
from bike_doc_api.models.observation_extraction import ObservationExtractionRun
from bike_doc_api.models.phase_report import PhaseReport
from bike_doc_api.models.repair_session import (
    RepairPhaseSession,
    RepairSession,
    RepairTurn,
)
from bike_doc_api.models.user import User
from bike_doc_api.repositories.artifacts import ArtifactRepository
from bike_doc_api.repositories.bikes import BikeRepository
from bike_doc_api.repositories.events import RepairSessionEventRepository
from bike_doc_api.repositories.observation_extraction import (
    ObservationExtractionRunRepository,
)
from bike_doc_api.repositories.repair_sessions import (
    RepairPhaseSessionRepository,
    RepairSessionRepository,
    RepairTurnRepository,
)
from bike_doc_api.repositories.reports import PhaseReportRepository
from bike_doc_api.repositories.users import UserRepository

TEST_DATABASE_URL_ENV: Final = "BIKE_DOC_API_TEST_DATABASE_URL"
CONTENT_SHA256: Final = "a" * 64


def _test_database_url() -> str:
    database_url = os.environ.get(TEST_DATABASE_URL_ENV)
    if not database_url:
        pytest.skip(f"{TEST_DATABASE_URL_ENV} is not configured")
    return database_url


@pytest.fixture(scope="session", autouse=True)
def migrated_test_database() -> None:
    """Run Alembic once against the dedicated repository test database."""
    database_url = _test_database_url()
    api_root = Path(__file__).resolve().parents[3]
    alembic_config = Config(api_root / "alembic.ini")
    alembic_config.set_main_option("sqlalchemy.url", database_url)
    os.environ["BIKE_DOC_API_DATABASE_URL"] = database_url
    get_settings.cache_clear()
    command.upgrade(alembic_config, "head")


@pytest_asyncio.fixture
async def db_session() -> AsyncIterator[AsyncSession]:
    """Yield an isolated async session for repository tests."""
    engine = create_async_engine(_test_database_url(), pool_pre_ping=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.execute(
            text(
                """
                TRUNCATE
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


async def _create_user_bike_session(
    db_session: AsyncSession,
) -> tuple[User, BikeProfile, RepairSession]:
    user = await UserRepository(db_session).add(
        User(
            auth_subject="auth|user",
            email="rider@example.com",
            display_name="Rider",
        ),
    )
    bike = await BikeRepository(db_session).add(
        BikeProfile(user_id=user.id, display_name="Commuter"),
    )
    repair_session = await RepairSessionRepository(db_session).add(
        RepairSession(user_id=user.id, bike_id=bike.id),
    )
    return user, bike, repair_session


async def test_repositories_create_get_and_list_full_diagnostic_graph(
    db_session: AsyncSession,
) -> None:
    user, bike, repair_session = await _create_user_bike_session(db_session)
    phase_session = await RepairPhaseSessionRepository(db_session).add(
        RepairPhaseSession(
            repair_session_id=repair_session.id,
            phase="diagnostic",
            adk_session_id="adk-session-1",
        ),
    )
    turn = await RepairTurnRepository(db_session).add(
        RepairTurn(
            repair_session_id=repair_session.id,
            repair_phase_session_id=phase_session.id,
            client_turn_id="turn-client-1",
            request_hash="turn-hash",
            phase="diagnostic",
            message={"artifact_ids": [], "text": "chain skips"},
            start_event_sequence=1,
        ),
    )
    event = await RepairSessionEventRepository(db_session).append_for_session(
        repair_session_id=repair_session.id,
        turn_id=turn.id,
        event_type="turn.started",
        data={"turn_id": turn.id, "phase": "diagnostic"},
    )
    artifact = await ArtifactRepository(db_session).add(
        ArtifactRef(
            user_id=user.id,
            repair_session_id=repair_session.id,
            client_artifact_id="artifact-client-1",
            request_hash="artifact-hash",
            purpose="diagnostic_photo",
            media_type="image",
            mime_type="image/jpeg",
            filename="derailleur.jpg",
            byte_size=123,
            status="ready",
            content_sha256=CONTENT_SHA256,
            storage_provider="local",
            storage_path="objects/derailleur.jpg",
        ),
    )
    report = await PhaseReportRepository(db_session).add(
        PhaseReport(
            repair_session_id=repair_session.id,
            repair_phase_session_id=phase_session.id,
            type="diagnostic",
            schema_version="diagnostic_report.v1",
            phase="diagnostic",
            summary="Cable tension likely needs adjustment.",
            safety_flags=[],
            source_artifact_ids=[artifact.id],
            payload={
                "schema_version": "diagnostic_report.v1",
                "primary_diagnosis": {
                    "component": "rear derailleur",
                    "issue": "low cable tension",
                    "confidence": "medium",
                },
                "alternate_hypotheses": [],
                "evidence_summary": "User reports skipping.",
                "repair_estimate": {
                    "difficulty": "easy",
                    "difficulty_notes": "A basic indexing adjustment is likely.",
                    "tools_required": ["bike stand or safe way to lift rear wheel"],
                    "parts_required": [],
                    "repair_time": {"low_minutes": 10, "high_minutes": 30},
                    "shop_repair_cost": {
                        "low_usd": 20,
                        "high_usd": 60,
                        "notes": "Estimate only; actual shop pricing varies.",
                    },
                },
                "key_artifact_ids": [artifact.id],
                "user_skill_level": "unknown",
                "safety_flags": [],
                "diagnostic_session_id": phase_session.id,
            },
        ),
    )
    await db_session.flush()

    assert await UserRepository(db_session).get(user.id) == user
    assert (
        await BikeRepository(db_session).get_owned_active(
            bike_id=bike.id,
            user_id=user.id,
        )
        == bike
    )
    assert (
        await RepairSessionRepository(db_session).get_owned(
            repair_session_id=repair_session.id,
            user_id=user.id,
        )
        == repair_session
    )
    assert (
        await RepairPhaseSessionRepository(db_session).get_for_session_phase(
            repair_session_id=repair_session.id,
            phase="diagnostic",
        )
        == phase_session
    )
    assert (
        await RepairTurnRepository(db_session).get_by_client_turn_id(
            repair_session_id=repair_session.id,
            client_turn_id="turn-client-1",
        )
        == turn
    )
    assert event.id.startswith("evt_")
    assert event.sequence == 1
    assert (
        await ArtifactRepository(db_session).get_by_client_artifact_id(
            user_id=user.id,
            client_artifact_id="artifact-client-1",
        )
        == artifact
    )
    assert (
        await PhaseReportRepository(db_session).get_for_session(
            repair_session_id=repair_session.id,
            report_id=report.id,
        )
        == report
    )
    assert await RepairSessionEventRepository(db_session).list_after_sequence(
        repair_session_id=repair_session.id,
        after_sequence=0,
    ) == [event]


async def test_event_append_allocates_sequences_and_updates_session(
    db_session: AsyncSession,
) -> None:
    _, _, repair_session = await _create_user_bike_session(db_session)
    events = RepairSessionEventRepository(db_session)

    first = await events.append_for_session(
        repair_session_id=repair_session.id,
        event_type="heartbeat",
        data={"ok": True},
    )
    second = await events.append_for_session(
        repair_session_id=repair_session.id,
        event_type="assistant.delta",
        data={"text": "Check the chain."},
    )
    await db_session.flush()

    refreshed = await RepairSessionRepository(db_session).get(repair_session.id)
    assert first.sequence == 1
    assert second.sequence == 2
    assert refreshed is not None
    assert refreshed.latest_event_sequence == 2
    assert [
        event.sequence
        for event in await events.list_after_sequence(
            repair_session_id=repair_session.id,
            after_sequence=0,
        )
    ] == [1, 2]


async def test_jsonb_shape_constraints_are_enforced(
    db_session: AsyncSession,
) -> None:
    user, bike, _ = await _create_user_bike_session(db_session)
    valid_session = RepairSession(
        user_id=user.id,
        bike_id=bike.id,
        client_session_id="client-session-valid",
        request_hash="hash-valid",
        current_input_request=None,
        execution_progress=None,
    )
    db_session.add(valid_session)
    await db_session.flush()

    await db_session.rollback()
    user, bike, _ = await _create_user_bike_session(db_session)
    bad_session = RepairSession(
        user_id=user.id,
        bike_id=bike.id,
        current_input_request=[],
    )
    db_session.add(bad_session)

    with pytest.raises(IntegrityError):
        await db_session.flush()


async def test_unique_idempotency_and_parent_constraints_are_enforced(
    db_session: AsyncSession,
) -> None:
    user, _, repair_session = await _create_user_bike_session(db_session)
    duplicate = RepairSession(
        user_id=user.id,
        bike_id=repair_session.bike_id,
        client_session_id="client-session-1",
        request_hash="hash-1",
    )
    db_session.add_all(
        [
            duplicate,
            RepairSession(
                user_id=user.id,
                bike_id=repair_session.bike_id,
                client_session_id="client-session-1",
                request_hash="hash-1",
            ),
        ],
    )

    with pytest.raises(IntegrityError):
        await db_session.flush()

    await db_session.rollback()
    user, _, repair_session = await _create_user_bike_session(db_session)
    db_session.add(
        ArtifactRef(
            user_id=user.id,
            bike_id=repair_session.bike_id,
            purpose="diagnostic_photo",
            media_type="image",
            mime_type="image/jpeg",
            filename="bad-parent.jpg",
            byte_size=10,
            content_sha256=CONTENT_SHA256,
            storage_provider="local",
            storage_path="objects/bad-parent.jpg",
        ),
    )

    with pytest.raises(IntegrityError):
        await db_session.flush()


async def _create_image_turn(
    db_session: AsyncSession,
) -> tuple[RepairSession, RepairTurn]:
    _, _, repair_session = await _create_user_bike_session(db_session)
    phase_session = await RepairPhaseSessionRepository(db_session).add(
        RepairPhaseSession(
            repair_session_id=repair_session.id,
            phase="diagnostic",
            adk_session_id="adk-image-run",
        ),
    )
    turn = await RepairTurnRepository(db_session).add(
        RepairTurn(
            repair_session_id=repair_session.id,
            repair_phase_session_id=phase_session.id,
            client_turn_id="image-run-turn",
            request_hash="image-run-hash",
            phase="diagnostic",
            message={"artifact_ids": ["art_image"], "text": "Inspect this."},
            start_event_sequence=1,
            image_analysis_mode="enabled",
        ),
    )
    return repair_session, turn


def _run(*, repair_session_id: str, turn_id: str) -> ObservationExtractionRun:
    return ObservationExtractionRun(
        repair_session_id=repair_session_id,
        turn_id=turn_id,
        image_analysis_mode="enabled",
        input_artifact_ids=["art_image"],
        preprocessing_version="image-normalization.v1",
        extractor_version="observation-extractor.v1",
        prompt_version="observation-prompt.v1",
        output_schema_version="visual-observation.v1",
        provider="google_ai",
        model="gemini-test",
    )


async def test_observation_run_creation_lookup_and_zero_observation_completion(
    db_session: AsyncSession,
) -> None:
    repair_session, turn = await _create_image_turn(db_session)
    runs = ObservationExtractionRunRepository(db_session)
    run = await runs.add(_run(repair_session_id=repair_session.id, turn_id=turn.id))

    completed = await runs.mark_completed(
        run,
        validated_output={"image_assessments": [], "observations": []},
    )

    assert await runs.get_by_turn_id(turn.id) == completed
    assert completed.status == "completed"
    assert completed.provider_attempt_count == 0
    assert completed.validated_output == {"image_assessments": [], "observations": []}


async def test_observation_run_can_fail_before_a_provider_attempt(
    db_session: AsyncSession,
) -> None:
    repair_session, turn = await _create_image_turn(db_session)
    runs = ObservationExtractionRunRepository(db_session)
    run = await runs.add(_run(repair_session_id=repair_session.id, turn_id=turn.id))

    failed = await runs.mark_failed(
        run,
        failure_metadata={"code": "image_decode_failed", "retryable": False},
    )

    assert failed.status == "failed"
    assert failed.provider_attempt_count == 0
    assert failed.validated_output is None


async def test_observation_run_appends_ordered_attempts_only_for_eligible_recovery(
    db_session: AsyncSession,
) -> None:
    repair_session, turn = await _create_image_turn(db_session)
    runs = ObservationExtractionRunRepository(db_session)
    run = await runs.add(_run(repair_session_id=repair_session.id, turn_id=turn.id))

    first = await runs.append_attempt(
        run_id=run.id,
        provider="google_ai",
        model="gemini-test",
    )
    await runs.finish_attempt(
        first,
        outcome="failed",
        failure_metadata={"code": "provider_timeout", "retryable": True},
        latency_ms=30,
    )
    await runs.mark_failed(
        run,
        failure_metadata={"code": "provider_timeout", "retryable": True},
    )
    second = await runs.append_attempt(
        run_id=run.id,
        provider="google_ai",
        model="gemini-test",
    )

    assert (first.attempt_number, second.attempt_number) == (1, 2)
    assert run.provider_attempt_count == 2
    await runs.mark_diagnostic_agent_started(run)
    await runs.mark_failed(
        run,
        failure_metadata={"code": "provider_timeout", "retryable": True},
    )
    with pytest.raises(ValueError, match="not eligible"):
        await runs.append_attempt(
            run_id=run.id,
            provider="google_ai",
            model="gemini-test",
        )


async def test_observation_run_unique_turn_identity_and_usable_session_reads(
    db_session: AsyncSession,
) -> None:
    repair_session, turn = await _create_image_turn(db_session)
    runs = ObservationExtractionRunRepository(db_session)
    run = await runs.add(_run(repair_session_id=repair_session.id, turn_id=turn.id))
    await runs.mark_completed(run, validated_output={"observations": []})

    assert await runs.list_usable_for_session(repair_session.id) == [run]
    db_session.add(_run(repair_session_id=repair_session.id, turn_id=turn.id))
    with pytest.raises(IntegrityError):
        await db_session.flush()

    await db_session.rollback()


async def test_observation_run_get_or_create_is_safe_across_concurrent_sessions(
    db_session: AsyncSession,
) -> None:
    repair_session, turn = await _create_image_turn(db_session)
    await db_session.commit()
    assert db_session.bind is not None
    session_factory = async_sessionmaker(db_session.bind, expire_on_commit=False)

    async def create() -> str:
        async with session_factory() as session:
            run = await ObservationExtractionRunRepository(session).get_or_create(
                _run(repair_session_id=repair_session.id, turn_id=turn.id)
            )
            await session.commit()
            return run.id

    first_id, second_id = await asyncio.gather(create(), create())

    assert first_id == second_id
    assert (
        await ObservationExtractionRunRepository(db_session).get_by_turn_id(turn.id)
        is not None
    )
