"""Seeded diagnostic role on real PostgreSQL/JetStream, durable ADK and fake model."""

import asyncio
import json
import os
import uuid
from collections.abc import AsyncGenerator, AsyncIterator

import pytest
from google.adk.models.base_llm import BaseLlm
from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse
from google.genai import types
from pydantic import PrivateAttr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from bike_doc_api.adk.execution import DiagnosticResources
from bike_doc_api.adk.resources import InactiveObservationExtractor
from bike_doc_api.adk.sessions import DIAGNOSTIC_ADK_APP_NAME, DIAGNOSTIC_ADK_USER_ID
from bike_doc_api.adk.storage import open_adk_session_service
from bike_doc_api.api.v1.turns import get_turn_service
from bike_doc_api.core.config import Settings
from bike_doc_api.models.artifact import ArtifactRef
from bike_doc_api.models.background_job import BackgroundJob
from bike_doc_api.models.bike import BikeProfile
from bike_doc_api.models.event import RepairSessionEvent
from bike_doc_api.models.repair_session import RepairPhaseSession, RepairSession
from bike_doc_api.models.user import User
from bike_doc_api.providers.storage import LocalStorageProvider
from bike_doc_api.repositories.background_jobs import BackgroundJobRepository, JobRecord
from bike_doc_api.schemas.turn import TurnCreate
from bike_doc_api.workers.adapters import NatsDelivery
from bike_doc_api.workers.diagnostic_role import diagnostic_worker

DB_URL = os.getenv("BIKE_DOC_API_MAINTENANCE_TEST_DATABASE_URL")
NATS_URL = os.getenv("BIKE_DOC_API_MAINTENANCE_TEST_NATS_URL")
pytestmark = [
    pytest.mark.nats,
    pytest.mark.skipif(
        not DB_URL or not NATS_URL,
        reason="requires disposable migrated PostgreSQL/JetStream",
    ),
]


class FakeModel(BaseLlm):
    _requests: list[str] = PrivateAttr(default_factory=list)
    _fail: bool = PrivateAttr(default=False)
    _started: asyncio.Event | None = PrivateAttr(default=None)
    _release: asyncio.Event | None = PrivateAttr(default=None)

    async def generate_content_async(
        self, llm_request: LlmRequest, stream: bool = False
    ) -> AsyncGenerator[LlmResponse, None]:
        self._requests.append(llm_request.model_dump_json())
        if self._started is not None and self._release is not None:
            self._started.set()
            await self._release.wait()
        if self._fail:
            raise RuntimeError("private provider response; must never reach job state")
        yield LlmResponse(
            content=types.Content(
                role="model",
                parts=[types.Part(text="Please describe when the noise happens.")],
            )
        )


