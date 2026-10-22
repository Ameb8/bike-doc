"""Real PostgreSQL/JetStream maintenance contract: task test:maintenance."""

import asyncio
import json
import os
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from bike_doc_api.core.config import Settings
from bike_doc_api.core.nats import jetstream, nats_connection
from bike_doc_api.maintenance.nats_publisher import NatsJobPublisher
from bike_doc_api.maintenance.runtime import JobMaintenance, SystemClock
from bike_doc_api.models._ids import generate_prefixed_ulid
from bike_doc_api.models.background_job import BackgroundJob
from bike_doc_api.repositories.background_jobs import (
    BackgroundJobRepository,
    ExecutionPolicy,
    JobError,
    JobOutcome,
    JobRecord,
    JobSnapshot,
    PublicationClaim,
    ResolutionKind,
)
from bike_doc_api.schemas.background_jobs import ProfileInferenceInputV1

DB_URL = os.getenv("BIKE_DOC_API_MAINTENANCE_TEST_DATABASE_URL")
NATS_URL = os.getenv("BIKE_DOC_API_MAINTENANCE_TEST_NATS_URL")
pytestmark = [
    pytest.mark.nats,
    pytest.mark.skipif(
        not DB_URL or not NATS_URL,
        reason="requires disposable migrated PostgreSQL and JetStream",
    ),
]


class ReplayPolicy:
    def expired_outcome(self, job: JobSnapshot) -> JobOutcome | None:
        if job.effect_boundary_at is not None:
            return None
        return JobOutcome("retrying", JobError.EXECUTION_LOST, timedelta(seconds=1))

    def may_republish(self, job: JobSnapshot) -> bool:
        return job.effect_boundary_at is None


class Harness:
    def __init__(
        self, sessions: async_sessionmaker[AsyncSession], settings: Settings
    ) -> None:
        self.sessions = sessions
        self.settings = settings
        self.jobs: list[str] = []
        self.hosts: list[JobMaintenance] = []

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[BackgroundJobRepository]:
        async with self.sessions() as session, session.begin():
            yield BackgroundJobRepository(session)

    def host(self) -> JobMaintenance:
        host = JobMaintenance(
            self.settings,
            self.transaction,
            NatsJobPublisher(self.settings),
            {("profile_inference", 1): ReplayPolicy()},
            jitter=lambda: 1,
        )
        self.hosts.append(host)
        return host

    async def create(self, **values: object) -> JobSnapshot:
        input = ProfileInferenceInputV1(
            turn_id=generate_prefixed_ulid("turn_"),
            inference_schema_version="bike_profile_inference.v1",
            extractor_version="maintenance.v1",
        )
        async with self.sessions() as session, session.begin():
            job = await BackgroundJobRepository(session).record(
                JobRecord(
                    "profile_inference",
                    "profile_inference",
                    values.pop("input_version", 1),
                    input.model_dump(mode="json"),
                    input.deduplication_key(),
                    values.pop("attempt_limit", 3),
                )
            )
            self.jobs.append(job.id)
            if values:
                await session.execute(
                    update(BackgroundJob)
                    .where(BackgroundJob.id == job.id)
                    .values(**values)
                )
            return job

    async def row(self, job_id: str) -> BackgroundJob:
        async with self.sessions() as session:
            return (
                await session.scalars(
                    select(BackgroundJob).where(BackgroundJob.id == job_id)
                )
            ).one()


