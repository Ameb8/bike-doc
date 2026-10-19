"""Accepted diagnostic jobs on real PostgreSQL/JetStream, durable ADK and fake model."""

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
from bike_doc_api.core.nats import ensure_work_topology, jetstream, nats_connection
from bike_doc_api.models.artifact import ArtifactRef
from bike_doc_api.models.background_job import BackgroundJob
from bike_doc_api.models.bike import BikeProfile
from bike_doc_api.models.event import RepairSessionEvent
from bike_doc_api.models.repair_session import RepairPhaseSession, RepairSession
from bike_doc_api.models.user import User
from bike_doc_api.providers.storage import LocalStorageProvider
from bike_doc_api.repositories.background_jobs import (
    BackgroundJobRepository,
    JobRecord,
    JobSnapshot,
)
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
        diagnostic_execution="durable_queue",
        profile_inference_execution="legacy",
        profile_inference_policy_mode="bootstrap-v1",
        image_analysis_mode="off",
        artifact_local_storage_root=tmp_path,
    )
    async with nats_connection(settings) as provisioner:
        await ensure_work_topology(jetstream(provisioner), settings)
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
        rows = list(
            await session.scalars(
                select(BackgroundJob).where(
                    BackgroundJob.input["turn_id"].astext == accepted.turn_id
                )
            )
        )
        assert {row.job_kind for row in rows} == {
            "diagnostic_turn",
            "profile_inference",
        }
        assert all(
            row.desired_generation == 1 and row.confirmed_generation == 0
            for row in rows
        )
        job = JobSnapshot.from_row(
            next(row for row in rows if row.job_kind == "diagnostic_turn")
        )
        assert job.input == {"turn_id": accepted.turn_id}
        assert (
            next(
                row for row in rows if row.job_kind == "diagnostic_turn"
            ).deduplication_key
            == accepted.turn_id
        )
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
async def test_accepted_role_commits_product_before_ack_and_duplicate_is_noop(
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


async def _terminal_events(context):
    _, sessions, job, repair_id, *_ = context
    async with sessions() as session:
        repair = await session.get(RepairSession, repair_id)
        row = await session.get(BackgroundJob, job.id)
        events = list(
            await session.scalars(
                select(RepairSessionEvent)
                .where(RepairSessionEvent.turn_id == job.input["turn_id"])
                .order_by(RepairSessionEvent.sequence)
            )
        )
        return row, repair, events


async def _assert_error_pair(context, state, attempts):
    row, repair, events = await _terminal_events(context)
    assert row.state == state and row.attempt_count == attempts
    assert repair.status == "awaiting_user"
    assert [e.type for e in events] == ["turn.started", "error", "turn.completed"]
    assert events[-1].sequence == events[-2].sequence + 1
    assert events[-2].data["code"] == "diagnostic_processing_error"
    assert events[-2].data["retryable"] is True
    snapshot = events[-1].data["session"]
    assert snapshot["status"] == "awaiting_user"
    assert snapshot["latest_event_id"] == str(repair.latest_event_sequence)
    assert events[-1].data["turn_id"] == context[2].input["turn_id"]


@pytest.mark.parametrize("error", ["version", "extra", "malformed", "missing"])
async def test_trusted_definition_failure_repairs_only_valid_product_identity(
    context, error
):
    from bike_doc_api.repositories.background_jobs import JobError, ResolutionKind
    from bike_doc_api.repositories.diagnostic_jobs import DiagnosticJobRepository

    _, sessions, job, repair_id, *_ = context
    # The stored definition is immutable: seed a separate poison row rather than
    # corrupting it through forbidden updates.
    async with sessions() as session, session.begin():
        instruction = dict(job.input)
        if error == "extra":
            instruction["unexpected"] = "private content"
        elif error == "malformed":
            instruction = {"turn_id": "private malformed"}
        elif error == "missing":
            instruction = {}
        poison = await DiagnosticJobRepository(session).record(
            JobRecord(
                "diagnostic_turn",
                "diagnostic",
                2 if error == "version" else 1,
                instruction,
                uuid.uuid4().hex,
                3,
            )
        )
        result = await DiagnosticJobRepository(session).resolve_delivery(
            job_id=poison.id,
            generation=1,
            workload_class="diagnostic",
            validate_definition=lambda _: JobError.INPUT_INVALID,
        )
        assert result.kind == ResolutionKind.DEFINITION_FAILURE
    row, repair, events = await _terminal_events(context)
    assert row.state == "queued"
    if error in {"version", "extra"}:
        await _assert_error_pair(
            (context[0], sessions, poison, repair_id, *context[4:]), "dead", 0
        )
    else:
        assert repair.status == "running"
        assert [e.type for e in events] == ["turn.started"]


@pytest.mark.parametrize("outcome", ["permanent", "exhausted", "retry"])
async def test_preboundary_outcomes_commit_before_settlement(context, outcome):
    from datetime import timedelta

    from bike_doc_api.repositories.background_jobs import (
        ExecutionPolicy,
        JobError,
        JobOutcome,
    )
    from bike_doc_api.repositories.diagnostic_jobs import DiagnosticJobRepository

    _, sessions, job, *_ = context
    current = None
    for attempt in range(3 if outcome == "exhausted" else 1):
        async with sessions() as session, session.begin():
            repo = DiagnosticJobRepository(session)
            current = (
                await repo.resolve_delivery(
                    job_id=job.id,
                    generation=1,
                    workload_class="diagnostic",
                    validate_definition=lambda _: ExecutionPolicy(
                        timedelta(seconds=30)
                    ),
                )
            ).job
        assert current.execution_token
        async with sessions() as session, session.begin():
            result = await DiagnosticJobRepository(session).apply_outcome_and_load(
                job_id=job.id,
                execution_token=current.execution_token,
                outcome=JobOutcome("dead", JobError.PERMANENT_FAILURE)
                if outcome == "permanent"
                else JobOutcome(
                    "retrying",
                    JobError.PROVIDER_UNAVAILABLE,
                    timedelta(milliseconds=10),
                ),
            )
        if outcome == "exhausted" and attempt < 2:
            await asyncio.sleep(0.02)
    if outcome == "retry":
        assert result.state == "retrying" and result.eligible_at > current.eligible_at
        _, repair, events = await _terminal_events(context)
        assert repair.status == "running"
        assert [e.type for e in events] == ["turn.started"]
    else:
        await _assert_error_pair(context, "dead", 3 if outcome == "exhausted" else 1)
    # Completion/NAK is lost; an eventual delivery must not execute early or revive.
    async with sessions() as session, session.begin():
        resolved = (
            await DiagnosticJobRepository(session).resolve_delivery(
                job_id=job.id,
                generation=1,
                workload_class="diagnostic",
                validate_definition=lambda _: pytest.fail("terminal must not validate"),
            )
            if outcome != "retry"
            else None
        )
        if resolved:
            assert (
                resolved.kind.value == "terminal"
                and resolved.job.attempt_count == result.attempt_count
            )


@pytest.mark.parametrize("boundary", [False, True])
async def test_expired_execution_race_repeated_maintenance_and_no_progress(
    context, boundary
):
    from contextlib import asynccontextmanager
    from datetime import UTC, datetime
    from unittest.mock import AsyncMock

    from bike_doc_api.maintenance.runtime import JobMaintenance
    from bike_doc_api.repositories.diagnostic_execution import DiagnosticExecutionFence
    from bike_doc_api.repositories.diagnostic_jobs import DiagnosticJobRepository
    from bike_doc_api.workers.diagnostic import DiagnosticRecoveryPolicy

    settings, sessions, job, *_ = context
    execution, _ = await _fencing_execution(context, duration=0.15)
    if boundary:
        async with sessions() as session:
            await DiagnosticExecutionFence(session, execution).cross_boundary()
    await asyncio.sleep(0.2)

    @asynccontextmanager
    async def transaction():
        async with sessions() as session, session.begin():
            yield DiagnosticJobRepository(session)

    host = JobMaintenance(
        settings,
        transaction,
        AsyncMock(),
        {("diagnostic_turn", 1): DiagnosticRecoveryPolicy(10)},
    )
    await asyncio.gather(host.reconcile_once(), host.reconcile_once())
    await host.reconcile_once()
    row, repair, events = await _terminal_events(context)
    assert row.attempt_count == 1
    assert not context[4]._requests
    if boundary:
        await _assert_error_pair(context, "interrupted", 1)
        assert row.desired_generation == 1
    else:
        assert row.state == "retrying" and row.desired_generation == 2
        assert row.eligible_at > datetime.now(UTC)
        assert repair.status == "running"
        assert [e.type for e in events] == ["turn.started"]
    async with sessions() as session, session.begin():
        assert not await DiagnosticJobRepository(session).advance_publication(
            job_id=job.id,
            observed_generation=row.desired_generation,
            no_progress_before=datetime.now(UTC),
        )


async def test_crash_during_terminalization_rolls_back_then_racing_recovery_emits_once(
    context, monkeypatch
):
    from bike_doc_api.repositories.background_jobs import JobError, JobOutcome
    from bike_doc_api.repositories.diagnostic_jobs import DiagnosticJobRepository
    from bike_doc_api.repositories.events import RepairSessionEventRepository

    _, sessions, job, *_ = context
    execution, _ = await _fencing_execution(context, duration=0.03)
    await asyncio.sleep(0.05)
    original = RepairSessionEventRepository.add

    async def crash(self, event):
        await original(self, event)
        if event.type == "turn.completed":
            raise RuntimeError("simulated loss after flushed completion")
        return event

    async def recover():
        async with sessions() as session, session.begin():
            return await DiagnosticJobRepository(session).recover_expired(
                job_id=job.id,
                execution_token=execution.token,
                outcome=JobOutcome("dead", JobError.ATTEMPTS_EXHAUSTED),
            )

    monkeypatch.setattr(RepairSessionEventRepository, "add", crash)
    with pytest.raises(RuntimeError):
        await recover()
    row, repair, events = await _terminal_events(context)
    assert row.state == "running" and repair.status == "running"
    assert [e.type for e in events] == ["turn.started"]
    monkeypatch.setattr(RepairSessionEventRepository, "add", original)
    assert sorted(await asyncio.gather(recover(), recover())) == [False, True]
    assert not await recover()
    await _assert_error_pair(context, "dead", 1)


@pytest.mark.parametrize("handled_error", [False, True])
async def test_crash_after_product_commit_before_job_settlement_is_success(
    context, handled_error
):
    from datetime import UTC, datetime

    from bike_doc_api.repositories.diagnostic_jobs import DiagnosticJobRepository
    from bike_doc_api.schemas.background_jobs import DiagnosticTurnInputV1
    from bike_doc_api.workers.diagnostic import DiagnosticRecoveryPolicy
    from bike_doc_api.workers.diagnostic_application import (
        DiagnosticExecutionApplication,
    )
    from bike_doc_api.workers.registry import ClaimedJob

    settings, sessions, job, repair_id, model, resources, _ = context
    model._fail = handled_error
    # Allow cold ADK setup on contended integration hosts before deliberately
    # waiting for expiry below. Product completion must commit before the loss.
    execution, _ = await _fencing_execution(context, duration=10)
    await DiagnosticExecutionApplication(sessions, settings, resources).execute(
        ClaimedJob(
            job.id,
            DiagnosticTurnInputV1.model_validate(job.input),
            1,
            execution.token,
            execution.deadline,
        )
    )
    row, _, events = await _terminal_events(context)
    assert row.state == "running"
    assert [e.type for e in events].count("turn.completed") == 1
    assert [e.type for e in events].count("error") == int(handled_error)
    if handled_error:
        assert [e.type for e in events][-2:] == ["error", "turn.completed"]
    # A user can accept another turn before settlement; recovery must preserve it.
    async with sessions() as session, session.begin():
        repair = await session.get(RepairSession, repair_id)
        repair.status = "running"
    before = await _product_snapshot(sessions, repair_id)
    await asyncio.sleep(
        max(0, (execution.deadline - datetime.now(UTC)).total_seconds()) + 0.05
    )
    async with sessions() as session, session.begin():
        repo = DiagnosticJobRepository(session)
        recovered = await repo.recover_expired_and_load(
            job_id=job.id,
            execution_token=execution.token,
            outcome=DiagnosticRecoveryPolicy().expired_outcome(row),
        )
        assert recovered is not None and recovered.state == "succeeded"
    after, _, _ = await _terminal_events(context)
    assert after.state == "succeeded" and after.latest_error_category is None
    assert await _product_snapshot(sessions, repair_id) == before
    assert len(model._requests) == 1
    # Eventual broker delivery acknowledges without calling ADK again.
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
        assert (
            await runtime.transport.subscription.consumer_info()
        ).num_ack_pending == 0
    assert len(model._requests) == 1
    after, _, _ = await _terminal_events(context)
    assert after.attempt_count == 1


@pytest.mark.parametrize("state", ["dead", "interrupted"])
async def test_terminal_delivery_after_commit_and_ack_loss_is_attempt_free(
    context, state
):
    from bike_doc_api.repositories.background_jobs import JobError, JobOutcome
    from bike_doc_api.repositories.diagnostic_jobs import DiagnosticJobRepository

    settings, sessions, job, _, model, resources, _ = context
    execution, _ = await _fencing_execution(context)
    if state == "interrupted":
        from bike_doc_api.repositories.diagnostic_execution import (
            DiagnosticExecutionFence,
        )

        async with sessions() as session:
            await DiagnosticExecutionFence(session, execution).cross_boundary()
    async with sessions() as session, session.begin():
        assert await DiagnosticJobRepository(session).apply_outcome(
            job_id=job.id,
            execution_token=execution.token,
            outcome=JobOutcome(state, JobError.PERMANENT_FAILURE),
        )
    await _assert_error_pair(context, state, 1)
    async with diagnostic_worker(settings, resources=resources) as runtime:
        for _ in range(2):
            await runtime.transport.client.jetstream().publish(
                runtime.options.subject,
                json.dumps(
                    {"version": 1, "job_id": job.id, "publication_generation": 1}
                ).encode(),
            )
            await runtime.process(
                NatsDelivery(
                    (await runtime.transport.subscription.fetch(1, timeout=3))[0]
                )
            )
            assert (
                await runtime.transport.subscription.consumer_info()
            ).num_ack_pending == 0
    assert not model._requests
    await _assert_error_pair(context, state, 1)


async def test_expired_preboundary_attempt_budget_exhausts_atomically(context):
    from datetime import timedelta

    from bike_doc_api.repositories.background_jobs import (
        ExecutionPolicy,
        ResolutionKind,
    )
    from bike_doc_api.repositories.diagnostic_jobs import DiagnosticJobRepository
    from bike_doc_api.workers.diagnostic import DiagnosticRecoveryPolicy

    _, sessions, job, *_ = context
    generation = 1
    policy = DiagnosticRecoveryPolicy(0.01)
    for attempt in range(1, 4):
        async with sessions() as session, session.begin():
            result = await DiagnosticJobRepository(session).resolve_delivery(
                job_id=job.id,
                generation=generation,
                workload_class="diagnostic",
                validate_definition=lambda _: ExecutionPolicy(
                    timedelta(milliseconds=20)
                ),
            )
        assert result.kind == ResolutionKind.EXECUTABLE
        assert result.job.attempt_count == attempt
        await asyncio.sleep(0.03)
        async with sessions() as session, session.begin():
            assert await DiagnosticJobRepository(session).recover_expired(
                job_id=job.id,
                execution_token=result.job.execution_token,
                outcome=policy.expired_outcome(result.job),
            )
        generation += 1
        await asyncio.sleep(0.02)
    await _assert_error_pair(context, "dead", 3)
    row, _, _ = await _terminal_events(context)
    assert row.desired_generation == 3
    assert not context[4]._requests


async def test_classified_retry_is_durable_before_real_delayed_nak(
    context, monkeypatch
):
    from bike_doc_api.repositories.background_jobs import JobError
    from bike_doc_api.workers.diagnostic import DiagnosticPreparationError
    from bike_doc_api.workers.diagnostic_application import (
        DiagnosticExecutionApplication,
    )

    settings, _sessions, job, _, model, resources, _ = context
    settings = settings.model_copy(update={"diagnostic_worker_retry_seconds": 0.05})
    observed = []
    original = NatsDelivery.nak

    async def unavailable(self, claimed, span):
        raise DiagnosticPreparationError(JobError.PROVIDER_UNAVAILABLE, retryable=True)

    async def nak_after_commit(self, delay):
        row, repair, events = await _terminal_events(context)
        assert row.state == "retrying"
        assert row.latest_error_category == "provider_unavailable"
        assert row.execution_token is None
        assert delay > 0
        assert repair.status == "running"
        assert [e.type for e in events] == ["turn.started"]
        observed.append(row.eligible_at)
        await original(self, delay)

    monkeypatch.setattr(DiagnosticExecutionApplication, "_execute", unavailable)
    monkeypatch.setattr(NatsDelivery, "nak", nak_after_commit)
    async with diagnostic_worker(settings, resources=resources) as runtime:
        await runtime.transport.client.jetstream().publish(
            runtime.options.subject,
            json.dumps(
                {"version": 1, "job_id": job.id, "publication_generation": 1}
            ).encode(),
        )
        for _ in range(3):
            delivery = NatsDelivery(
                (await runtime.transport.subscription.fetch(1, timeout=3))[0]
            )
            await runtime.process(delivery)
        assert (
            await runtime.transport.subscription.consumer_info()
        ).num_ack_pending == 0
    assert len(observed) == 2 and observed[1] > observed[0]
    assert not model._requests
    await _assert_error_pair(context, "dead", 3)


async def test_arbitrary_postboundary_failure_is_interrupted_without_retry(context):
    from bike_doc_api.repositories.background_jobs import JobError, JobOutcome
    from bike_doc_api.repositories.diagnostic_execution import DiagnosticExecutionFence
    from bike_doc_api.repositories.diagnostic_jobs import DiagnosticJobRepository

    _, sessions, job, *_ = context
    execution, _ = await _fencing_execution(context)
    async with sessions() as session:
        await DiagnosticExecutionFence(session, execution).cross_boundary()
    async with sessions() as session, session.begin():
        result = await DiagnosticJobRepository(session).apply_outcome_and_load(
            job_id=job.id,
            execution_token=execution.token,
            outcome=JobOutcome("dead", JobError.PERMANENT_FAILURE),
        )
    assert result.state == "interrupted" and result.desired_generation == 1
    await _assert_error_pair(context, "interrupted", 1)


@pytest.mark.parametrize("first", ["profile", "diagnostic", "profile_failure"])
async def test_two_jobs_execute_independently_and_read_only_committed_facts(
    context, first, monkeypatch
):
    from test_profile_worker import Extractor, Storage

    from bike_doc_api.workers.profile_role import profile_worker

    settings, sessions, job, repair_id, model, resources, _ = context

    from bike_doc_api.workers.diagnostic_application import (
        DiagnosticExecutionApplication,
    )

    diagnostic_entered, diagnostic_release = asyncio.Event(), asyncio.Event()
    profile_entered, profile_release = asyncio.Event(), asyncio.Event()
    original_execute = DiagnosticExecutionApplication._execute

    async def gated_execute(self, claimed, span):
        diagnostic_entered.set()
        await diagnostic_release.wait()
        return await original_execute(self, claimed, span)

    monkeypatch.setattr(DiagnosticExecutionApplication, "_execute", gated_execute)

    class Provider(Extractor):
        async def extract(self, request):
            profile_entered.set()
            await profile_release.wait()
            return await super().extract(
                request.model_copy(update={"caption": "accepted caption"})
            )

    provider = Provider()
    provider.fail = first == "profile_failure"
    async with sessions() as session:
        profile_row = (
            await session.scalars(
                select(BackgroundJob).where(
                    BackgroundJob.input["turn_id"].astext == job.input["turn_id"],
                    BackgroundJob.job_kind == "profile_inference",
                )
            )
        ).one()
        profile = JobSnapshot.from_row(profile_row)
    async with (
        diagnostic_worker(settings, resources=resources) as diagnostic,
        profile_worker(settings, extractor=provider, storage=Storage()) as enrichment,
    ):

        async def execute(runtime, instruction):
            await runtime.transport.client.jetstream().publish(
                runtime.options.subject,
                json.dumps(
                    {
                        "version": 1,
                        "job_id": instruction.id,
                        "publication_generation": 1,
                    }
                ).encode(),
            )
            await runtime.process(
                NatsDelivery(
                    (await runtime.transport.subscription.fetch(1, timeout=3))[0]
                )
            )

        diagnostic_task = asyncio.create_task(execute(diagnostic, job))
        profile_task = asyncio.create_task(execute(enrichment, profile))
        try:
            async with asyncio.timeout(5):
                await diagnostic_entered.wait()
                await profile_entered.wait()
            if first != "diagnostic":
                profile_release.set()
                await profile_task
                async with sessions() as session:
                    assert (await session.get(BackgroundJob, job.id)).state == "running"
            diagnostic_release.set()
            await diagnostic_task
        finally:
            diagnostic_release.set()
            profile_release.set()
            await asyncio.gather(diagnostic_task, profile_task)
        stored_phase = None
        async with sessions() as session:
            stored_phase = (
                await session.scalars(
                    select(RepairPhaseSession).where(
                        RepairPhaseSession.repair_session_id == repair_id
                    )
                )
            ).one()
            assert (await session.get(BackgroundJob, job.id)).state == "succeeded"
        stored = await resources.session_service.get_session(
            app_name=DIAGNOSTIC_ADK_APP_NAME,
            user_id=DIAGNOSTIC_ADK_USER_ID,
            session_id=stored_phase.adk_session_id,
        )
        profile_context = stored.state["app_context"]["bike_profile"]
        states = profile_context.get("field_states") or {}
        assert (
            states.get("brakes.rear.mechanism", {}).get("resolution_state")
            == "resolved"
        ) is (first == "profile")
        async with sessions() as session:
            profile_row = await session.get(BackgroundJob, profile.id)
            assert profile_row.state == ("retrying" if provider.fail else "succeeded")
        assert len(model._requests) == 1
        assert (
            await diagnostic.transport.subscription.consumer_info()
        ).num_ack_pending == 0


@pytest.mark.parametrize("crash_point", [None, "pre_boundary", "post_boundary"])
async def test_live_http_sse_with_external_diagnostic_process_and_rollback(
    context, monkeypatch, crash_point
):
    import signal
    import socket
    import sys
    from collections import deque
    from contextlib import asynccontextmanager
    from datetime import UTC, datetime
    from unittest.mock import AsyncMock

    import httpx
    import uvicorn

    from bike_doc_api.api.deps import get_current_user, get_db_session
    from bike_doc_api.api.v1 import turns as route
    from bike_doc_api.db.session import get_sessionmaker
    from bike_doc_api.main import create_app
    from bike_doc_api.maintenance.nats_publisher import NatsJobPublisher
    from bike_doc_api.maintenance.runtime import JobMaintenance
    from bike_doc_api.repositories.diagnostic_jobs import DiagnosticJobRepository
    from bike_doc_api.workers.diagnostic import DiagnosticRecoveryPolicy

    settings, sessions, _initial, repair_id, *_ = context
    # This exercises cold-process execution and restart, not handler timing.
    # Leave room for ADK/schema preparation on slower hosts; the dedicated
    # timeout/fencing tests above retain their short deterministic deadlines.
    settings = settings.model_copy(
        update={
            "diagnostic_worker_handler_seconds": 10,
            "diagnostic_worker_hard_seconds": 12,
            "diagnostic_worker_timeout_grace_seconds": 0.2,
            "diagnostic_agent_timeout_seconds": 0.1,
            "observation_extraction_timeout_seconds": 0.1,
            "price_lookup_timeout_seconds": 0.1,
            "diagnostic_worker_storage_seconds": 0.1,
            "diagnostic_worker_fetch_timeout_seconds": 0.1,
            "diagnostic_worker_progress_seconds": 0.1,
        }
    )
    child_code = """
import asyncio, json, signal, sys
sys.path.insert(0, "tests/integration")
from test_diagnostic_worker import FakeModel
from bike_doc_api.core.config import Settings
from bike_doc_api.adk.execution import DiagnosticResources
from bike_doc_api.adk.resources import InactiveObservationExtractor
from bike_doc_api.adk.storage import open_adk_session_service
from bike_doc_api.providers.storage import LocalStorageProvider
from bike_doc_api.workers.diagnostic_role import diagnostic_worker
from bike_doc_api.workers.diagnostic_application import DiagnosticExecutionApplication

async def main():
    settings = Settings.model_validate_json(sys.stdin.readline())
    crash_point = sys.stdin.readline().strip()
    original_execute = DiagnosticExecutionApplication._execute
    async def prepare(self, job, span):
        if crash_point == "pre_boundary":
            print("CANARY_PREPARE", flush=True)
            await asyncio.Future()
        return await original_execute(self, job, span)
    DiagnosticExecutionApplication._execute = prepare
    class Model(FakeModel):
        async def generate_content_async(self, request, stream=False):
            print("CANARY_EFFECT", flush=True)
            if crash_point == "post_boundary":
                await asyncio.Future()
            async for response in super().generate_content_async(request, stream):
                yield response
    adk = await open_adk_session_service(settings)
    stop = asyncio.Event()
    asyncio.get_running_loop().add_signal_handler(signal.SIGTERM, stop.set)
    try:
        resources = DiagnosticResources(
            adk, LocalStorageProvider(settings.artifact_local_storage_root),
            InactiveObservationExtractor(), model=Model(model="fake")
        )
        async with diagnostic_worker(settings, resources=resources) as runtime:
            runtime.start()
            print("CANARY_READY", flush=True)
            await stop.wait()
    finally:
        await adk.close()
asyncio.run(main())
"""
    children = []
    output_readers = []
    output = {}
    markers = {}

    async def read_output(process):
        async for line in process.stdout:
            output[process].append(line.decode(errors="replace").rstrip())
            for marker, reached in markers[process].items():
                if marker in line:
                    reached.set()

    async def start(point):
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-c",
            child_code,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        children.append(process)
        output[process] = deque(maxlen=100)
        markers[process] = {
            marker: asyncio.Event()
            for marker in (b"CANARY_READY", b"CANARY_PREPARE", b"CANARY_EFFECT")
        }
        output_readers.append(asyncio.create_task(read_output(process)))
        configuration = settings.model_dump(mode="json")
        configuration["nats_url"] = settings.nats_url.get_secret_value()
        process.stdin.write(
            (json.dumps(configuration) + "\n" + (point or "normal") + "\n").encode()
        )
        await process.stdin.drain()
        async with asyncio.timeout(60):
            await markers[process][b"CANARY_READY"].wait()
        return process

    @asynccontextmanager
    async def transaction():
        async with sessions() as session, session.begin():
            yield DiagnosticJobRepository(session)

    host = JobMaintenance(
        settings,
        transaction,
        NatsJobPublisher(settings),
        {
            ("diagnostic_turn", 1): DiagnosticRecoveryPolicy(0.01),
        },
    )
    async with sessions() as session, session.begin():
        repair = await session.get(RepairSession, repair_id)
        user = await session.get(User, repair.user_id)
        repair.status = "awaiting_user"
    app = create_app(settings)

    async def database():
        async with sessions() as session:
            yield session

    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_db_session] = database
    legacy = AsyncMock()
    monkeypatch.setattr(route, "execute_diagnostic_turn_background", legacy)
    monkeypatch.setattr(route, "execute_profile_inference_background", legacy)
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    port = listener.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, lifespan="off", log_level="error"))
    serving = asyncio.create_task(server.serve(sockets=[listener]))
    exclusion = sessions()
    try:
        process = await start(crash_point)  # compatible worker before producer
        async with asyncio.timeout(5):
            while not server.started:  # noqa: ASYNC110 - uvicorn exposes a flag
                await asyncio.sleep(0.01)
        async with httpx.AsyncClient(
            base_url=f"http://127.0.0.1:{port}", timeout=100
        ) as client:
            payload = {
                "schema_version": "ai_turn.v1",
                "client_turn_id": "external",
                "message": {"text": "Chain skips", "artifact_ids": []},
            }
            response = await client.post(
                f"/v1/repair-sessions/{repair_id}/turns", json=payload
            )
            assert response.status_code == 202
            accepted = response.json()
            async with sessions() as session:
                row = (
                    await session.scalars(
                        select(BackgroundJob).where(
                            BackgroundJob.input["turn_id"].astext == accepted["turn_id"]
                        )
                    )
                ).one()
                job = JobSnapshot.from_row(row)
            await exclusion.execute(
                select(BackgroundJob.id)
                .where(BackgroundJob.id != job.id)
                .with_for_update()
            )
            async with client.stream(
                "GET", accepted["event_stream_url"] + "&timeout_seconds=90"
            ) as stream:
                assert stream.status_code == 200
                await host.publish_once()
                if crash_point:
                    async with asyncio.timeout(10):
                        marker = (
                            b"CANARY_PREPARE"
                            if crash_point == "pre_boundary"
                            else b"CANARY_EFFECT"
                        )
                        await markers[process][marker].wait()
                    async with sessions() as session:
                        running = await session.get(BackgroundJob, job.id)
                        assert running.state == "running" and running.execution_token
                        assert (running.effect_boundary_at is not None) is (
                            crash_point == "post_boundary"
                        )
                    # Roll back the producer while the external owner is running.
                    settings.diagnostic_execution = "legacy"
                    replay = await client.post(
                        f"/v1/repair-sessions/{repair_id}/turns", json=payload
                    )
                    assert replay.json() == accepted
                    legacy.assert_not_awaited()
                    process.kill()
                    assert await process.wait() == -signal.SIGKILL
                    await asyncio.sleep(
                        max(
                            0,
                            (
                                running.execution_deadline - datetime.now(UTC)
                            ).total_seconds(),
                        )
                        + 0.05
                    )
                    await host.reconcile_once()
                    if crash_point == "pre_boundary":
                        await asyncio.sleep(0.02)
                        await host.publish_once()
                    # Pre-boundary jobs may retry; post-boundary jobs only settle.
                    process = await start(None)
                frames = []
                async for line in stream.aiter_lines():
                    if line.startswith("data: "):
                        event = json.loads(line[6:])
                        frames.append(event)
                        if event["type"] == "turn.completed":
                            break
                assert frames and frames[-1]["type"] == "turn.completed", "\n".join(
                    output[process]
                )
                sequences = [int(event["id"]) for event in frames]
                assert sequences == sorted(set(sequences))
                encoded = json.dumps(frames)
                assert job.id not in encoded and "adk_diagnostic_" not in encoded
            # Producer rollback never schedules a legacy task for this accepted key.
            settings.diagnostic_execution = "legacy"
            replay = await client.post(
                f"/v1/repair-sessions/{repair_id}/turns", json=payload
            )
            assert replay.json() == accepted
            legacy.assert_not_awaited()
        async with sessions() as session:
            row = await session.get(BackgroundJob, job.id)
            assert row.state == (
                "interrupted" if crash_point == "post_boundary" else "succeeded"
            )
            assert row.attempt_count == (2 if crash_point == "pre_boundary" else 1)
        process.terminate()
        assert await asyncio.wait_for(process.wait(), 10) == 0
        # A fresh key after drain uses legacy exactly once and creates no queue job.
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}") as client:
            response = await client.post(
                f"/v1/repair-sessions/{repair_id}/turns",
                json={**payload, "client_turn_id": "after-drain"},
            )
            assert response.status_code == 202
            legacy.assert_awaited_once_with(
                user.id, repair_id, response.json()["turn_id"]
            )
            async with sessions() as session:
                assert not list(
                    await session.scalars(
                        select(BackgroundJob).where(
                            BackgroundJob.input["turn_id"].astext
                            == response.json()["turn_id"]
                        )
                    )
                )
    finally:
        for process in children:
            if process.returncode is None:
                process.kill()
                await process.wait()
        await asyncio.gather(*output_readers)
        server.should_exit = True
        await asyncio.wait_for(serving, 5)
        listener.close()
        await host.close()
        await exclusion.rollback()
        await exclusion.close()
        # SSE owns a cached pool distinct from the injected acceptance pool.
        # Dispose it on this loop before another test uses the same database URL.
        await get_sessionmaker(settings.database_url).kw["bind"].dispose()