@pytest.fixture
async def context(tmp_path) -> AsyncIterator[tuple]:
    suffix = uuid.uuid4().hex[:12]
    settings = Settings(
        environment="test",
        database_url=DB_URL,
        nats_url=NATS_URL,
        nats_work_stream=f"DIAGNOSTIC_{suffix}",
        nats_profile_subject=f"diagnostic.{suffix}.profile",
        nats_diagnostic_subject=f"diagnostic.{suffix}.diagnostic",
        nats_profile_consumer=f"profile_{suffix}",
        nats_diagnostic_consumer=f"diagnostic_{suffix}",
        profile_inference_execution="legacy",
        image_analysis_mode="off",
        artifact_local_storage_root=tmp_path,
    )
    engine = create_async_engine(DB_URL)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    adk = await open_adk_session_service(settings, pool_size=4)
    async with sessions() as session, session.begin():
        user = User(
            auth_subject=suffix, email="worker@example.test", display_name="Test"
        )
        session.add(user)
        await session.flush()
        bike = BikeProfile(user_id=user.id, display_name="Before acceptance")
        session.add(bike)
        await session.flush()
        repair = RepairSession(user_id=user.id, bike_id=bike.id)
        session.add(repair)
        await session.flush()
        user_id, bike_id, repair_id = user.id, bike.id, repair.id
        artifact = ArtifactRef(
            user_id=user_id,
            repair_session_id=repair_id,
            purpose="diagnostic_photo",
            media_type="image",
            mime_type="image/jpeg",
            filename="private.jpg",
            byte_size=18,
            status="ready",
            content_sha256="a" * 64,
            storage_provider="local",
            storage_path="private/artifact/path.jpg",
        )
        session.add(artifact)
        await session.flush()
        artifact_id = artifact.id
    async with sessions() as session:
        accepted = await get_turn_service(session, settings).accept_turn(
            current_user=await session.get(User, user_id),
            repair_session_id=repair_id,
            request=TurnCreate.model_validate(
                {
                    "schema_version": "ai_turn.v1",
                    "client_turn_id": suffix,
                    "message": {
                        "text": "private accepted chain skips",
                        "artifact_ids": [artifact_id],
                    },
                }
            ),
        )
        # The producer remains absent. Seed one strict job only for this role test.
        assert not list(
            await session.scalars(
                select(BackgroundJob).where(
                    BackgroundJob.input["turn_id"].astext == accepted.turn_id
                )
            )
        )
        job = await BackgroundJobRepository(session).record(
            JobRecord(
                "diagnostic_turn",
                "diagnostic",
                1,
                {"turn_id": accepted.turn_id},
                f"diagnostic:{suffix}",
                3,
            )
        )
        await session.commit()
    async with sessions() as session, session.begin():
        bike = await session.get(BikeProfile, bike_id)
        bike.display_name = "Current authoritative bike"
    model = FakeModel(model="fake-diagnostic")
    resources = DiagnosticResources(
        adk, LocalStorageProvider(tmp_path), InactiveObservationExtractor(), model=model
    )
    try:
        yield settings, sessions, job, repair_id, model, resources, artifact_id
    finally:
        await adk.close()
        await engine.dispose()


@pytest.mark.parametrize("fail_provider", [False, True])
async def test_seeded_role_commits_product_before_ack_and_duplicate_is_noop(
    context: tuple, fail_provider: bool, caplog: pytest.LogCaptureFixture
) -> None:
    settings, sessions, job, repair_id, model, resources, artifact_id = context
    model._fail = fail_provider
    async with diagnostic_worker(settings, resources=resources) as runtime:
        client = runtime.transport.client

        async def notify():
            await client.jetstream().publish(
                runtime.options.subject,
                json.dumps(
                    {"version": 1, "job_id": job.id, "publication_generation": 1}
                ).encode(),
            )
            return NatsDelivery(
                (await runtime.transport.subscription.fetch(1, timeout=3))[0]
            )

        await runtime.process(await notify())
        async with sessions() as session:
            row = await session.get(BackgroundJob, job.id)
            assert row.state == "succeeded" and row.attempt_count == 1
            assert row.latest_error_category is None
            phase = (
                await session.scalars(
                    select(RepairPhaseSession).where(
                        RepairPhaseSession.repair_session_id == repair_id
                    )
                )
            ).one()
            assert phase.adk_session_id == f"adk_diagnostic_{phase.id}"
            repair = await session.get(RepairSession, repair_id)
            assert repair.status == "awaiting_user"
            events = list(
                await session.scalars(
                    select(RepairSessionEvent)
                    .where(RepairSessionEvent.turn_id == job.input["turn_id"])
                    .order_by(RepairSessionEvent.sequence)
                )
            )
            assert (
                events[0].type == "turn.started" and events[-1].type == "turn.completed"
            )
            if fail_provider:
                assert any(event.type == "error" for event in events)
                assert "private provider response" not in repr(
                    [event.data for event in events]
                )
        await runtime.process(await notify())
        assert len(model._requests) == 1
        assert "private accepted chain skips" in model._requests[0]
        stored = await resources.session_service.get_session(
            app_name=DIAGNOSTIC_ADK_APP_NAME,
            user_id=DIAGNOSTIC_ADK_USER_ID,
            session_id=phase.adk_session_id,
        )
        assert (
            stored.state["app_context"]["bike_profile"]["display_name"]
            == "Current authoritative bike"
        )
        assert stored.state["app_context"]["repair_history"] == []
        assert stored.state["app_context"]["artifact_ids"] == [artifact_id]
        info = await client.jetstream().consumer_info(
            settings.nats_work_stream, settings.nats_diagnostic_consumer
        )
        assert info.num_ack_pending == 0
        # The independent profile consumer receives no diagnostic delivery.
        profile_info = await client.jetstream().consumer_info(
            settings.nats_work_stream, settings.nats_profile_consumer
        )
        assert profile_info.num_pending == 0
    with pytest.raises(RuntimeError, match="worker is closed"):
        runtime.start()
    assert client.is_closed
    assert "private provider response" not in caplog.text
    assert "private accepted chain skips" not in caplog.text
    assert "private/artifact/path.jpg" not in caplog.text
    assert stored.state["app_context"]["user_id"] not in caplog.text