@pytest_asyncio.fixture
async def harness() -> AsyncIterator[Harness]:
    assert DB_URL and NATS_URL
    suffix = uuid.uuid4().hex[:12]
    settings = Settings(
        environment="test",
        database_url=DB_URL,
        nats_url=NATS_URL,
        nats_work_stream=f"MAINTENANCE_{suffix}",
        nats_diagnostic_subject=f"maintenance.{suffix}.diagnostic",
        nats_profile_subject=f"maintenance.{suffix}.profile",
        nats_diagnostic_consumer=f"diagnostic_{suffix}",
        nats_profile_consumer=f"profile_{suffix}",
        job_publish_timeout_seconds=3,
        job_publication_claim_seconds=10,
        job_backoff_initial_seconds=0.1,
        job_backoff_max_seconds=1,
        job_no_progress_seconds=12,
        job_reconciliation_poll_seconds=1,
    )
    engine = create_async_engine(DB_URL)
    harness = Harness(async_sessionmaker(engine, expire_on_commit=False), settings)
    try:
        yield harness
    finally:
        for host in harness.hosts:
            await host.close()
        async with harness.sessions() as session, session.begin():
            await session.execute(
                delete(BackgroundJob).where(BackgroundJob.id.in_(harness.jobs))
            )
        async with nats_connection(settings) as client:
            js = jetstream(client)
            if harness.hosts:
                # Some reconciliation-only tests never initialize topology.
                from nats.js.errors import NotFoundError

                with suppress(NotFoundError):
                    await js.delete_stream(settings.nats_work_stream)
        await engine.dispose()


async def test_ack_window_replay_deduplicates_and_preserves_newer_intent(
    harness: Harness,
) -> None:
    job = await harness.create()
    host = harness.host()
    host._settings = harness.settings.model_copy(
        update={"job_publication_claim_seconds": 4}
    )
    acknowledged = asyncio.Event()
    actual_transaction = harness.transaction

    class CrashWindowRepository(BackgroundJobRepository):
        async def confirm_publication(self, claim: PublicationClaim) -> bool:
            acknowledged.set()
            await asyncio.Future[None]()
            return False

    @asynccontextmanager
    async def crashing_transaction() -> AsyncIterator[BackgroundJobRepository]:
        async with harness.sessions() as session, session.begin():
            yield CrashWindowRepository(session)

    host._transaction = crashing_transaction
    publishing = asyncio.create_task(host.publish_once())
    await asyncio.wait_for(acknowledged.wait(), 10)
    publishing.cancel()
    with pytest.raises(asyncio.CancelledError):
        await publishing
    abandoned = await harness.row(job.id)
    assert (
        abandoned.confirmed_generation == 0
        and abandoned.publication_claim_generation == 1
    )
    await asyncio.sleep(4.1)
    host._transaction = actual_transaction
    await host.publish_once()
    assert (await harness.row(job.id)).confirmed_generation == 1
    async with nats_connection(harness.settings) as client:
        js = jetstream(client)
        assert (
            await js.stream_info(harness.settings.nats_work_stream)
        ).state.messages == 1
        sub = await js.pull_subscribe(
            harness.settings.nats_profile_subject,
            durable=harness.settings.nats_profile_consumer,
            stream=harness.settings.nats_work_stream,
        )
        message = (await sub.fetch(1, timeout=3))[0]
        assert json.loads(message.data) == {
            "version": 1,
            "job_id": job.id,
            "publication_generation": 1,
        }
        assert message.headers and message.headers["Nats-Msg-Id"] == f"{job.id}:1"
        await message.ack_sync()

    # Advance new intent while g=2 is in flight. Confirmation must stop at 2.
    async with harness.sessions() as session, session.begin():
        await session.execute(
            update(BackgroundJob)
            .where(BackgroundJob.id == job.id)
            .values(desired_generation=2)
        )
    async with harness.transaction() as repository:
        claim = (
            await repository.claim_publications(
                limit=1, claim_duration=timedelta(seconds=10)
            )
        )[0]
    async with harness.sessions() as session, session.begin():
        await session.execute(
            update(BackgroundJob)
            .where(BackgroundJob.id == job.id)
            .values(desired_generation=4)
        )
    from bike_doc_api.maintenance.runtime import publication_message

    payload, message_id = publication_message(claim)
    await host._publisher.publish(
        harness.settings.nats_profile_subject, payload, message_id
    )
    async with harness.transaction() as repository:
        assert await repository.confirm_publication(claim)
    row = await harness.row(job.id)
    assert (row.desired_generation, row.confirmed_generation) == (4, 2)
    await host.publish_once()
    assert (await harness.row(job.id)).confirmed_generation == 4
    async with nats_connection(harness.settings) as client:
        js = jetstream(client)
        sub = await js.pull_subscribe(
            harness.settings.nats_profile_subject,
            durable=harness.settings.nats_profile_consumer,
            stream=harness.settings.nats_work_stream,
        )
        messages = await sub.fetch(2, timeout=3)
        assert [
            json.loads(message.data)["publication_generation"] for message in messages
        ] == [2, 4]
        for message in messages:
            await message.ack_sync()


