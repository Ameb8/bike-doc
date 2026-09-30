"""Real profile role, PostgreSQL/JetStream, and fake provider/storage adapters."""

import asyncio
import json
import os
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from bike_doc_api.api.v1.turns import get_turn_service
from bike_doc_api.core.config import Settings
from bike_doc_api.maintenance.runtime import JobMaintenance
from bike_doc_api.models._ids import generate_prefixed_ulid
from bike_doc_api.models.artifact import ArtifactRef
from bike_doc_api.models.background_job import BackgroundJob
from bike_doc_api.models.bike import BikeFactClaim, BikeProfile
from bike_doc_api.models.event import RepairSessionEvent
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
from bike_doc_api.schemas.turn import TurnCreate
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
        profile_inference_execution="durable_queue",
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
    user_id, bike_id, session_id, artifact_id = [
        generate_prefixed_ulid(prefix) for prefix in ("usr_", "bike_", "rs_", "art_")
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
                status="created",
                safety_state="ok",
                active_safety_flags=[],
            )
        )
        await session.flush()
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
    async with sessions() as session:
        user = await session.get(User, user_id)
        adk = AsyncMock()
        service = get_turn_service(session, adk, settings)
        accepted = await service.accept_turn(
            current_user=user,
            repair_session_id=session_id,
            request=TurnCreate.model_validate(
                {
                    "schema_version": "ai_turn.v1",
                    "client_turn_id": suffix,
                    "message": {
                        "text": "accepted caption",
                        "artifact_ids": [artifact_id],
                    },
                }
            ),
        )
        assert service.last_profile_job_recorded
        adk.create_session.assert_not_awaited()
        turn_id = accepted.turn_id
        row = (
            await session.scalars(
                select(BackgroundJob).where(
                    BackgroundJob.input["turn_id"].astext == turn_id
                )
            )
        ).one()
        job = JobSnapshot.from_row(row)
        assert job and job.desired_generation == 1 and job.confirmed_generation == 0
        assert job.input == {
            "turn_id": turn_id,
            "inference_schema_version": "bike_profile_inference.v1",
            "extractor_version": settings.profile_inference_extractor_version,
        }
        phase = (
            await session.scalars(
                select(RepairPhaseSession).where(
                    RepairPhaseSession.repair_session_id == session_id
                )
            )
        ).one()
        assert phase.adk_session_id is None
        event = (
            await session.scalars(
                select(RepairSessionEvent).where(RepairSessionEvent.turn_id == turn_id)
            )
        ).one()
        assert event.type == "turn.started" and event.sequence == 1
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


async def acceptance_context(
    sessions: async_sessionmaker[AsyncSession], job: JobSnapshot
) -> tuple[User, RepairTurn]:
    async with sessions() as session:
        turn = await session.get(RepairTurn, job.input["turn_id"])
        repair = await session.get(RepairSession, turn.repair_session_id)
        user = await session.get(User, repair.user_id)
        return user, turn


async def test_concurrent_replay_does_not_reselect_executor(
    setup: ProfileSetup,
) -> None:
    settings, sessions, job, _ = setup
    user, turn = await acceptance_context(sessions, job)
    # Reset this fixture's session so a new client key can race at acceptance.
    async with sessions() as session, session.begin():
        repair = await session.get(RepairSession, turn.repair_session_id)
        repair.status = "awaiting_user"
    request = TurnCreate.model_validate(
        {
            "schema_version": "ai_turn.v1",
            "client_turn_id": "concurrent",
            "message": turn.message,
        }
    )

    async def accept(selection: str = "durable_queue") -> tuple[str, bool, bool]:
        async with sessions() as session:
            service = get_turn_service(
                session,
                AsyncMock(),
                settings.model_copy(update={"profile_inference_execution": selection}),
            )
            result = await service.accept_turn(
                current_user=user,
                repair_session_id=turn.repair_session_id,
                request=request,
            )
            return (
                result.turn_id,
                service.last_acceptance_was_idempotent_replay,
                service.last_profile_job_recorded,
            )

    first, second = await asyncio.gather(accept(), accept())
    assert first[0] == second[0]
    assert sorted([first[1], second[1]]) == [False, True]
    assert sorted([first[2], second[2]]) == [False, True]
    replay = await accept("legacy")
    assert replay == (first[0], True, False)
    async with sessions() as session:
        jobs = list(
            await session.scalars(
                select(BackgroundJob).where(
                    BackgroundJob.input["turn_id"].astext == first[0]
                )
            )
        )
        events = list(
            await session.scalars(
                select(RepairSessionEvent).where(RepairSessionEvent.turn_id == first[0])
            )
        )
        assert len(jobs) == len(events) == 1