async def test_confirmed_missing_binding_is_pre_effect_failure(context: tuple) -> None:
    settings, sessions, job, repair_id, model, resources, _artifact_id = context
    async with sessions() as session, session.begin():
        phase = (
            await session.scalars(
                select(RepairPhaseSession).where(
                    RepairPhaseSession.repair_session_id == repair_id
                )
            )
        ).one()
        phase.adk_session_id = "confirmed_missing_bound_session"
    async with diagnostic_worker(settings, resources=resources) as runtime:
        await runtime.transport.client.jetstream().publish(
            runtime.options.subject,
            json.dumps(
                {"version": 1, "job_id": job.id, "publication_generation": 1}
            ).encode(),
        )
        delivery = NatsDelivery(
            (await runtime.transport.subscription.fetch(1, timeout=3))[0]
        )
        await runtime.process(delivery)
        async with sessions() as session:
            row = await session.get(BackgroundJob, job.id)
            assert (
                row.state == "dead" and row.latest_error_category == "permanent_failure"
            )
            phase = (
                await session.scalars(
                    select(RepairPhaseSession).where(
                        RepairPhaseSession.repair_session_id == repair_id
                    )
                )
            ).one()
            assert phase.adk_session_id == "confirmed_missing_bound_session"
        assert model._requests == []


# Real database fencing tests use independent sessions. No transport fake can
# prove job-lock ordering, wall-clock expiry, or rollback of already-flushed SQL.
async def _fencing_execution(context: tuple, duration: float = 120):
    from datetime import timedelta

    from bike_doc_api.adk.sessions import DiagnosticADKSessionClient
    from bike_doc_api.core.diagnostic_execution import DiagnosticExecution
    from bike_doc_api.repositories.background_jobs import ExecutionPolicy
    from bike_doc_api.repositories.repair_sessions import RepairPhaseSessionRepository

    _, sessions, job, repair_id, _, resources, _ = context
    async with sessions() as session:
        phase = (
            await session.scalars(
                select(RepairPhaseSession).where(
                    RepairPhaseSession.repair_session_id == repair_id
                )
            )
        ).one()
        await DiagnosticADKSessionClient(resources.session_service).ensure_or_resume(
            phase_session=phase, phase_sessions=RepairPhaseSessionRepository(session)
        )
        await session.commit()
        phase_id = phase.id
        resolved = await BackgroundJobRepository(session).resolve_delivery(
            job_id=job.id,
            generation=1,
            workload_class="diagnostic",
            validate_definition=lambda _: ExecutionPolicy(timedelta(seconds=duration)),
        )
        await session.commit()
        current = resolved.job
        assert current and current.execution_token and current.execution_deadline
        return DiagnosticExecution(
            current.id, current.execution_token, current.execution_deadline
        ), phase_id