async def test_two_hosts_claim_and_reconcile_without_duplicate_attempts(
    harness: Harness,
) -> None:
    jobs = [await harness.create() for _ in range(20)]
    first, second = harness.host(), harness.host()
    # The tuned per-host batch is eight: drain twenty intents over bounded
    # cycles rather than assuming one replica can publish the whole fixture.
    for _ in range(2):
        await asyncio.gather(first.publish_once(), second.publish_once())
    async with nats_connection(harness.settings) as client:
        assert (
            await jetstream(client).stream_info(harness.settings.nats_work_stream)
        ).state.messages == 20
    for job in jobs:
        row = await harness.row(job.id)
        assert row.confirmed_generation == row.desired_generation == 1
        assert row.attempt_count == 0

    # No active publication claims: two independent scans recover once per row.
    await asyncio.sleep(12.1)
    await asyncio.gather(first.reconcile_once(), second.reconcile_once())
    for job in jobs:
        row = await harness.row(job.id)
        assert row.desired_generation == 2 and row.attempt_count == 0
    for _ in range(2):
        await asyncio.gather(first.publish_once(), second.publish_once())
    async with nats_connection(harness.settings) as client:
        assert (
            await jetstream(client).stream_info(harness.settings.nats_work_stream)
        ).state.messages == 40


async def test_abandoned_claim_replaced_token_cannot_confirm(harness: Harness) -> None:
    job = await harness.create()
    async with harness.transaction() as repository:
        old = (
            await repository.claim_publications(
                limit=1, claim_duration=timedelta(seconds=0.1)
            )
        )[0]
    await asyncio.sleep(0.15)
    async with harness.transaction() as repository:
        new = (
            await repository.claim_publications(
                limit=1, claim_duration=timedelta(seconds=3)
            )
        )[0]
        assert new.token != old.token
        assert not await repository.confirm_publication(old)
    host = harness.host()
    from bike_doc_api.maintenance.runtime import publication_message

    await host._publisher.publish(
        harness.settings.nats_profile_subject, *publication_message(new)
    )
    async with harness.transaction() as repository:
        assert await repository.confirm_publication(new)
    assert (await harness.row(job.id)).confirmed_generation == 1


