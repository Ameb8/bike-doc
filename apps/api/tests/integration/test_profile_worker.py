"""Real profile role, PostgreSQL/JetStream, and fake provider/storage adapters."""

import asyncio
import json
import os
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from bike_doc_api.core.config import Settings
from bike_doc_api.maintenance.runtime import JobMaintenance
from bike_doc_api.models._ids import generate_prefixed_ulid
from bike_doc_api.models.artifact import ArtifactRef
from bike_doc_api.models.background_job import BackgroundJob
from bike_doc_api.models.bike import BikeFactClaim, BikeProfile
from bike_doc_api.models.profile_inference import ProfileInferenceRun
from bike_doc_api.models.repair_session import (
    RepairPhaseSession,
    RepairSession,
    RepairTurn,
)
from bike_doc_api.models.user import User
from bike_doc_api.repositories.background_jobs import JobRecord, JobSnapshot
from bike_doc_api.repositories.bikes import BikeRepository
from bike_doc_api.repositories.profile_jobs import ProfileJobRepository
from bike_doc_api.schemas.background_jobs import ProfileInferenceInputV1
from bike_doc_api.schemas.profile_inference import ProfileInferenceRequest
from bike_doc_api.workers.adapters import NatsDelivery, NatsPullTransport
from bike_doc_api.workers.profile_inference import profile_policy
from bike_doc_api.workers.profile_role import profile_worker
from bike_doc_api.workers.runtime import PullWorker

DB_URL = os.getenv("BIKE_DOC_API_MAINTENANCE_TEST_DATABASE_URL")
NATS_URL = os.getenv("BIKE_DOC_API_MAINTENANCE_TEST_NATS_URL")
type ProfileSetup = tuple[Settings, async_sessionmaker[AsyncSession], JobSnapshot, str]


class NoPublisher:
    async def publish(self, subject: str, payload: bytes, message_id: str) -> None:
        raise AssertionError("reconciliation must not publish")

    async def close(self) -> None:
        pass


pytestmark = [
    pytest.mark.nats,
    pytest.mark.skipif(
        not DB_URL or not NATS_URL,
        reason="requires disposable migrated PostgreSQL and pinned JetStream",
    ),
]


class Storage:
    async def get_object(self, *, path: str, bucket: str | None) -> bytes:
        return b"private fake image"


class Extractor:
    def __init__(self) -> None:
        self.calls = 0
        self.crash = False
        self.fail = False
        self.slow = False
        self.abstain = False

    async def extract(self, request: ProfileInferenceRequest) -> dict[str, object]:
        self.calls += 1
        assert request.caption == "accepted caption"
        assert len(request.images) == 1
        if self.crash:
            self.crash = False
            raise asyncio.CancelledError()
        if self.fail:
            raise TimeoutError("private provider error")
        if self.slow:
            await asyncio.Future()
        return {
            "schema_version": "bike_profile_inference.v1",
            "scene": {
                "contains_bicycle": True,
                "multiple_bicycles": False,
                "target_relation": "installed_on_target_bike",
                "confidence_score": 0.99,
            },
            "claims": []
            if self.abstain
            else [
                {
                    "field_path": "brakes.rear.mechanism",
                    "value": "disc",
                    "subject_relation": "installed_on_target_bike",
                    "evidence_basis": "direct_visual",
                    "visibility": "clear",
                    "confidence_score": 0.99,
                    "artifact_ids": [request.images[0].artifact_id],
                    "observed_text": None,
                    "evidence_cues": ["A rear rotor is visible."],
                }
            ],
            "abstentions": [],
        }