@pytest.mark.parametrize("failure", ["commit", "invalid_pin", "invalid_artifact"])
async def test_acceptance_rollback_has_no_product_or_job_intent(
    setup: ProfileSetup,
    failure: str,
) -> None:
    from bike_doc_api.core.errors import AppError

    settings, sessions, job, _ = setup
    user, turn = await acceptance_context(sessions, job)
    repair_id = generate_prefixed_ulid("rs_")
    async with sessions() as session, session.begin():
        session.add(
            RepairSession(
                id=repair_id,
                user_id=user.id,
                bike_id=setup[3],
                phase="diagnostic",
                status="created",
                safety_state="ok",
                active_safety_flags=[],
            )
        )
        await session.flush()
        artifact = await session.get(ArtifactRef, turn.message["artifact_ids"][0])
        artifact.repair_session_id = repair_id
        before_jobs = set(await session.scalars(select(BackgroundJob.id)))
    async with sessions() as session:
        service = get_turn_service(
            session,
            AsyncMock(),
            settings.model_copy(
                update={
                    "profile_inference_extractor_version": "invalid pin"
                    if failure == "invalid_pin"
                    else settings.profile_inference_extractor_version,
                }
            ),
        )
        if failure == "commit":
            service._commit = AsyncMock(side_effect=RuntimeError("injected loss"))
        with pytest.raises(AppError):
            await service.accept_turn(
                current_user=user,
                repair_session_id=repair_id,
                request=TurnCreate.model_validate(
                    {
                        "schema_version": "ai_turn.v1",
                        "client_turn_id": "rollback",
                        "message": {**turn.message, "artifact_ids": ["art_missing"]}
                        if failure == "invalid_artifact"
                        else turn.message,
                    }
                ),
            )
    async with sessions() as session:
        assert not list(
            await session.scalars(
                select(RepairTurn).where(
                    RepairTurn.repair_session_id == repair_id,
                    RepairTurn.client_turn_id == "rollback",
                )
            )
        )
        repair = await session.get(RepairSession, repair_id)
        assert repair.status == "created" and repair.latest_event_sequence == 0
        assert not list(
            await session.scalars(
                select(RepairPhaseSession).where(
                    RepairPhaseSession.repair_session_id == repair_id
                )
            )
        )
        assert not list(
            await session.scalars(
                select(RepairSessionEvent).where(
                    RepairSessionEvent.repair_session_id == repair_id
                )
            )
        )
        assert set(await session.scalars(select(BackgroundJob.id))) == before_jobs
        assert (
            len(
                list(
                    await session.scalars(
                        select(BackgroundJob).where(
                            BackgroundJob.input["turn_id"].astext == turn.id
                        )
                    )
                )
            )
            == 1
        )


