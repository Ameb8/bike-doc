"""Diagnostic persistence repository tests."""

from __future__ import annotations

import asyncio
from typing import Final

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

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

CONTENT_SHA256: Final = "a" * 64


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


async def test_phase_session_turn_counts_exclude_other_sessions_and_events(
    db_session: AsyncSession,
) -> None:
    """Count accepted turns by phase session and stable event-sequence ordinal."""
    _, _, repair_session = await _create_user_bike_session(db_session)
    phase_sessions = RepairPhaseSessionRepository(db_session)
    diagnostic_phase_session = await phase_sessions.add(
        RepairPhaseSession(
            repair_session_id=repair_session.id,
            phase="diagnostic",
            adk_session_id="adk-diagnostic-counts",
        ),
    )
    planning_phase_session = await phase_sessions.add(
        RepairPhaseSession(
            repair_session_id=repair_session.id,
            phase="planning",
            adk_session_id="adk-planning-counts",
        ),
    )
    turns = RepairTurnRepository(db_session)
    first_turn = await turns.add(
        RepairTurn(
            repair_session_id=repair_session.id,
            repair_phase_session_id=diagnostic_phase_session.id,
            client_turn_id="diagnostic-first",
            request_hash="hash-diagnostic-first",
            phase="diagnostic",
            message={"artifact_ids": [], "text": "First diagnostic turn."},
            start_event_sequence=1,
        ),
    )
    await RepairSessionEventRepository(db_session).append_for_session(
        repair_session_id=repair_session.id,
        turn_id=first_turn.id,
        event_type="turn.started",
        data={"turn_id": first_turn.id, "phase": "diagnostic"},
    )
    await RepairSessionEventRepository(db_session).append_for_session(
        repair_session_id=repair_session.id,
        event_type="assistant.delta",
        data={"message_id": "msg_count", "delta": "Need more detail."},
    )
    assert await turns.count_for_phase_session(diagnostic_phase_session.id) == 1
    await turns.add(
        RepairTurn(
            repair_session_id=repair_session.id,
            repair_phase_session_id=planning_phase_session.id,
            client_turn_id="planning-turn",
            request_hash="hash-planning-turn",
            phase="planning",
            message={"artifact_ids": [], "text": "Plan it."},
            start_event_sequence=4,
        ),
    )
    later_diagnostic_turn = await turns.add(
        RepairTurn(
            repair_session_id=repair_session.id,
            repair_phase_session_id=diagnostic_phase_session.id,
            client_turn_id="diagnostic-later",
            request_hash="hash-diagnostic-later",
            phase="diagnostic",
            message={"artifact_ids": [], "text": "Second diagnostic turn."},
            start_event_sequence=5,
        ),
    )
    await db_session.flush()

    assert await turns.count_for_phase_session(diagnostic_phase_session.id) == 2
    first_turn_index = await turns.count_for_phase_session_through_start_event_sequence(
        repair_phase_session_id=diagnostic_phase_session.id,
        start_event_sequence=first_turn.start_event_sequence,
    )
    retried_first_turn_index = (
        await turns.count_for_phase_session_through_start_event_sequence(
            repair_phase_session_id=diagnostic_phase_session.id,
            start_event_sequence=first_turn.start_event_sequence,
        )
    )
    assert first_turn_index == 1
    assert retried_first_turn_index == 1
    assert (
        await turns.count_for_phase_session_through_start_event_sequence(
            repair_phase_session_id=diagnostic_phase_session.id,
            start_event_sequence=later_diagnostic_turn.start_event_sequence,
        )
        == 2
    )


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
    # Failure must precede the agent-start fence. The database forbids later
    # extraction changes; the marker blocks an otherwise eligible recovery.
    await runs.mark_failed(
        run,
        failure_metadata={"code": "provider_timeout", "retryable": True},
    )
    await runs.mark_diagnostic_agent_started(run)
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


async def test_artifact_invalidation_hides_citing_runs_and_reports(
    db_session: AsyncSession,
) -> None:
    repair_session, turn = await _create_image_turn(db_session)
    runs = ObservationExtractionRunRepository(db_session)
    run = await runs.add(_run(repair_session_id=repair_session.id, turn_id=turn.id))
    await runs.mark_completed(run, validated_output={"observations": []})
    report = await PhaseReportRepository(db_session).add(
        PhaseReport(
            repair_session_id=repair_session.id,
            type="diagnostic",
            schema_version="diagnostic_report.v1",
            phase="diagnostic",
            summary="Derived image evidence.",
            safety_flags=[],
            source_artifact_ids=["art_image"],
            payload={},
        )
    )

    assert (
        await runs.redact_citing_artifact(
            artifact_id="art_image", reason="retention_expired"
        )
        == 1
    )
    assert (
        await PhaseReportRepository(db_session).invalidate_citing_artifact(
            artifact_id="art_image", reason="retention_expired"
        )
        == 1
    )

    assert run.validated_output is None
    assert run.preprocessing_manifest == []
    assert await runs.list_usable_for_session(repair_session.id) == []
    assert await PhaseReportRepository(db_session).get(report.id) is None
    assert (
        await PhaseReportRepository(db_session).list_for_session(repair_session.id)
        == []
    )


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