@pytest.fixture
async def setup() -> AsyncIterator[
    tuple[Settings, async_sessionmaker[AsyncSession], JobSnapshot, str]
]:
    assert DB_URL and NATS_URL
    suffix = uuid.uuid4().hex[:12]
    settings = Settings(
        environment="test",
        database_url=DB_URL,
        nats_url=NATS_URL,
        nats_work_stream=f"PROFILE_{suffix}",
        nats_profile_subject=f"profile.{suffix}.profile",
        nats_diagnostic_subject=f"profile.{suffix}.diagnostic",
        nats_profile_consumer=f"profile_{suffix}",
        nats_diagnostic_consumer=f"diagnostic_{suffix}",
        profile_inference_policy_mode="bootstrap-v1",
        profile_worker_retry_seconds=0.01,
        worker_progress_seconds=0.1,
        profile_inference_timeout_seconds=1,
        profile_worker_handler_seconds=1.5,
        profile_worker_hard_seconds=2,
        profile_worker_timeout_grace_seconds=0.2,
    )
    engine = create_async_engine(DB_URL)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    user_id, bike_id, session_id, phase_id, turn_id, artifact_id = [
        generate_prefixed_ulid(prefix)
        for prefix in ("usr_", "bike_", "rs_", "phs_", "turn_", "art_")
    ]
    async with sessions() as session, session.begin():
        session.add(
            User(
                id=user_id,
                auth_subject=suffix,
                email="test@example.test",
                display_name="Test",
            )
        )
        await session.flush()
        session.add(BikeProfile(id=bike_id, user_id=user_id, display_name="Test"))
        await session.flush()
        session.add(
            RepairSession(
                id=session_id,
                user_id=user_id,
                bike_id=bike_id,
                phase="diagnostic",
                status="running",
                safety_state="ok",
                active_safety_flags=[],
            )
        )
        await session.flush()
        session.add(
            RepairPhaseSession(
                id=phase_id,
                repair_session_id=session_id,
                phase="diagnostic",
                adk_session_id=f"test-{suffix}",
            )
        )
        await session.flush()
        session.add(
            RepairTurn(
                id=turn_id,
                repair_session_id=session_id,
                repair_phase_session_id=phase_id,
                client_turn_id=suffix,
                request_hash="hash",
                schema_version="ai_turn.v1",
                phase="diagnostic",
                message={"text": "accepted caption", "artifact_ids": [artifact_id]},
                start_event_sequence=1,
            )
        )
        session.add(
            ArtifactRef(
                id=artifact_id,
                user_id=user_id,
                repair_session_id=session_id,
                purpose="diagnostic_photo",
                media_type="image",
                mime_type="image/jpeg",
                filename="private.jpg",
                byte_size=18,
                status="ready",
                content_sha256="a" * 64,
                storage_provider="local",
                storage_path="private/path.jpg",
            )
        )
        await session.flush()
        instruction = ProfileInferenceInputV1(
            turn_id=turn_id,
            inference_schema_version="bike_profile_inference.v1",
            extractor_version=settings.profile_inference_extractor_version,
        )
        job = await ProfileJobRepository(session).record(
            JobRecord(
                "profile_inference",
                "profile_inference",
                1,
                instruction.model_dump(),
                instruction.deduplication_key(),
                3,
            )
        )
    try:
        yield settings, sessions, job, bike_id
    finally:
        # The topology/database are disposable and owned by the verification script.
        await engine.dispose()


async def notify(
    runtime: PullWorker, job: JobSnapshot, generation: int = 1
) -> NatsDelivery:
    transport = runtime.transport
    assert isinstance(transport, NatsPullTransport)
    await transport.client.jetstream().publish(
        runtime.options.subject,
        json.dumps(
            {"version": 1, "job_id": job.id, "publication_generation": generation}
        ).encode(),
    )
    return NatsDelivery((await transport.subscription.fetch(1, timeout=3))[0])


async def state(
    sessions: async_sessionmaker[AsyncSession], job: JobSnapshot, bike_id: str
) -> tuple[BackgroundJob, ProfileInferenceRun, BikeProfile, list[BikeFactClaim]]:
    async with sessions() as session:
        row = await session.get(BackgroundJob, job.id)
        run = (
            await session.scalars(
                select(ProfileInferenceRun).where(
                    ProfileInferenceRun.turn_id == job.input["turn_id"]
                )
            )
        ).one()
        bike = await session.get(BikeProfile, bike_id)
        claims = list(
            await session.scalars(
                select(BikeFactClaim).where(BikeFactClaim.bike_id == bike_id)
            )
        )
        assert row and bike
        return row, run, bike, claims


async def recover(
    settings: Settings, sessions: async_sessionmaker[AsyncSession], job: JobSnapshot
) -> None:
    async with sessions() as session:
        row = await session.get(BackgroundJob, job.id)
        assert row and row.execution_deadline
        remaining = (row.execution_deadline - datetime.now(UTC)).total_seconds()
    await asyncio.sleep(max(0, remaining) + 0.02)

    @asynccontextmanager
    async def transaction() -> AsyncIterator[ProfileJobRepository]:
        async with sessions() as session, session.begin():
            yield ProfileJobRepository(session)

    maintenance = JobMaintenance(
        settings,
        transaction,
        NoPublisher(),
        {("profile_inference", 1): profile_policy(settings).reconciliation},
    )
    await maintenance.reconcile_once()
    await asyncio.sleep(0.02)


