"""Shared worker on real PostgreSQL and pinned JetStream: task test:worker."""

import asyncio
import json
import os
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from nats.aio.client import Client
from nats.aio.msg import Msg
from nats.js import JetStreamContext
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from bike_doc_api.core.config import Settings
from bike_doc_api.core.nats import ensure_work_topology, jetstream, nats_connection
from bike_doc_api.models._ids import generate_prefixed_ulid
from bike_doc_api.models.background_job import BackgroundJob
from bike_doc_api.repositories.background_jobs import (
    BackgroundJobRepository,
    JobError,
    JobOutcome,
    JobRecord,
    JobSnapshot,
)
from bike_doc_api.schemas.background_jobs import ProfileInferenceInputV1
from bike_doc_api.workers.adapters import (
    NatsDelivery,
    NatsPullTransport,
    PostgresJobStore,
    subscribe_work_advisories,
)
from bike_doc_api.workers.registry import (
    ClaimedJob,
    EffectPolicy,
    HandlerDefinition,
    HandlerPolicy,
    HandlerRegistry,
)
from bike_doc_api.workers.runtime import PullWorker, RuntimeOptions

DB_URL = os.getenv("BIKE_DOC_API_MAINTENANCE_TEST_DATABASE_URL")
NATS_URL = os.getenv("BIKE_DOC_API_MAINTENANCE_TEST_NATS_URL")
pytestmark = [
    pytest.mark.nats,
    pytest.mark.skipif(
        not DB_URL or not NATS_URL,
        reason="requires disposable migrated PostgreSQL and pinned JetStream",
    ),
]


class Recovery:
    def expired_outcome(self, job: JobSnapshot) -> JobOutcome | None:
        return JobOutcome("dead", JobError.EXECUTION_LOST)

    def may_republish(self, job: JobSnapshot) -> bool:
        return True


class Harness:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        settings: Settings,
        client: Client,
        js: JetStreamContext,
        subscription: JetStreamContext.PullSubscription,
    ) -> None:
        self.sessions = sessions
        self.settings = settings
        self.client = client
        self.js = js
        self.subscription = subscription
        self.jobs = []
        self.workers = []

    async def create(self, **values: object) -> JobSnapshot:
        input = ProfileInferenceInputV1(
            turn_id=generate_prefixed_ulid("turn_"),
            inference_schema_version="bike_profile_inference.v1",
            extractor_version="worker.v1",
        )
        async with self.sessions() as session, session.begin():
            job = await BackgroundJobRepository(session).record(
                JobRecord(
                    values.pop("job_kind", "profile_inference"),
                    values.pop("workload_class", "profile_inference"),
                    values.pop("input_version", 1),
                    values.pop("input", input.model_dump(mode="json")),
                    input.deduplication_key(),
                    3,
                )
            )
            self.jobs.append(job.id)
            if "terminal_at" in values:
                values["terminal_at"] = datetime.now(UTC)
            if values:
                await session.execute(
                    update(BackgroundJob)
                    .where(BackgroundJob.id == job.id)
                    .values(**values)
                )
        return job

    async def row(self, job: JobSnapshot) -> BackgroundJob:
        async with self.sessions() as session:
            return (
                await session.scalars(
                    select(BackgroundJob).where(BackgroundJob.id == job.id)
                )
            ).one()

    async def publish(
        self, job: JobSnapshot, generation: int = 1, **extra: object
    ) -> None:
        await self.js.publish(
            self.settings.nats_profile_subject,
            json.dumps(
                {
                    "version": 1,
                    "job_id": job.id,
                    "publication_generation": generation,
                    **extra,
                }
            ).encode(),
        )

    async def message(self) -> Msg:
        return (await self.subscription.fetch(1, timeout=3))[0]

    def worker(
        self,
        handler: Callable[[ClaimedJob[ProfileInferenceInputV1]], Awaitable[JobOutcome]],
        **options: object,
    ) -> PullWorker:
        policy = HandlerPolicy(
            3,
            timedelta(seconds=2),
            timedelta(seconds=2),
            timedelta(seconds=0.2),
            timedelta(seconds=0.2),
            JobOutcome("dead", JobError.EXECUTION_TIMEOUT),
            EffectPolicy.IDEMPOTENT,
            Recovery(),
        )
        registry = HandlerRegistry(
            "profile_inference",
            [
                HandlerDefinition(
                    "profile_inference",
                    1,
                    "profile_inference",
                    ProfileInferenceInputV1,
                    handler,
                    policy,
                )
            ],
        )
        runtime = PullWorker(
            registry,
            PostgresJobStore(self.sessions),
            NatsPullTransport(self.subscription, client=self.client),
            RuntimeOptions(
                self.settings.nats_profile_subject,
                ack_wait=0.3,
                progress_interval=0.05,
                fetch_timeout=0.1,
                **options,
            ),
        )
        self.workers.append(runtime)
        return runtime