async def test_acceptance_outage_ack_window_broker_and_worker_restart(
    setup: ProfileSetup,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from datetime import timedelta

    import httpx

    from bike_doc_api.api.deps import (
        get_current_user,
        get_db_session,
        get_diagnostic_adk_session_client,
    )
    from bike_doc_api.api.v1 import turns as route
    from bike_doc_api.main import create_app
    from bike_doc_api.maintenance.nats_publisher import NatsJobPublisher
    from bike_doc_api.repositories.background_jobs import PublicationClaim

    settings, sessions, initial, bike_id = setup
    container = os.getenv("BIKE_DOC_API_MAINTENANCE_TEST_NATS_CONTAINER")
    assert container, "canary requires disposable broker restart injection"

    async def broker(action: str) -> None:
        process = await asyncio.create_subprocess_exec(
            "docker", action, container, stdout=asyncio.subprocess.DEVNULL
        )
        assert await process.wait() == 0

    @asynccontextmanager
    async def transaction() -> AsyncIterator[ProfileJobRepository]:
        async with sessions() as session, session.begin():
            yield ProfileJobRepository(session)

    host = JobMaintenance(
        settings,
        transaction,
        NatsJobPublisher(settings),
        {
            ("profile_inference", 1): profile_policy(settings).reconciliation,
        },
    )
    user, turn = await acceptance_context(sessions, initial)
    exclusion = sessions()
    try:
        # Expand-first: a compatible idle worker is constructed before producing.
        async with profile_worker(
            settings.model_copy(update={"profile_inference_execution": "legacy"}),
            extractor=Extractor(),
            storage=Storage(),
        ) as runtime:
            info = await runtime.transport.client.jetstream().consumer_info(
                settings.nats_work_stream, settings.nats_profile_consumer
            )
            assert info.num_pending == 0
        await broker("stop")
        async with sessions() as session:
            repair = await session.get(RepairSession, turn.repair_session_id)
            repair.status = "awaiting_user"
            await session.commit()
        app = create_app(settings)

        async def database() -> AsyncIterator[AsyncSession]:
            async with sessions() as session:
                yield session

        app.dependency_overrides[get_db_session] = database
        app.dependency_overrides[get_current_user] = lambda: user
        app.dependency_overrides[get_diagnostic_adk_session_client] = lambda: (
            AsyncMock()
        )
        diagnostic = AsyncMock()
        legacy_profile = AsyncMock()
        monkeypatch.setattr(route, "execute_diagnostic_turn_background", diagnostic)
        monkeypatch.setattr(
            route, "execute_profile_inference_background", legacy_profile
        )
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://canary"
        ) as client:
            response = await asyncio.wait_for(
                client.post(
                    f"/v1/repair-sessions/{turn.repair_session_id}/turns",
                    json={
                        "schema_version": "ai_turn.v1",
                        "client_turn_id": "outage",
                        "message": turn.message,
                    },
                ),
                2,
            )
        assert response.status_code == 202
        accepted_id = response.json()["turn_id"]
        assert "job_id" not in response.json()
        diagnostic.assert_awaited_once_with(
            user.id, turn.repair_session_id, accepted_id
        )
        legacy_profile.assert_not_awaited()
        async with sessions() as session:
            row = (
                await session.scalars(
                    select(BackgroundJob).where(
                        BackgroundJob.input["turn_id"].astext == accepted_id
                    )
                )
            ).one()
            job = JobSnapshot.from_row(row)
            assert job.confirmed_generation == 0
        # Other failure tests intentionally retain jobs in this disposable DB.
        # Hold those rows so the real SKIP LOCKED publisher scans only this slice.
        await exclusion.execute(
            select(BackgroundJob.id)
            .where(BackgroundJob.id.not_in([initial.id, job.id]))
            .with_for_update()
        )
        await broker("start")
        # Publisher loses its process after broker ack, before durable confirmation.
        original = ProfileJobRepository.confirm_publication

        async def crash(self: ProfileJobRepository, claim: PublicationClaim) -> bool:
            raise asyncio.CancelledError()

        monkeypatch.setattr(ProfileJobRepository, "confirm_publication", crash)
        with pytest.raises(asyncio.CancelledError):
            await host.publish_once()
        monkeypatch.setattr(ProfileJobRepository, "confirm_publication", original)
        async with sessions() as session, session.begin():
            for identity in (initial.id, job.id):
                row = await session.get(BackgroundJob, identity)
                if row.publication_claim_until:
                    row.publication_claim_until = datetime.now(UTC) - timedelta(
                        seconds=1
                    )
        await host.publish_once()
        await broker("stop")
        await broker("start")
        extractor = Extractor()
        async with profile_worker(
            settings, extractor=extractor, storage=Storage()
        ) as runtime:
            transport = runtime.transport
            messages = await transport.subscription.fetch(2, timeout=3)
            for message in messages:
                await runtime.process(NatsDelivery(message))
            info = await transport.client.jetstream().consumer_info(
                settings.nats_work_stream, settings.nats_profile_consumer
            )
            assert info.num_ack_pending == 0 and info.num_pending == 0
        revision = (await state(sessions, job, bike_id))[2].profile_revision
        # Restart worker and duplicate a completed acceptance: no provider/effects.
        async with profile_worker(
            settings, extractor=extractor, storage=Storage()
        ) as runtime:
            await runtime.process(await notify(runtime, job))
        row, run, bike, claims = await state(sessions, job, bike_id)
        assert row.state == "succeeded" and run.status == "completed"
        assert row.attempt_count == 1 and extractor.calls == 2
        assert bike.profile_revision == revision and len(claims) == 2
    finally:
        await broker("start")
        await host.close()
        await exclusion.rollback()
        await exclusion.close()