@pytest.mark.parametrize("crash_phase", ["precommit", "postcommit"])
async def test_ambiguous_crash_replays_with_one_run_and_idempotent_effects(
    setup: ProfileSetup, monkeypatch: pytest.MonkeyPatch, crash_phase: str
) -> None:
    settings, sessions, job, bike_id = setup
    extractor = Extractor()
    extractor.crash = crash_phase == "precommit"
    async with profile_worker(
        settings, extractor=extractor, storage=Storage()
    ) as runtime:
        if crash_phase == "postcommit":
            finish = runtime.store.finish

            async def lost_finish(*args: object) -> None:
                raise TimeoutError("lost commit window")

            monkeypatch.setattr(runtime.store, "finish", lost_finish)
            await runtime.process(await notify(runtime, job))
            monkeypatch.setattr(runtime.store, "finish", finish)
        else:
            with pytest.raises(asyncio.CancelledError):
                await runtime.process(await notify(runtime, job))
        row, run, bike, claims = await state(sessions, job, bike_id)
        assert row.state == "running"
        revision = bike.profile_revision
        await recover(settings, sessions, job)
        await runtime.process(await notify(runtime, job, 2))
        row, run, bike, claims = await state(sessions, job, bike_id)
        assert row.state == "succeeded" and row.attempt_count == 2
        assert run.status == "completed" and len(claims) == 1
        assert extractor.calls == (2 if crash_phase == "precommit" else 1)
        assert bike.profile_revision == (revision if crash_phase == "postcommit" else 1)
        await runtime.process(await notify(runtime, job, 2))
        assert (await state(sessions, job, bike_id))[
            2
        ].profile_revision == bike.profile_revision


@pytest.mark.parametrize("failure_phase", ["resolution", "commit"])
async def test_real_resolution_rollback_retries_database_only(
    setup: ProfileSetup, monkeypatch: pytest.MonkeyPatch, failure_phase: str
) -> None:
    settings, sessions, job, bike_id = setup
    extractor = Extractor()
    original = BikeRepository.save_resolution
    attempts = 0

    async def conflict(self: BikeRepository, resolution: object) -> object:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("resolution conflict")
        return await original(self, resolution)

    commits = 0
    original_commit = AsyncSession.commit

    async def commit_conflict(self: AsyncSession) -> None:
        nonlocal commits
        commits += 1
        if commits == 2:
            raise RuntimeError("commit conflict")
        await original_commit(self)

    if failure_phase == "resolution":
        monkeypatch.setattr(BikeRepository, "save_resolution", conflict)
    else:
        monkeypatch.setattr(AsyncSession, "commit", commit_conflict)
    async with profile_worker(
        settings, extractor=extractor, storage=Storage()
    ) as runtime:
        await runtime.process(await notify(runtime, job))
    row, run, bike, claims = await state(sessions, job, bike_id)
    assert row.state == "succeeded" and run.attempt_count == 1
    assert extractor.calls == 1 and len(claims) == 1
    assert attempts == 2 if failure_phase == "resolution" else commits == 3
    assert bike.profile_revision == 1