async def _product_snapshot(sessions, repair_id):
    from bike_doc_api.models.observation_extraction import (
        ObservationExtractionAttempt,
        ObservationExtractionRun,
    )
    from bike_doc_api.models.phase_report import PhaseReport

    async with sessions() as session:
        repair = await session.get(RepairSession, repair_id)
        result = [
            tuple(getattr(repair, col.name) for col in RepairSession.__table__.columns)
        ]
        for model in (
            RepairSessionEvent,
            PhaseReport,
            ObservationExtractionRun,
            ObservationExtractionAttempt,
        ):
            rows = (await session.scalars(select(model).order_by(model.id))).all()
            result.append(
                [
                    tuple(getattr(row, col.name) for col in model.__table__.columns)
                    for row in rows
                ]
            )
        return result


@pytest.mark.parametrize(
    "family",
    [
        "event",
        "input",
        "safety",
        "report",
        "completion",
        "session_dml",
        "visual_start",
        "visual_manifest",
        "visual_attempt",
        "visual_complete",
        "visual_failed",
        "visual_finish_attempt",
        "visual_agent_start",
        "visual_redact",
    ],
)
@pytest.mark.parametrize("loss", ["replacement", "expiry"])
async def test_every_protected_write_family_rejects_late_execution(
    context, family, loss
):
    import asyncio

    from sqlalchemy import update

    from bike_doc_api.adk.execution import build_diagnostic_orchestrator
    from bike_doc_api.core.diagnostic_execution import StaleDiagnosticExecutionError
    from bike_doc_api.models.observation_extraction import ObservationExtractionRun
    from bike_doc_api.repositories.diagnostic_execution import DiagnosticExecutionFence
    from bike_doc_api.repositories.observation_extraction import (
        ObservationExtractionRunRepository,
    )
    from bike_doc_api.schemas.common import RepairSessionStatus
    from bike_doc_api.schemas.repair_session import InputRequestType

    settings, sessions, job, repair_id, _, resources, _ = context
    execution, phase_id = await _fencing_execution(
        context, duration=0.6 if loss == "expiry" else 120
    )
    async with sessions() as session:
        fence = DiagnosticExecutionFence(session, execution)
        await fence.cross_boundary()
        run = ObservationExtractionRun(
            turn_id=job.input["turn_id"],
            repair_session_id=repair_id,
            image_analysis_mode="enabled",
            input_artifact_ids=[],
            preprocessing_version="test",
            extractor_version="test",
            prompt_version="test",
            output_schema_version="visual-observation.v1",
            provider="fake",
            model="fake",
        )
        runs = ObservationExtractionRunRepository(session)
        if family == "visual_start":
            run_id, attempt_id = None, None
        else:
            await runs.add(run)
            attempt = await runs.append_attempt(
                run_id=run.id, provider="fake", model="fake"
            )
            if family == "visual_attempt":
                await runs.mark_failed(run, failure_metadata={"retryable": True})
            run_id, attempt_id = run.id, attempt.id
        await session.commit()
    # Let the original hard deadline expire without replacing the token or
    # changing validity. The database also prohibits changing this deadline.
    if loss == "expiry":
        from sqlalchemy import func

        async with sessions() as control:
            now = (await control.execute(select(func.clock_timestamp()))).scalar_one()
        await asyncio.sleep(max(0, (execution.deadline - now).total_seconds()) + 0.05)
    else:
        async with sessions() as control:
            await control.execute(
                update(BackgroundJob)
                .where(BackgroundJob.id == job.id)
                .values(execution_token="replacement-token")
            )
            await control.commit()
    before = await _product_snapshot(sessions, repair_id)
    async with sessions() as session:
        # Load objects before installing the fence to exercise dirty/identity-map
        # writes as well as query-based service writes after authority is lost.
        user_id = (await session.get(RepairSession, repair_id)).user_id
        user = await session.get(User, user_id)
        run = await session.get(ObservationExtractionRun, run_id) if run_id else None
        from bike_doc_api.models.observation_extraction import (
            ObservationExtractionAttempt,
        )

        attempt = (
            await session.get(ObservationExtractionAttempt, attempt_id)
            if attempt_id
            else None
        )
        fence = DiagnosticExecutionFence(session, execution)
        graph = build_diagnostic_orchestrator(
            session=session, settings=settings, resources=resources, fence=fence
        )
        runs = ObservationExtractionRunRepository(session)
        with pytest.raises(
            StaleDiagnosticExecutionError, match=r"^diagnostic_execution_stale$"
        ):
            if family == "event":
                await graph.event_service.append_event(
                    repair_session_id=repair_id,
                    turn_id=job.input["turn_id"],
                    event_type="assistant.delta",
                    data={"text": "late"},
                )
            elif family == "input":
                await graph.request_diagnostic_input._service.request_diagnostic_input(
                    current_user=user,
                    repair_session_id=repair_id,
                    diagnostic_session_id=phase_id,
                    request_type=InputRequestType.TEXT,
                    prompt="Describe noise",
                    required=True,
                    accepted_media_types=[],
                    choices=[],
                    min_artifacts=None,
                    max_artifacts=None,
                )
            elif family == "safety":
                await graph.raise_safety_flag._service.raise_safety_flag(
                    current_user=user,
                    repair_session_id=repair_id,
                    safety_flag={
                        "code": "unsafe_riding_condition",
                        "severity": "blocking",
                        "phase": "diagnostic",
                        "message": "Stop riding",
                        "blocks_repair_instructions": True,
                    },
                )
            elif family == "report":
                payload = {
                    "schema_version": "diagnostic_report.v2",
                    "diagnostic_outcome": "diagnosis_supported",
                    "reported_symptoms": ["Chain skips"],
                    "primary_diagnosis": {
                        "component": "chain",
                        "issue": "Wear",
                        "confidence": "medium",
                        "diy_suitability": "caution",
                        "supporting_finding_ids": ["symptom"],
                    },
                    "contributing_factors": [],
                    "observed_findings": [
                        {
                            "finding_id": "symptom",
                            "component": "chain",
                            "finding": "Skipping",
                            "evidence_source": "user_report",
                            "evidence_source_detail": None,
                            "relationship_to_symptoms": "supports_primary_diagnosis",
                            "artifact_ids": [],
                        }
                    ],
                    "alternate_hypotheses": [],
                    "unresolved_uncertainties": [],
                    "evidence_summary": "Reported skipping",
                    "key_artifact_ids": [],
                    "user_skill_level": "beginner",
                    "safety_flags": [],
                    "diagnostic_session_id": phase_id,
                }
                await graph.save_diagnostic_report._service.persist_diagnostic_report(
                    current_user=user,
                    repair_session_id=repair_id,
                    summary="Wear",
                    payload=payload,
                    safety_flags=[],
                    source_artifact_ids=[],
                )
            elif family == "completion":
                await graph._append_turn_completed(
                    user_id=user_id,
                    repair_session_id=repair_id,
                    turn_id=job.input["turn_id"],
                    status=RepairSessionStatus.AWAITING_USER,
                )
            elif family == "session_dml":
                await session.execute(
                    update(RepairSession)
                    .where(RepairSession.id == repair_id)
                    .values(status="awaiting_user")
                )
            elif family == "visual_start":
                await runs.get_or_create(
                    ObservationExtractionRun(
                        turn_id=job.input["turn_id"],
                        repair_session_id=repair_id,
                        image_analysis_mode="enabled",
                        input_artifact_ids=[],
                        preprocessing_version="test",
                        extractor_version="test",
                        prompt_version="test",
                        output_schema_version="visual-observation.v1",
                        provider="fake",
                        model="fake",
                    )
                )
            elif family == "visual_manifest":
                await runs.set_preprocessing_manifest(run, manifest=[{"late": True}])
            elif family == "visual_attempt":
                await runs.append_attempt(run_id=run_id, provider="fake", model="fake")
            elif family == "visual_complete":
                await runs.mark_completed(run, validated_output={"late": True})
            elif family == "visual_failed":
                await runs.mark_failed(run, failure_metadata={"late": True})
            elif family == "visual_finish_attempt":
                await runs.finish_attempt(attempt, outcome="completed")
            elif family == "visual_agent_start":
                await runs.mark_diagnostic_agent_started(run)
            elif family == "visual_redact":
                await runs.redact(run, reason="late")
            await session.commit()
        await session.rollback()
    assert await _product_snapshot(sessions, repair_id) == before
    assert not context[4]._requests