async def test_reconciliation_preserves_eligibility_deadlines_attempts_and_policy(
    harness: Harness,
) -> None:
    eligible = await harness.create()
    early = await harness.create(eligible_at=datetime.now(UTC) + timedelta(minutes=1))
    unsupported = await harness.create(input_version=2)
    active = await harness.create()
    expired = await harness.create()
    protected = await harness.create()
    exhausted = await harness.create(attempt_limit=1)
    terminal = await harness.create()
    for job in [active, expired, protected, exhausted, terminal]:
        async with harness.transaction() as repository:
            resolution = await repository.resolve_delivery(
                job_id=job.id,
                generation=1,
                workload_class="profile_inference",
                validate_definition=lambda _, current=job: ExecutionPolicy(
                    timedelta(seconds=0.2)
                    if current != active
                    else timedelta(minutes=1)
                ),
            )
            assert resolution.kind == ResolutionKind.EXECUTABLE
        if job == terminal:
            assert resolution.job and resolution.job.execution_token
            async with harness.transaction() as repository:
                assert await repository.apply_outcome(
                    job_id=job.id,
                    execution_token=resolution.job.execution_token,
                    outcome=JobOutcome("succeeded"),
                )
    async with harness.sessions() as session, session.begin():
        await session.execute(
            update(BackgroundJob)
            .where(BackgroundJob.id == protected.id)
            .values(effect_boundary_at=datetime.now(UTC))
        )
    await asyncio.sleep(12.1)
    first, second = harness.host(), harness.host()
    await asyncio.gather(first.reconcile_once(), second.reconcile_once())
    assert (await harness.row(eligible.id)).desired_generation == 2
    for job in [early, unsupported, active, protected, terminal]:
        assert (await harness.row(job.id)).desired_generation == 1
    recovered = await harness.row(expired.id)
    assert recovered.state == "retrying" and recovered.attempt_count == 1
    assert (
        recovered.desired_generation == 2
        and recovered.eligible_at == recovered.publication_eligible_at
    )
    assert recovered.eligible_at > datetime.now(UTC)
    dead = await harness.row(exhausted.id)
    assert dead.state == "dead" and dead.desired_generation == 1
    assert dead.latest_error_category == "attempts_exhausted"
    assert (await harness.row(protected.id)).state == "running"
    assert (await harness.row(terminal.id)).state == "succeeded"
    # Publication/reconciliation may not replace tokens or increment attempts.
    await asyncio.gather(first.reconcile_once(), second.reconcile_once())
    assert (await harness.row(expired.id)).attempt_count == 1