async def test_provider_exhaustion_is_one_call_per_application_attempt(
    setup: ProfileSetup,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level("INFO")
    settings, sessions, job, bike_id = setup
    extractor = Extractor()
    extractor.fail = True
    async with profile_worker(
        settings, extractor=extractor, storage=Storage()
    ) as runtime:
        for _ in range(3):
            await runtime.process(await notify(runtime, job))
            await asyncio.sleep(0.02)
        row, run, bike, claims = await state(sessions, job, bike_id)
        assert row.state == "dead" and row.attempt_count == 3
        assert run.status == "exhausted" and run.attempt_count == 3
        assert extractor.calls == 3 and not claims and bike.profile_revision == 0
    for private in (
        "accepted caption",
        "private fake image",
        "private provider error",
        "private/path.jpg",
        bike.user_id,
        settings.nats_url.get_secret_value(),
    ):
        assert private not in caplog.text


@pytest.mark.parametrize("lost", [False, True])
async def test_timeout_or_lost_last_execution_terminalizes_domain_audit(
    setup: ProfileSetup, lost: bool
) -> None:
    settings, sessions, job, bike_id = setup
    extractor = Extractor()
    extractor.slow = not lost
    async with profile_worker(
        settings, extractor=extractor, storage=Storage()
    ) as runtime:
        for _ in range(3):
            extractor.crash = lost
            if lost:
                with pytest.raises(asyncio.CancelledError):
                    await runtime.process(await notify(runtime, job, _ + 1))
                await recover(settings, sessions, job)
            else:
                await runtime.process(await notify(runtime, job))
                await asyncio.sleep(0.02)
    row, run, bike, claims = await state(sessions, job, bike_id)
    assert row.state == "dead" and row.latest_error_category == "attempts_exhausted"
    assert run.status == "exhausted" and run.attempt_count == 3
    assert extractor.calls == 3 and not claims and bike.profile_revision == 0


async def test_role_pull_loop_abstains_and_does_not_consume_diagnostic_workload(
    setup: ProfileSetup,
) -> None:
    settings, sessions, job, bike_id = setup
    extractor = Extractor()
    extractor.abstain = True
    async with profile_worker(
        settings, extractor=extractor, storage=Storage()
    ) as runtime:
        transport = runtime.transport
        js = transport.client.jetstream()
        await js.publish(settings.nats_diagnostic_subject, b"{}")
        await js.publish(
            settings.nats_profile_subject,
            json.dumps(
                {"version": 1, "job_id": job.id, "publication_generation": 1}
            ).encode(),
        )
        runtime.start()
        async with asyncio.timeout(3):
            while True:
                async with sessions() as session:
                    row = await session.get(BackgroundJob, job.id)
                    if row.state == "succeeded":
                        break
                await asyncio.sleep(0.02)
        diagnostic = await js.consumer_info(
            settings.nats_work_stream, settings.nats_diagnostic_consumer
        )
        assert diagnostic.num_pending == 1 and diagnostic.num_ack_pending == 0
    row, run, _bike, claims = await state(sessions, job, bike_id)
    assert run.status == "abstained" and extractor.calls == 1 and not claims


@pytest.mark.parametrize(
    "field,value",
    [
        ("inference_schema_version", "bike_profile_inference.v2"),
        ("extractor_version", "unknown.v1"),
    ],
)
async def test_unsupported_behavior_pin_permanently_fails_before_provider(
    setup: ProfileSetup, field: str, value: str
) -> None:
    settings, sessions, initial, _bike_id = setup
    instruction = ProfileInferenceInputV1.model_validate(
        {**initial.input, field: value}
    )
    async with sessions() as session, session.begin():
        job = await ProfileJobRepository(session).record(
            JobRecord(
                "profile_inference",
                "profile_inference",
                1,
                instruction.model_dump(),
                instruction.deduplication_key(),
                3,
            )
        )
    extractor = Extractor()
    async with profile_worker(
        settings, extractor=extractor, storage=Storage()
    ) as runtime:
        await runtime.process(await notify(runtime, job))
    async with sessions() as session:
        row = await session.get(BackgroundJob, job.id)
        assert (
            row.state == "dead"
            and row.latest_error_category == "version_unsupported"
            and row.attempt_count == 0
        )
        assert not list(
            await session.scalars(
                select(ProfileInferenceRun).where(
                    ProfileInferenceRun.turn_id == instruction.turn_id
                )
            )
        )
    assert extractor.calls == 0


async def test_late_cancelled_provider_response_cannot_write_after_recovery(
    setup: ProfileSetup,
) -> None:
    settings, sessions, job, bike_id = setup
    release = asyncio.Event()

    class LateExtractor:
        def __init__(self) -> None:
            self.calls = 0

        async def extract(self, request: ProfileInferenceRequest) -> dict[str, object]:
            self.calls += 1
            if self.calls == 1:
                # A provider that resists cancellation outlives the hard deadline.
                while not release.is_set():
                    try:
                        await release.wait()
                    except asyncio.CancelledError:
                        continue
            return await Extractor().extract(request)

    extractor = LateExtractor()
    async with profile_worker(
        settings, extractor=extractor, storage=Storage()
    ) as runtime:
        try:
            await runtime.process(await notify(runtime, job))
            assert runtime._orphaned
            await recover(settings, sessions, job)
            await runtime.process(await notify(runtime, job, 2))
            orphaned = set(runtime._orphaned)
            release.set()
            _, pending = await asyncio.wait(orphaned, timeout=3)
            assert not pending
            row, run, bike, claims = await state(sessions, job, bike_id)
            assert row.state == "succeeded" and row.attempt_count == 2
            assert run.status == "completed" and run.attempt_count == 2
            assert (
                extractor.calls == 2 and len(claims) == 1 and bike.profile_revision == 1
            )
        finally:
            release.set()