async def test_boundary_race_idempotence_and_replaced_owner_cannot_inherit(context):
    import asyncio
    from dataclasses import replace

    from sqlalchemy import update

    from bike_doc_api.core.diagnostic_execution import StaleDiagnosticExecutionError
    from bike_doc_api.repositories.diagnostic_execution import DiagnosticExecutionFence

    _, sessions, job, _, _, _, _ = context
    execution, _ = await _fencing_execution(context)

    async def cross(identity):
        async with sessions() as session:
            try:
                await DiagnosticExecutionFence(session, identity).cross_boundary()
                return True
            except StaleDiagnosticExecutionError:
                return False

    assert sorted(
        await asyncio.gather(cross(execution), cross(replace(execution, token="other")))
    ) == [False, True]
    async with sessions() as session:
        row = await session.get(BackgroundJob, job.id)
        boundary = row.effect_boundary_at
        assert row.effect_boundary_token == execution.token
        assert row.execution_deadline == execution.deadline
    assert await cross(execution)
    async with sessions() as session:
        row = await session.get(BackgroundJob, job.id)
        assert row.effect_boundary_at == boundary
        await session.execute(
            update(BackgroundJob)
            .where(BackgroundJob.id == job.id)
            .values(execution_token="replacement")
        )
        await session.commit()
    assert not await cross(execution)
    assert not await cross(replace(execution, token="replacement"))