@pytest_asyncio.fixture
async def harness() -> AsyncIterator[Harness]:
    assert DB_URL and NATS_URL
    suffix = uuid.uuid4().hex[:12]
    settings = Settings(
        environment="test",
        database_url=DB_URL,
        nats_url=NATS_URL,
        nats_work_stream=f"WORKER_{suffix}",
        nats_diagnostic_subject=f"worker.{suffix}.diagnostic",
        nats_profile_subject=f"worker.{suffix}.profile",
        nats_diagnostic_consumer=f"diagnostic_{suffix}",
        nats_profile_consumer=f"profile_{suffix}",
    )
    engine = create_async_engine(DB_URL)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with nats_connection(settings) as client:
            js = jetstream(client)
            await ensure_work_topology(js, settings, ack_wait=0.3, max_deliver=3)
            sub = await js.pull_subscribe(
                settings.nats_profile_subject,
                durable=settings.nats_profile_consumer,
                stream=settings.nats_work_stream,
            )
            await subscribe_work_advisories(
                client, settings.nats_work_stream, settings.nats_profile_consumer
            )
            harness = Harness(sessions, settings, client, js, sub)
            try:
                yield harness
            finally:
                # Tests which close a runtime already drained this process connection.
                for runtime in harness.workers:
                    if not runtime._closed:
                        await runtime.close()
        async with nats_connection(settings) as cleanup_client:
            await jetstream(cleanup_client).delete_stream(settings.nats_work_stream)
        async with sessions() as session, session.begin():
            await session.execute(
                delete(BackgroundJob).where(BackgroundJob.id.in_(harness.jobs))
            )
    finally:
        await engine.dispose()


async def succeed(job: ClaimedJob[ProfileInferenceInputV1]) -> JobOutcome:
    assert isinstance(job.input, ProfileInferenceInputV1)
    return JobOutcome("succeeded")


async def test_real_pull_progress_duplicate_claim_ack_and_drain(
    harness: Harness,
) -> None:
    job = await harness.create()
    started, release = asyncio.Event(), asyncio.Event()
    calls = []

    async def handle(claim: ClaimedJob[ProfileInferenceInputV1]) -> JobOutcome:
        calls.append(claim)
        started.set()
        await release.wait()
        return JobOutcome("succeeded")

    runtime = harness.worker(handle)
    await harness.publish(job)
    first = await harness.message()
    work = asyncio.create_task(runtime.process(NatsDelivery(first)))
    await asyncio.wait_for(started.wait(), 3)
    deadline = (await harness.row(job)).execution_deadline
    await asyncio.sleep(0.5)  # Exceeds AckWait; progress keeps delivery live.
    assert (await harness.row(job)).execution_deadline == deadline
    with pytest.raises(TimeoutError):
        await harness.subscription.fetch(1, timeout=0.1)
    await harness.publish(job)
    duplicate = await harness.message()
    await runtime.process(NatsDelivery(duplicate))
    assert len(calls) == 1 and (await harness.row(job)).attempt_count == 1
    with pytest.raises(TimeoutError):
        await harness.subscription.fetch(1, timeout=0.15)
    release.set()
    await work
    row = await harness.row(job)
    assert row.state == "succeeded" and row.attempt_count == 1
    # The duplicate's deadline NAK remains available for terminal no-op.
    redelivery = await harness.message()
    assert redelivery.metadata.num_delivered == 2
    await runtime.process(NatsDelivery(redelivery))
    assert len(calls) == 1
    assert (
        await harness.js.consumer_info(
            harness.settings.nats_work_stream, harness.settings.nats_profile_consumer
        )
    ).num_ack_pending == 0
    await runtime.close()
    assert harness.client.is_closed


