"""Shared worker on real PostgreSQL and pinned JetStream: task test:worker."""

import asyncio
import json
import os
import sys
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
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
from bike_doc_api.operations.monitoring import QueueMonitor
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
            await QueueMonitor(settings).subscribe(client)
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
    with caplog.at_level("INFO"):
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
    assert "terminated" in caplog.text and "max_deliver" in caplog.text
    for job in (poisoned, limited):
        row = await harness.row(job)
        assert row.state == "queued" and row.attempt_count == 0


async def test_observability_real_transport_execution_recovery_and_privacy(
    harness: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    from bike_doc_api.core.queue_telemetry import (
        RecordingQueueTelemetry,
        queue_telemetry,
    )
    from bike_doc_api.db.pool_observation import observe_pool
    from bike_doc_api.maintenance.runtime import JobMaintenance
    from bike_doc_api.operations.inspection import inspect_jobs
    from bike_doc_api.operations.monitoring import QueueMonitor
    from bike_doc_api.repositories.job_inspection import (
        InspectionFilter,
        JobInspectionRepository,
    )

    sentinel = "PRIVATE_SENTINEL_EMAIL_prompt_artifact_credentials"
    recorder = RecordingQueueTelemetry()
    monkeypatch.setattr(queue_telemetry, "_sink", recorder.observations.append)
    job = await harness.create(input={"private": sentinel})
    before = await harness.row(job)
    settings = harness.settings
    monitor = QueueMonitor(settings, recorder)
    await monitor.subscribe(harness.client)
    bind = harness.sessions.kw["bind"]
    observe_pool(bind, "worker", recorder)
    async with bind.connect(), bind.connect():
        assert any(
            o.metric == "pool"
            and o.attributes == {"role": "worker", "pool": "checked_out"}
            and o.value >= 2
            for o in recorder.observations
        )
    async with harness.sessions() as session:
        count, oldest = await JobInspectionRepository(session).publication_pressure()
    recorder.observe("publication_pending", count)
    recorder.observe("publication_oldest", oldest)
    assert count >= 1
    snapshot = await inspect_jobs(
        InspectionFilter(kind="profile_inference"), transport=True, settings=settings
    )
    assert sentinel not in json.dumps(snapshot, default=str)
    assert snapshot["jetstream_transport_observations"]["status"] == "available"
    assert (await harness.row(job)).input == before.input
    assert (await harness.row(job)).attempt_count == 0
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "bike_doc_api.operations.inspection",
        "--filters",
        '{"kind":"profile_inference","limit":200}',
        env={**os.environ, "BIKE_DOC_API_DATABASE_URL": settings.database_url},
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await asyncio.wait_for(process.communicate(), 15)
    assert process.returncode == 0
    assert sentinel.encode() not in stdout + stderr
    assert settings.database_url.encode() not in stdout + stderr
    assert json.loads(stdout)["postgresql_truth"]["jobs"]

    # Real redelivery exhaustion produces a monitoring-only advisory, without
    # resolving the intentionally invalid authoritative input or mutating a job.
    await harness.publish(job)
    transport_view = await monitor.snapshot()
    assert any(
        c["consumer"] == "profile_inference" and c["pending"] >= 1 and c["waiting"] == 0
        for c in transport_view["consumers"]
    )
    # Pending transport notifications with no recent worker heartbeat expose an
    # unavailable pool without changing PostgreSQL application state.
    assert not any(
        o.metric == "worker_health" and o.attributes["health"] == "ready"
        for o in recorder.observations
    )
    for _ in range(3):
        message = await harness.message()
        assert message.metadata.num_delivered >= 1
        assert sentinel not in repr((message.data, message.headers))
        await asyncio.sleep(0.35)
    with pytest.raises(TimeoutError):
        await harness.subscription.fetch(1, timeout=0.5)
    await asyncio.sleep(0.1)
    assert any(
        o.metric == "advisory" and o.attributes["advisory"] == "max_deliver"
        for o in recorder.observations
    )
    assert (await harness.row(job)).attempt_count == 0
    # A second physical notification can be terminated; this too is observation only.
    await harness.publish(job)
    await (await harness.message()).term()
    await asyncio.sleep(0.1)
    assert any(
        o.metric == "advisory" and o.attributes["advisory"] == "terminated"
        for o in recorder.observations
    )
    assert (await harness.row(job)).state == "queued"

    async def succeed(_job: ClaimedJob[ProfileInferenceInputV1]) -> JobOutcome:
        return JobOutcome("succeeded")

    executable = await harness.create()
    runtime = harness.worker(succeed)
    await harness.publish(executable)
    await runtime.process(NatsDelivery(await harness.message()))
    assert (await harness.row(executable)).state == "succeeded"
    assert {
        "queue_latency",
        "execution_duration",
        "active",
        "outcome",
        "resolution",
        "pool",
    } <= {o.metric for o in recorder.observations}

    # Leave another claim past its deadline and let maintenance recover it.
    expired = await harness.create()
    async with harness.sessions() as session, session.begin():
        await BackgroundJobRepository(session).resolve_delivery(
            job_id=expired.id,
            generation=1,
            workload_class="profile_inference",
            validate_definition=runtime.registry.validate,
        )
    await asyncio.sleep(4.5)

    @asynccontextmanager
    async def transaction() -> AsyncIterator[BackgroundJobRepository]:
        async with harness.sessions() as session, session.begin():
            yield BackgroundJobRepository(session)

    class NoPublisher:
        async def publish(self, subject: str, payload: bytes, message_id: str) -> None:
            raise AssertionError("recovery must only advance PostgreSQL intent")

        async def close(self) -> None:
            pass

    maintenance = JobMaintenance(
        settings,
        transaction,
        NoPublisher(),
        runtime.registry.reconciliation_policies(),
        telemetry=recorder,
    )
    await maintenance.reconcile_once()
    assert (await harness.row(expired)).state == "dead"
    assert any(
        o.metric == "recovery" and o.attributes["outcome"] == "dead"
        for o in recorder.observations
    )
    await monitor.snapshot()
    assert sentinel not in repr(recorder.observations)
    assert all("id" not in key for o in recorder.observations for key in o.attributes)


async def test_worker_first_version_rollout_and_producer_rollback(
    harness: Harness,
) -> None:
    """Synthetic versions exercise deployment ordering without adding product kinds."""
    calls: list[str] = []

    async def handle(job: ClaimedJob[ProfileInferenceInputV1]) -> JobOutcome:
        calls.append(job.id)
        return JobOutcome("succeeded")

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

    @asynccontextmanager
    async def host(versions: tuple[int, ...]) -> AsyncIterator[PullWorker]:
        async with nats_connection(harness.settings) as client:
            subscription = await jetstream(client).pull_subscribe(
                harness.settings.nats_profile_subject,
                durable=harness.settings.nats_profile_consumer,
                stream=harness.settings.nats_work_stream,
            )
            runtime = PullWorker(
                HandlerRegistry(
                    "profile_inference",
                    [
                        HandlerDefinition(
                            "rollout_probe",
                            version,
                            "profile_inference",
                            ProfileInferenceInputV1,
                            handle,
                            policy,
                        )
                        for version in versions
                    ],
                ),
                PostgresJobStore(harness.sessions),
                NatsPullTransport(subscription, client=client),
                RuntimeOptions(
                    harness.settings.nats_profile_subject,
                    ack_wait=0.3,
                    progress_interval=0.05,
                    fetch_timeout=0.1,
                ),
            )
            runtime.start()
            try:
                yield runtime
            finally:
                await runtime.close()

    async def completed(*jobs: JobSnapshot) -> None:
        for _ in range(300):
            rows = [await harness.row(job) for job in jobs]
            if all(row.state == "succeeded" for row in rows):
                assert all(row.attempt_count == 1 for row in rows)
                return
            await asyncio.sleep(0.01)
        pytest.fail("rollout work failed to reach durable completion")

    original = await harness.create(job_kind="rollout_probe")
    async with host((1,)) as old_worker:
        await harness.publish(original)
        await completed(original)
    assert not old_worker.healthy()
    # Old producers can leave v1 backlog before the worker upgrade barrier.
    backlog = await harness.create(job_kind="rollout_probe")
    async with host((1, 2)) as first, host((1, 2)) as second:
        for _ in range(300):
            if first.healthy() and second.healthy():
                break
            await asyncio.sleep(0.01)
        else:
            pytest.fail("compatible worker replicas did not become ready")
        for runtime in (first, second):
            assert set(runtime.registry.reconciliation_policies()) == {
                ("rollout_probe", 1),
                ("rollout_probe", 2),
            }
        # Only complete upgraded replicas are fetching when v2 producers enable.
        upgraded = await harness.create(job_kind="rollout_probe", input_version=2)
        await harness.publish(backlog)
        await harness.publish(upgraded)
        await completed(backlog, upgraded)
        # Roll back the producer while retaining compatible workers through all
        # possible v2 deliveries; never send accepted queue work to legacy code.
        rollback = await harness.create(job_kind="rollout_probe", input_version=1)
        await harness.publish(rollback)
        await harness.publish(upgraded)
        await completed(rollback)
        for _ in range(300):
            consumer = await harness.js.consumer_info(
                harness.settings.nats_work_stream,
                harness.settings.nats_profile_consumer,
            )
            if consumer.num_pending == consumer.num_ack_pending == 0:
                break
            await asyncio.sleep(0.01)
        else:
            pytest.fail("retained-version duplicate did not settle")
        await completed(original, backlog, upgraded, rollback)
        assert sorted(calls) == sorted(
            job.id for job in (original, backlog, upgraded, rollback)
        )