async def test_flushed_effect_rolls_back_if_deadline_passes_before_commit(context):
    import asyncio

    from bike_doc_api.core.diagnostic_execution import StaleDiagnosticExecutionError
    from bike_doc_api.repositories.diagnostic_execution import DiagnosticExecutionFence
    from bike_doc_api.repositories.events import RepairSessionEventRepository

    _, sessions, _job, repair_id, _, _, _ = context
    execution, _ = await _fencing_execution(context, duration=0.6)
    before = await _product_snapshot(sessions, repair_id)
    async with sessions() as session:
        fence = DiagnosticExecutionFence(session, execution)
        await fence.cross_boundary()
        fence.bind()
        await RepairSessionEventRepository(session).append_for_session(
            repair_session_id=repair_id,
            event_type="assistant.delta",
            data={"text": "flushed"},
        )
        await asyncio.sleep(0.7)
        with pytest.raises(StaleDiagnosticExecutionError):
            await session.commit()
        await session.rollback()
    assert await _product_snapshot(sessions, repair_id) == before


async def test_lock_wait_checks_wall_clock_after_deadline(context):
    import asyncio

    from bike_doc_api.core.diagnostic_execution import StaleDiagnosticExecutionError
    from bike_doc_api.repositories.diagnostic_execution import DiagnosticExecutionFence

    _, sessions, job, _, _, _, _ = context
    execution, _ = await _fencing_execution(context, duration=0.5)
    async with sessions() as blocker:
        await blocker.execute(
            select(BackgroundJob).where(BackgroundJob.id == job.id).with_for_update()
        )

        async def late_boundary():
            async with sessions() as session:
                with pytest.raises(StaleDiagnosticExecutionError):
                    await DiagnosticExecutionFence(session, execution).cross_boundary()

        task = asyncio.create_task(late_boundary())
        await asyncio.sleep(0.6)
        assert not task.done()
        await blocker.commit()
        await task
    async with sessions() as session:
        row = await session.get(BackgroundJob, job.id)
        assert row.effect_boundary_at is None
        assert row.execution_deadline == execution.deadline


