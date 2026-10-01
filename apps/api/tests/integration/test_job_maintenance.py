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
from bike_doc_api.maintenance.runtime import JobMaintenance
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
) -> None:
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
        async with nats_connection(harness.settings) as client:
            # Both acknowledgements survive the broker's persistent restart.
            assert (
                await jetstream(client).stream_info(harness.settings.nats_work_stream)
            ).state.messages == 2
    finally:
        await broker_action("start")
        await host.close()