@pytest.mark.parametrize("reserved", [False, True])
async def test_reconstruction_restores_diagnostic_without_replaying_reserved_effects(
    context, reserved
):
    from contextlib import asynccontextmanager

    from bike_doc_api.core.nats import ensure_work_topology, jetstream, nats_connection
    from bike_doc_api.maintenance.hosting import recovery_policies
    from bike_doc_api.maintenance.nats_publisher import NatsJobPublisher
    from bike_doc_api.maintenance.reconstruction import reconstruct
    from bike_doc_api.maintenance.runtime import JobMaintenance
    from bike_doc_api.repositories.diagnostic_execution import DiagnosticExecutionFence
    from bike_doc_api.repositories.diagnostic_jobs import DiagnosticJobRepository

    settings, sessions, job, _, model, resources, _ = context

    @asynccontextmanager
    async def transaction():
        async with sessions() as session, session.begin():
            yield DiagnosticJobRepository(session)

    policies = recovery_policies(settings)
    host = JobMaintenance(settings, transaction, NatsJobPublisher(settings), policies)

    async def publish_current():
        # Earlier failure tests deliberately retain jobs in this disposable DB.
        # Exercise the real global publisher without assuming our row is first.
        for _ in range(100):
            await host.publish_once()
            async with sessions() as session:
                current = await session.get(BackgroundJob, job.id)
                if current.confirmed_generation == current.desired_generation:
                    return
        pytest.fail("current job publication did not complete within bounded batches")

    try:
        await publish_current()
        if reserved:
            execution, _ = await _fencing_execution(context, duration=0.1)
            async with sessions() as session:
                await DiagnosticExecutionFence(session, execution).cross_boundary()
            await asyncio.sleep(0.15)
        async with nats_connection(settings) as client:
            js = jetstream(client)
            await js.delete_stream(settings.nats_work_stream)

            async def provision():
                await ensure_work_topology(js, settings)

            await reconstruct(settings, transaction, provision, policies)
            assert await reconstruct(settings, transaction, provision, policies) == 0
            async with sessions() as session:
                row = await session.get(BackgroundJob, job.id)
                assert row.desired_generation == (1 if reserved else 2)
            await publish_current()
        if reserved:
            await host.reconcile_once()
            await _assert_error_pair(context, "interrupted", 1)
        async with diagnostic_worker(settings, resources=resources) as runtime:
            if reserved:
                # An original post-boundary notification cannot invoke the model,
                # even when manually delivered after topology reconstruction.
                await runtime.transport.client.jetstream().publish(
                    settings.nats_diagnostic_subject,
                    json.dumps(
                        {"version": 1, "job_id": job.id, "publication_generation": 1}
                    ).encode(),
                )
            for _ in range(1000):
                message = (await runtime.transport.subscription.fetch(1, timeout=3))[0]
                if json.loads(message.data)["job_id"] == job.id:
                    await runtime.process(NatsDelivery(message))
                    break
                await message.ack_sync()
            else:
                pytest.fail("reconstructed diagnostic notification was missing")
        assert len(model._requests) == (0 if reserved else 1)
        async with sessions() as session:
            row = await session.get(BackgroundJob, job.id)
            assert row.state == ("interrupted" if reserved else "succeeded")
            assert row.attempt_count == 1
    finally:
        await host.close()