async def test_broker_outage_does_not_gate_commits_and_polling_resumes(
    harness: Harness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from bike_doc_api.core.queue_telemetry import (
        RecordingQueueTelemetry,
        queue_telemetry,
    )
    from bike_doc_api.operations.inspection import inspect_jobs
    from bike_doc_api.repositories.job_inspection import InspectionFilter

    recorder = RecordingQueueTelemetry()
    monkeypatch.setattr(queue_telemetry, "_sink", recorder.observations.append)
    container = os.getenv("BIKE_DOC_API_MAINTENANCE_TEST_NATS_CONTAINER")
    if not container:
        pytest.skip("requires disposable broker container for stop/start injection")

    async def broker_action(action: str) -> None:
        process = await asyncio.create_subprocess_exec(
            "docker",
            action,
            container,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        assert await process.wait() == 0

    await broker_action("stop")
    host = harness.host()
    host._settings = harness.settings.model_copy(
        update={"job_no_progress_seconds": 300}
    )
    try:
        host.start()
        job = await asyncio.wait_for(harness.create(), 2)
        for _ in range(60):
            row = await harness.row(job.id)
            if row.transport_error:
                break
            await asyncio.sleep(0.1)
        assert row.confirmed_generation == 0 and row.desired_generation == 1
        assert row.transport_error in {"unavailable", "timeout"}
        snapshot = await inspect_jobs(
            InspectionFilter(), transport=True, settings=harness.settings
        )
        assert snapshot["postgresql_truth"]["jobs"]
        assert snapshot["jetstream_transport_observations"] == {"status": "unavailable"}
        assert any(
            o.metric == "publication"
            and o.attributes["publication"] in {"unavailable", "timeout"}
            for o in recorder.observations
        )
        await broker_action("start")
        for _ in range(150):
            if (await harness.row(job.id)).confirmed_generation == 1:
                break
            await asyncio.sleep(0.1)
        assert (await harness.row(job.id)).confirmed_generation == 1
        # The established adapter must also resume through its reconnect seam.
        await broker_action("stop")
        next_job = await asyncio.wait_for(harness.create(), 2)
        await asyncio.sleep(3.5)
        assert (await harness.row(next_job.id)).confirmed_generation == 0
        await broker_action("start")
        for _ in range(150):
            if (await harness.row(next_job.id)).confirmed_generation == 1:
                break
            await asyncio.sleep(0.1)
        assert (await harness.row(next_job.id)).confirmed_generation == 1
        assert any(o.metric == "publication_ack" for o in recorder.observations)
        async with nats_connection(harness.settings) as client:
            # Both acknowledgements survive the broker's persistent restart.
            assert (
                await jetstream(client).stream_info(harness.settings.nats_work_stream)
            ).state.messages == 2
    finally:
        await broker_action("start")
        await host.close()


async def test_reconstruction_broker_loss_restart_overlap_and_delivery(
    harness: Harness,
) -> None:
    from bike_doc_api.core.nats import ensure_work_topology
    from bike_doc_api.maintenance.hosting import recovery_policies
    from bike_doc_api.maintenance.reconstruction import reconstruct

    safe = [await harness.create() for _ in range(5)]
    early = await harness.create(eligible_at=datetime.now(UTC) + timedelta(hours=1))
    active = await harness.create()
    unsafe = await harness.create()
    terminal = await harness.create()
    for job in [active, unsafe, terminal]:
        async with harness.transaction() as repo:
            resolution = await repo.resolve_delivery(
                job_id=job.id,
                generation=1,
                workload_class="profile_inference",
                validate_definition=lambda _: ExecutionPolicy(timedelta(minutes=1)),
            )
            assert resolution.job and resolution.job.execution_token
            if job == terminal:
                assert await repo.apply_outcome(
                    job_id=job.id,
                    execution_token=resolution.job.execution_token,
                    outcome=JobOutcome("succeeded"),
                )
    async with harness.sessions() as session, session.begin():
        await session.execute(
            update(BackgroundJob)
            .where(BackgroundJob.id == unsafe.id)
            .values(
                effect_boundary_at=datetime.now(UTC),
            )
        )
    host = harness.host()
    # Nine initial intents exceed the tuned per-cycle batch of eight.
    await host.publish_once()
    await host.publish_once()
    async with nats_connection(harness.settings) as client:
        js = jetstream(client)
        await js.delete_stream(harness.settings.nats_work_stream)

        async def provision() -> None:
            await ensure_work_topology(js, harness.settings)

        settings = harness.settings.model_copy(
            update={"job_reconstruction_page_limit": 2}
        )
        policies = recovery_policies(settings)
        pages = 0

        @asynccontextmanager
        async def interrupted() -> AsyncIterator[BackgroundJobRepository]:
            nonlocal pages
            if pages == 1:
                raise asyncio.CancelledError
            async with harness.transaction() as repository:
                yield repository
            pages += 1

        with pytest.raises(asyncio.CancelledError):
            await reconstruct(settings, interrupted, provision, policies)
        assert (await js.stream_info(settings.nats_work_stream)).state.messages == 0
        # Two finite operations can overlap the normal reconciler. Pending
        # intent coalesces under the same locks instead of losing generations.
        await asyncio.gather(
            reconstruct(settings, harness.transaction, provision, policies),
            reconstruct(settings, harness.transaction, provision, policies),
            host.reconcile_once(),
        )
        assert (
            await reconstruct(settings, harness.transaction, provision, policies) == 0
        )
        for job in safe:
            row = await harness.row(job.id)
            assert row.desired_generation == 2 and row.attempt_count == 0
        for job in [early, active, unsafe, terminal]:
            assert (await harness.row(job.id)).desired_generation == 1
        await host.publish_once()
        sub = await js.pull_subscribe(
            settings.nats_profile_subject,
            durable=settings.nats_profile_consumer,
            stream=settings.nats_work_stream,
        )
        messages = await sub.fetch(len(safe), timeout=3)
        assert {json.loads(message.data)["job_id"] for message in messages} == {
            job.id for job in safe
        }
        for message in messages:
            envelope = json.loads(message.data)
            async with harness.transaction() as repo:
                result = await repo.resolve_delivery(
                    job_id=envelope["job_id"],
                    generation=envelope["publication_generation"],
                    workload_class="profile_inference",
                    validate_definition=lambda _: ExecutionPolicy(timedelta(minutes=1)),
                )
                assert result.kind == ResolutionKind.EXECUTABLE
            await message.ack_sync()
        # Even injected terminal/reserved deliveries cannot execute.
        for job in [terminal, unsafe]:
            async with harness.transaction() as repo:
                result = await repo.resolve_delivery(
                    job_id=job.id,
                    generation=1,
                    workload_class="profile_inference",
                    validate_definition=lambda _: ExecutionPolicy(timedelta(minutes=1)),
                )
                assert result.kind in {ResolutionKind.TERMINAL, ResolutionKind.RUNNING}


async def test_reconstruction_incompatible_topology_leaves_truth(
    harness: Harness,
) -> None:
    from nats.js.api import StreamConfig

    from bike_doc_api.core.nats import ensure_work_topology
    from bike_doc_api.maintenance.hosting import recovery_policies
    from bike_doc_api.maintenance.reconstruction import reconstruct

    job = await harness.create()
    harness.host()  # fixture owns disposable topology cleanup
    async with nats_connection(harness.settings) as client:
        js = jetstream(client)
        await js.add_stream(
            config=StreamConfig(
                name=harness.settings.nats_work_stream,
                subjects=[harness.settings.nats_profile_subject],
            )
        )

        async def provision() -> None:
            await ensure_work_topology(js, harness.settings)

        with pytest.raises(ValueError, match="incompatible"):
            await reconstruct(
                harness.settings,
                harness.transaction,
                provision,
                recovery_policies(harness.settings),
            )
    assert (await harness.row(job.id)).desired_generation == 1


async def historical_job(harness: Harness, **changes: object) -> str:
    past = datetime.now(UTC) - timedelta(days=10)
    values = dict(
        id=generate_prefixed_ulid("job_"),
        job_kind="profile_inference",
        workload_class="profile_inference",
        input_version=1,
        input={},
        deduplication_key=uuid.uuid4().hex,
        attempt_limit=3,
        state="dead",
        created_at=past,
        updated_at=past,
        eligible_at=past,
        terminal_at=past,
        publication_eligible_at=past,
        desired_generation=1,
        confirmed_generation=1,
        publication_confirmed_at=past,
    )
    values.update(changes)
    row = BackgroundJob(**values)
    async with harness.sessions() as session, session.begin():
        session.add(row)
    harness.jobs.append(row.id)
    return row.id


async def test_retention_boundaries_refusals_and_competing_cleanup(
    harness: Harness,
) -> None:
    now = datetime.now(UTC)
    cutoff = now - timedelta(days=7)

    class RetentionClock(SystemClock):
        def now(self) -> datetime:
            return now

    old = [
        await historical_job(harness, state=state)
        for state in ["succeeded", "dead", "interrupted"]
    ]
    refused = [
        await historical_job(harness, state="queued", terminal_at=None),
        await historical_job(harness, desired_generation=2),
        await historical_job(harness, terminal_at=cutoff + timedelta(microseconds=1)),
        await historical_job(harness, updated_at=datetime.now(UTC)),
        await historical_job(
            harness,
            publication_claim_token="held",
            publication_claim_generation=1,
            publication_claim_until=datetime.now(UTC) + timedelta(seconds=30),
        ),
    ]
    # Exact boundary is inclusive; a microsecond younger is refused.
    edge = await historical_job(harness, terminal_at=cutoff)
    async with harness.transaction() as repo:
        assert await repo.delete_retained(job_id=edge, before=cutoff)
        for job_id in refused:
            assert not await repo.delete_retained(job_id=job_id, before=cutoff)
    first, second = harness.host(), harness.host()
    # Both scans evaluate the same cutoff as the exact-boundary assertions.
    # Database/container startup time must not age the refused row into retention.
    first._clock = second._clock = RetentionClock()
    first._settings = harness.settings.model_copy(update={"job_cleanup_batch_limit": 2})
    second._settings = first._settings
    counts = await asyncio.gather(first.cleanup_once(), second.cleanup_once())
    assert sum(counts) == len(old) and max(counts) <= 2
    async with harness.sessions() as session:
        remaining = set(
            (
                await session.scalars(
                    select(BackgroundJob.id).where(BackgroundJob.id.in_(harness.jobs))
                )
            ).all()
        )
    assert remaining == set(refused)


async def test_cleanup_waits_and_rechecks_concurrent_confirmation(
    harness: Harness,
) -> None:
    job_id = await historical_job(
        harness, confirmed_generation=0, publication_confirmed_at=None
    )
    cutoff = datetime.now(UTC) - timedelta(days=7)
    async with harness.sessions() as session, session.begin():
        await session.execute(
            select(BackgroundJob).where(BackgroundJob.id == job_id).with_for_update()
        )

        async def cleanup() -> bool:
            async with harness.transaction() as repo:
                return await repo.delete_retained(job_id=job_id, before=cutoff)

        task = asyncio.create_task(cleanup())
        await asyncio.sleep(0.05)
        assert not task.done()
        await session.execute(
            update(BackgroundJob)
            .where(BackgroundJob.id == job_id)
            .values(
                confirmed_generation=1,
                publication_confirmed_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
            )
        )
    assert not await task
    assert (await harness.row(job_id)).state == "dead"


async def test_reconstruction_preserves_intent_across_old_broker_ack(
    harness: Harness,
) -> None:
    from bike_doc_api.core.nats import ensure_work_topology
    from bike_doc_api.maintenance.hosting import recovery_policies
    from bike_doc_api.maintenance.reconstruction import reconstruct

    job = await harness.create()
    host = harness.host()
    async with harness.transaction() as repo:
        old = (
            await repo.claim_publications(limit=1, claim_duration=timedelta(seconds=30))
        )[0]
    # Simulate the ACK window: broker acceptance precedes broker-data loss;
    # PostgreSQL confirmation arrives after reconstruction.
    from bike_doc_api.maintenance.runtime import publication_message

    await host._publisher.publish(
        harness.settings.nats_profile_subject, *publication_message(old)
    )
    async with nats_connection(harness.settings) as client:
        js = jetstream(client)
        await js.delete_stream(harness.settings.nats_work_stream)

        async def provision() -> None:
            await ensure_work_topology(js, harness.settings)

        assert (
            await reconstruct(
                harness.settings,
                harness.transaction,
                provision,
                recovery_policies(harness.settings),
            )
            == 1
        )
        assert (
            await reconstruct(
                harness.settings,
                harness.transaction,
                provision,
                recovery_policies(harness.settings),
            )
            == 0
        )
        async with harness.transaction() as repo:
            assert await repo.confirm_publication(old)
        row = await harness.row(job.id)
        assert (row.confirmed_generation, row.desired_generation) == (1, 2)
        await host.publish_once()
        assert (
            await js.stream_info(harness.settings.nats_work_stream)
        ).state.messages == 1
        assert (await harness.row(job.id)).confirmed_generation == 2


async def test_cancelled_cleanup_releases_scan_locks(harness: Harness) -> None:
    job_id = await historical_job(harness)
    entered = asyncio.Event()

    class PausedCleanup(BackgroundJobRepository):
        async def delete_retained(self, *, job_id: str, before: datetime) -> bool:
            entered.set()
            await asyncio.Future[None]()
            return False

    @asynccontextmanager
    async def paused() -> AsyncIterator[BackgroundJobRepository]:
        async with harness.sessions() as session, session.begin():
            yield PausedCleanup(session)

    host = harness.host()
    host._transaction = paused
    task = asyncio.create_task(host.cleanup_once())
    await asyncio.wait_for(entered.wait(), 3)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert await asyncio.wait_for(harness.host().cleanup_once(), 3) == 1
    async with harness.sessions() as session:
        assert await session.get(BackgroundJob, job_id) is None