async def test_retry_commits_eligibility_and_early_delivery_cannot_execute(
    harness: Harness,
) -> None:
    job = await harness.create()
    calls = []

    async def handle(claim: ClaimedJob[ProfileInferenceInputV1]) -> JobOutcome:
        calls.append(claim)
        if len(calls) == 1:
            return JobOutcome(
                "retrying", JobError.PROVIDER_UNAVAILABLE, timedelta(seconds=0.5)
            )
        return JobOutcome("succeeded")

    runtime = harness.worker(handle)
    await harness.publish(job)
    await runtime.process(NatsDelivery(await harness.message()))
    row = await harness.row(job)
    assert row.state == "retrying" and row.eligible_at > datetime.now(UTC)
    await harness.publish(job)
    await runtime.process(NatsDelivery(await harness.message()))
    assert len(calls) == 1 and (await harness.row(job)).attempt_count == 1
    with pytest.raises(TimeoutError):
        await harness.subscription.fetch(1, timeout=0.15)
    await runtime.process(NatsDelivery(await harness.message()))
    await runtime.process(NatsDelivery(await harness.message()))
    assert (await harness.row(job)).state == "succeeded" and len(calls) == 2


@pytest.mark.parametrize(
    "case",
    [
        "terminal",
        "stale",
        "future",
        "wrong_workload",
        "wrong_subject",
        "malformed",
        "missing",
        "unknown",
        "version",
        "input",
    ],
)
async def test_real_fail_closed_and_attempt_free_branches(
    harness: Harness, case: str
) -> None:
    values = {}
    if case == "terminal":
        values = {"state": "dead", "terminal_at": datetime.now(UTC)}
    elif case == "stale":
        values = {"desired_generation": 2}
    elif case == "wrong_workload":
        values = {"workload_class": "diagnostic"}
    elif case == "unknown":
        values = {"job_kind": "unregistered"}
    elif case == "version":
        values = {"input_version": 2}
    elif case == "input":
        values = {"input": {"turn_id": "bad", "secret": "forbidden"}}
    job = await harness.create(**values)
    calls = []

    async def handle(claim: ClaimedJob[ProfileInferenceInputV1]) -> JobOutcome:
        calls.append(claim)
        return JobOutcome("succeeded")

    runtime = harness.worker(handle)
    published = (
        replace(job, id=generate_prefixed_ulid("job_")) if case == "missing" else job
    )
    await harness.publish(
        published,
        generation=2 if case == "future" else 1,
        **({"input": "prohibited"} if case == "malformed" else {}),
    )
    if case == "wrong_subject":
        # A real delivery from the other workload must not reach PostgreSQL resolution.
        other = await harness.js.pull_subscribe(
            harness.settings.nats_diagnostic_subject,
            durable=harness.settings.nats_diagnostic_consumer,
            stream=harness.settings.nats_work_stream,
        )
        await harness.js.publish(
            harness.settings.nats_diagnostic_subject,
            json.dumps(
                {"version": 1, "job_id": job.id, "publication_generation": 1}
            ).encode(),
        )
        await runtime.process(NatsDelivery((await other.fetch(1, timeout=3))[0]))
        await other.unsubscribe()
        # Clear the profile notification as an explicit untrusted-subject wrapper too.
        message = await harness.message()
        message.subject = harness.settings.nats_diagnostic_subject
        await runtime.process(NatsDelivery(message))
    else:
        await runtime.process(NatsDelivery(await harness.message()))
    row = await harness.row(job)
    assert not calls and row.attempt_count == 0
    assert row.state == (
        "dead" if case in {"terminal", "unknown", "version", "input"} else "queued"
    )
    assert (
        await harness.js.consumer_info(
            harness.settings.nats_work_stream, harness.settings.nats_profile_consumer
        )
    ).num_ack_pending == 0


async def test_commit_then_ack_failure_redelivers_safely(harness: Harness) -> None:
    job = await harness.create()
    calls = []

    async def handle(claim: ClaimedJob[ProfileInferenceInputV1]) -> JobOutcome:
        calls.append(claim)
        return JobOutcome("succeeded")

    class LostAck(NatsDelivery):
        async def ack(self) -> None:
            # Drop the ack before sending; the durable outcome must survive.
            raise TimeoutError()

    runtime = harness.worker(handle)
    await harness.publish(job)
    await runtime.process(LostAck(await harness.message()))
    assert (await harness.row(job)).state == "succeeded"
    redelivery = await harness.message()
    assert redelivery.metadata.num_delivered == 2
    await runtime.process(NatsDelivery(redelivery))
    assert len(calls) == 1 and (await harness.row(job)).attempt_count == 1