async def test_in_flight_provider_loses_authority_despite_progress(
    context,
):
    from sqlalchemy import update

    settings, sessions, job, repair_id, model, resources, _ = context
    model._started, model._release = asyncio.Event(), asyncio.Event()
    async with diagnostic_worker(settings, resources=resources) as runtime:
        await runtime.transport.client.jetstream().publish(
            runtime.options.subject,
            json.dumps(
                {"version": 1, "job_id": job.id, "publication_generation": 1}
            ).encode(),
        )
        delivery = NatsDelivery(
            (await runtime.transport.subscription.fetch(1, timeout=3))[0]
        )
        running = asyncio.create_task(runtime.process(delivery))
        try:
            await asyncio.wait_for(model._started.wait(), timeout=10)
            before = await _product_snapshot(sessions, repair_id)
            async with sessions() as control:
                row = await control.get(BackgroundJob, job.id)
                assert row.effect_boundary_at is not None
                deadline = row.execution_deadline
                await control.execute(
                    update(BackgroundJob)
                    .where(BackgroundJob.id == job.id)
                    .values(execution_token="new-owner")
                )
                await control.commit()
            for _ in range(3):
                await delivery.progress()
            model._release.set()
            await asyncio.wait_for(running, timeout=10)
            assert await _product_snapshot(sessions, repair_id) == before
            async with sessions() as control:
                row = await control.get(BackgroundJob, job.id)
                assert row.state == "running"
                assert row.execution_token == "new-owner"
                assert row.execution_deadline == deadline
            assert (
                await runtime.transport.subscription.consumer_info()
            ).num_ack_pending == 1
        finally:
            model._release.set()
            if not running.done():
                running.cancel()
                await asyncio.gather(running, return_exceptions=True)


async def test_boundary_and_token_replacement_race_has_one_owner(context):
    from dataclasses import replace

    from sqlalchemy import update

    from bike_doc_api.core.diagnostic_execution import StaleDiagnosticExecutionError
    from bike_doc_api.repositories.diagnostic_execution import DiagnosticExecutionFence

    _, sessions, job, _, _, _, _ = context
    execution, _ = await _fencing_execution(context)
    replacement = replace(execution, token="b" * 32)

    async def cross(identity):
        async with sessions() as session:
            try:
                await DiagnosticExecutionFence(session, identity).cross_boundary()
                return True
            except StaleDiagnosticExecutionError:
                return False

    async def replace_and_cross():
        async with sessions() as session:
            await session.execute(
                update(BackgroundJob)
                .where(BackgroundJob.id == job.id)
                .values(execution_token=replacement.token)
            )
            await session.commit()
        return await cross(replacement)

    old_won, new_won = await asyncio.gather(cross(execution), replace_and_cross())
    assert old_won != new_won
    async with sessions() as session:
        row = await session.get(BackgroundJob, job.id)
        assert row.execution_deadline == execution.deadline
        assert row.effect_boundary_token == (
            execution.token if old_won else replacement.token
        )
        # Even the current execution cannot rewrite an earlier reservation owner.
        from sqlalchemy.exc import IntegrityError

        with pytest.raises(IntegrityError):
            await session.execute(
                update(BackgroundJob)
                .where(BackgroundJob.id == job.id)
                .values(effect_boundary_token="c" * 32)
            )
        await session.rollback()