async def test_queue_acceptance_remains_runnable_by_diagnostic_executor(
    setup: ProfileSetup,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from bike_doc_api.adk import background
    from bike_doc_api.adk.sessions import DiagnosticADKSessionClient
    from bike_doc_api.adk.storage import open_adk_session_service

    settings, sessions, job, _ = setup
    user, turn = await acceptance_context(sessions, job)
    adk = await open_adk_session_service(settings)
    try:
        # Simulate ADK creation committed but the app binding lost to a crash.
        async with sessions() as session:
            phase = await session.get(RepairPhaseSession, turn.repair_phase_session_id)
            identity = await DiagnosticADKSessionClient(adk).ensure_unbound_session(
                phase_session_id=phase.id, repair_session_id=turn.repair_session_id
            )
            assert phase.adk_session_id is None
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
        await background.execute_diagnostic_turn_background(
            user.id, turn.repair_session_id, turn.id
        )
        orchestrator.process_turn.assert_awaited_once()
        async with sessions() as session:
            phase = await session.get(RepairPhaseSession, turn.repair_phase_session_id)
            assert phase.adk_session_id == identity
            row = await session.get(BackgroundJob, job.id)
            assert row.state == "queued" and row.attempt_count == 0
        # Existing bindings are never replaced by subsequent diagnostic execution.
        await background.execute_diagnostic_turn_background(
            user.id, turn.repair_session_id, turn.id
        )
        assert orchestrator.process_turn.await_count == 2
        async with sessions() as session:
            phase = await session.get(RepairPhaseSession, turn.repair_phase_session_id)
            assert phase.adk_session_id == identity
    finally:
        await adk.close()


async def test_concurrent_distinct_turns_cannot_bypass_running_session(
    setup: ProfileSetup,
) -> None:
    from bike_doc_api.core.errors import SessionStateConflictError

    settings, sessions, job, _ = setup
    user, turn = await acceptance_context(sessions, job)
    async with sessions() as session, session.begin():
        repair = await session.get(RepairSession, turn.repair_session_id)
        repair.status = "awaiting_user"

    async def accept(key: str) -> object:
        async with sessions() as session:
            return await get_turn_service(session, AsyncMock(), settings).accept_turn(
                current_user=user,
                repair_session_id=turn.repair_session_id,
                request=TurnCreate.model_validate(
                    {
                        "schema_version": "ai_turn.v1",
                        "client_turn_id": key,
                        "message": turn.message,
                    }
                ),
            )

    results = await asyncio.gather(
        accept("distinct-a"), accept("distinct-b"), return_exceptions=True
    )
    assert sum(isinstance(result, SessionStateConflictError) for result in results) == 1
    async with sessions() as session:
        turns = list(
            await session.scalars(
                select(RepairTurn).where(
                    RepairTurn.repair_session_id == turn.repair_session_id,
                    RepairTurn.client_turn_id.in_(["distinct-a", "distinct-b"]),
                )
            )
        )
        assert len(turns) == 1
        jobs = list(
            await session.scalars(
                select(BackgroundJob).where(
                    BackgroundJob.input["turn_id"].astext == turns[0].id
                )
            )
        )
        assert len(jobs) == 1


async def test_external_process_loss_and_restart_share_one_provider_budget(
    setup: ProfileSetup,
) -> None:
    import signal
    import sys

    from bike_doc_api.maintenance.nats_publisher import NatsJobPublisher

    settings, sessions, job, bike_id = setup
    child_code = """
import asyncio, signal, sys
sys.path.insert(0, "tests/integration")
from test_profile_worker import Extractor, Storage
from bike_doc_api.core.config import Settings
from bike_doc_api.workers.profile_role import profile_worker

async def main():
    settings = Settings.model_validate_json(sys.stdin.readline())
    slow = sys.stdin.readline().strip() == "slow"
    class Provider(Extractor):
        async def extract(self, request):
            if slow:
                self.calls += 1
                print("CANARY_PROVIDER_ENTERED", flush=True)
                await asyncio.Future()
            return await super().extract(request)
    provider = Provider()
    stopped = asyncio.Event()
    loop = asyncio.get_running_loop()
    loop.add_signal_handler(signal.SIGTERM, stopped.set)
    async with profile_worker(
        settings, extractor=provider, storage=Storage()
    ) as runtime:
        runtime.start()
        print("CANARY_READY", flush=True)
        await stopped.wait()
    print(f"CANARY_CALLS={provider.calls}", flush=True)
asyncio.run(main())
"""

    @asynccontextmanager
    async def transaction() -> AsyncIterator[ProfileJobRepository]:
        async with sessions() as session, session.begin():
            yield ProfileJobRepository(session)

    host = JobMaintenance(
        settings,
        transaction,
        NatsJobPublisher(settings),
        {
            ("profile_inference", 1): profile_policy(settings).reconciliation,
        },
    )
    processes = []
    exclusion = sessions()

    async def start(slow: bool) -> asyncio.subprocess.Process:
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-c",
            child_code,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        processes.append(process)
        configuration = settings.model_dump(mode="json")
        configuration["nats_url"] = settings.nats_url.get_secret_value()
        process.stdin.write(
            (
                json.dumps(configuration) + "\n" + ("slow" if slow else "normal") + "\n"
            ).encode()
        )
        await process.stdin.drain()
        return process

    async def marker(
        process: asyncio.subprocess.Process, value: bytes, *, startup: bool = False
    ) -> None:
        async with asyncio.timeout(60 if startup else 10):
            while True:
                line = await process.stdout.readline()
                assert line, "external worker exited before canary marker"
                if value in line:
                    return

    try:
        await exclusion.execute(
            select(BackgroundJob.id).where(BackgroundJob.id != job.id).with_for_update()
        )
        await host.publish_once()
        first = await start(True)
        await marker(first, b"CANARY_READY", startup=True)
        await marker(first, b"CANARY_PROVIDER_ENTERED")
        row, run, bike, claims = await state(sessions, job, bike_id)
        assert row.attempt_count == run.attempt_count == 1 and not claims
        first.kill()
        assert await first.wait() == -signal.SIGKILL
        await recover(settings, sessions, job)
        await host.publish_once()
        second = await start(False)
        await marker(second, b"CANARY_READY", startup=True)
        async with asyncio.timeout(10):
            while True:
                if (await state(sessions, job, bike_id))[0].state == "succeeded":
                    break
                await asyncio.sleep(0.02)
        second.terminate()
        await marker(second, b"CANARY_CALLS=1")
        assert await second.wait() == 0
        row, run, bike, claims = await state(sessions, job, bike_id)
        assert row.attempt_count == run.attempt_count == 2
        assert row.attempt_count <= row.attempt_limit
        assert run.status == "completed" and len(claims) == 1
        assert bike.profile_revision == 1
    finally:
        for process in processes:
            if process.returncode is None:
                process.kill()
                await process.wait()
        await host.close()
        await exclusion.rollback()
        await exclusion.close()