async def test_timeout_finishes_before_hard_deadline(harness: Harness) -> None:
    job = await harness.create()
    calls = []

    async def slow(claim: ClaimedJob[ProfileInferenceInputV1]) -> JobOutcome:
        calls.append(claim)
        await asyncio.Future()

    runtime = harness.worker(slow)
    await harness.publish(job)
    await runtime.process(NatsDelivery(await harness.message()))
    row = await harness.row(job)
    assert row.state == "dead" and row.latest_error_category == "execution_timeout"
    assert datetime.now(UTC) < calls[0].execution_deadline


@pytest.mark.parametrize("graceful", [True, False])
async def test_shutdown_drain_and_forced_loss_redelivery(
    harness: Harness, graceful: bool
) -> None:
    job = await harness.create()
    started = asyncio.Event()

    async def slow(claim: ClaimedJob[ProfileInferenceInputV1]) -> JobOutcome:
        started.set()
        await asyncio.sleep(0.1 if graceful else 10)
        return JobOutcome("succeeded")

    runtime = harness.worker(slow, shutdown_timeout=0.2)
    await harness.publish(job)
    runtime.start()
    await asyncio.wait_for(started.wait(), 3)
    await runtime.close()
    assert harness.client.is_closed
    row = await harness.row(job)
    assert row.state == ("succeeded" if graceful else "running")
    if not graceful:
        async with nats_connection(harness.settings) as client:
            sub = await jetstream(client).pull_subscribe(
                harness.settings.nats_profile_subject,
                durable=harness.settings.nats_profile_consumer,
                stream=harness.settings.nats_work_stream,
            )
            redelivery = (await sub.fetch(1, timeout=3))[0]
            assert redelivery.metadata.num_delivered == 2
            assert (await harness.row(job)).attempt_count == 1


async def test_simultaneous_claims_in_independent_transactions_invoke_once(
    harness: Harness,
) -> None:
    job = await harness.create()
    entered, release = asyncio.Event(), asyncio.Event()
    calls = []

    async def handle(claim: ClaimedJob[ProfileInferenceInputV1]) -> JobOutcome:
        calls.append(claim)
        entered.set()
        await release.wait()
        return JobOutcome("succeeded")

    runtime = harness.worker(handle)
    await harness.publish(job)
    await harness.publish(job)
    first, second = await harness.subscription.fetch(2, timeout=3)
    one = asyncio.create_task(runtime.process(NatsDelivery(first)))
    two = asyncio.create_task(runtime.process(NatsDelivery(second)))
    await entered.wait()
    # One task must finish duplicate settlement while the handler remains blocked.
    done, pending = await asyncio.wait(
        {one, two}, timeout=1, return_when=asyncio.FIRST_COMPLETED
    )
    assert len(done) == len(pending) == 1
    assert len(calls) == 1 and (await harness.row(job)).attempt_count == 1
    release.set()
    await asyncio.gather(one, two)
    assert (await harness.row(job)).state == "succeeded"


async def test_real_advisories_do_not_mutate_durable_job_state(
    harness: Harness, caplog: pytest.LogCaptureFixture
) -> None:
    runtime = harness.worker(succeed)
    poisoned = await harness.create()
    with caplog.at_level("WARNING"):
        await harness.publish(poisoned, extra="not permitted")
        await runtime.process(NatsDelivery(await harness.message()))
        limited = await harness.create()
        await harness.publish(limited)
        for expected in range(1, 4):
            message = await harness.message()
            assert message.metadata.num_delivered == expected
        with pytest.raises(TimeoutError):
            await harness.subscription.fetch(1, timeout=0.5)
        await harness.client.flush()
        await asyncio.sleep(0.05)
    assert "terminated" in caplog.text and "maximum_delivery" in caplog.text
    for job in (poisoned, limited):
        row = await harness.row(job)
        assert row.state == "queued" and row.attempt_count == 0
