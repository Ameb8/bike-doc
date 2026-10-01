"""Registry, timing, lifecycle and every settlement branch without infrastructure."""

import asyncio
import json
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import BaseModel, ConfigDict, ValidationError

from bike_doc_api.models._ids import generate_prefixed_ulid
from bike_doc_api.repositories.background_jobs import (
    DeliveryResolution,
    ExecutionPolicy,
    JobError,
    JobOutcome,
    JobSnapshot,
    ResolutionKind,
)
from bike_doc_api.workers.registry import (
    ClaimedJob,
    DeliveryEnvelope,
    EffectPolicy,
    HandlerDefinition,
    HandlerPolicy,
    HandlerRegistry,
)
from bike_doc_api.workers.runtime import PullWorker, RuntimeOptions


class Input(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)
    value: int


class Recovery:
    def expired_outcome(self, job: JobSnapshot) -> JobOutcome | None:
        return JobOutcome("dead", JobError.EXECUTION_LOST)

    def may_republish(self, job: JobSnapshot) -> bool:
        return True


def policy(**changes: object) -> HandlerPolicy:
    return replace(
        HandlerPolicy(
            3,
            timedelta(seconds=10),
            timedelta(seconds=1),
            timedelta(seconds=0.1),
            timedelta(seconds=0.1),
            JobOutcome("dead", JobError.EXECUTION_TIMEOUT),
            EffectPolicy.IDEMPOTENT,
            Recovery(),
        ),
        **changes,
    )


def snapshot(**changes: object) -> JobSnapshot:
    return replace(
        JobSnapshot(
            generate_prefixed_ulid("job_"),
            "test",
            "profile_inference",
            1,
            {"value": 1},
            "running",
            1,
            3,
            1,
            1,
            datetime.now(UTC),
            "token",
            datetime.now(UTC) + timedelta(seconds=1),
            None,
            None,
        ),
        **changes,
    )


class FakeClock:
    def __init__(self) -> None:
        self.time = datetime.now(UTC)

    def now(self) -> datetime:
        return self.time

    async def sleep(self, seconds: float) -> None:
        await asyncio.sleep(seconds)


class FakeDelivery:
    def __init__(
        self,
        job: JobSnapshot | None = None,
        *,
        data: bytes | None = None,
        subject: str = "work.profile",
        events: list[object] | None = None,
        ack_failure: bool = False,
    ) -> None:
        self.subject = subject
        self.data = (
            data
            if data is not None
            else json.dumps(
                {
                    "version": 1,
                    "job_id": (job or snapshot()).id,
                    "publication_generation": 1,
                }
            ).encode()
        )
        self.events = events if events is not None else []
        self.ack_failure = ack_failure

    async def ack(self) -> None:
        self.events.append("ack")
        if self.ack_failure:
            raise TimeoutError()

    async def nak(self, delay: float) -> None:
        self.events.append(("nak", delay))

    async def terminate(self) -> None:
        self.events.append("term")

    async def progress(self) -> None:
        self.events.append("progress")


class FakeStore:
    def __init__(self, resolution: DeliveryResolution, events: list[object]) -> None:
        self.resolution = resolution
        self.events = events
        self.calls = 0
        self.finish_result = True
        self.failure = False

    async def resolve(
        self,
        envelope: DeliveryEnvelope,
        workload: str,
        validate: Callable[[JobSnapshot], ExecutionPolicy | JobError],
    ) -> DeliveryResolution:
        self.calls += 1
        self.events.append("resolve_commit")
        return self.resolution

    async def finish(self, job: JobSnapshot, outcome: JobOutcome) -> JobSnapshot | None:
        if self.failure:
            raise ConnectionError()
        self.events.append(("outcome_commit", outcome.state))
        if not self.finish_result:
            return None
        state = outcome.state
        if state == "retrying" and job.attempt_count >= job.attempt_limit:
            state = "dead"
        settled = replace(
            job,
            state=state,
            eligible_at=datetime.now(UTC) + (outcome.retry_after or timedelta()),
        )
        self.resolution = DeliveryResolution(ResolutionKind.TERMINAL, settled)
        return settled


class FakeTransport:
    def __init__(self, messages: Sequence[FakeDelivery] = ()) -> None:
        self.messages = list(messages)
        self.batches = []
        self.closed = False

    async def fetch(self, batch: int, wait_seconds: float) -> Sequence[FakeDelivery]:
        self.batches.append(batch)
        if not self.messages:
            await asyncio.sleep(wait_seconds)
        messages, self.messages = self.messages[:batch], self.messages[batch:]
        return messages

    async def close(self) -> None:
        self.closed = True


def worker(
    resolution: DeliveryResolution,
    handler: Callable[[ClaimedJob[Input]], Awaitable[JobOutcome]] | None = None,
    handler_policy: HandlerPolicy | None = None,
    **options: object,
) -> tuple[PullWorker, FakeStore, list[object]]:
    async def succeed(job: ClaimedJob[Input]) -> JobOutcome:
        return JobOutcome("succeeded")

    definition = HandlerDefinition(
        "test",
        1,
        "profile_inference",
        Input,
        handler or succeed,
        handler_policy or policy(),
    )
    events = []
    store = FakeStore(resolution, events)
    runtime = PullWorker(
        HandlerRegistry("profile_inference", [definition]),
        store,
        FakeTransport(),
        RuntimeOptions("work.profile", **options),
    )
    return runtime, store, events


@pytest.mark.parametrize(
    "change",
    [
        {"version": 2},
        {"version": True},
        {"version": "1"},
        {"version": 1.0},
        {"job_id": "user@example.com"},
        {"job_id": "job_bad"},
        {"job_id": None},
        {"publication_generation": 0},
        {"publication_generation": -1},
        {"publication_generation": True},
        {"publication_generation": "1"},
        {"publication_generation": 1.1},
        {"publication_generation": 2**63},
        {"input": {"secret": "no"}},
        {"kind": "os.system"},
    ],
)
def test_strict_envelope(change: dict[str, object]) -> None:
    valid = {"version": 1, "job_id": snapshot().id, "publication_generation": 1}
    DeliveryEnvelope.model_validate(valid)
    with pytest.raises(ValidationError):
        DeliveryEnvelope.model_validate(valid | change)


def test_registry_exact_selection_and_input_validation() -> None:
    runtime, _, _ = worker(DeliveryResolution(ResolutionKind.EXECUTABLE, snapshot()))
    registry = runtime.registry
    assert registry.validate(snapshot(job_kind="other")) == JobError.DEFINITION_UNKNOWN
    assert registry.validate(snapshot(input_version=2)) == JobError.VERSION_UNSUPPORTED
    assert registry.validate(snapshot(input={"value": "1"})) == JobError.INPUT_INVALID
    assert (
        registry.validate(snapshot(input={"value": 1, "secret": "x"}))
        == JobError.INPUT_INVALID
    )
    assert registry.validate(snapshot(attempt_limit=4)) == JobError.PERMANENT_FAILURE
    assert (
        registry.validate(snapshot(workload_class="diagnostic"))
        == JobError.PERMANENT_FAILURE
    )
    assert registry.reconciliation_policies()[("test", 1)].may_republish(snapshot())
    definition = registry.select(snapshot())
    with pytest.raises(ValueError):
        HandlerRegistry("profile_inference", [definition, definition])
    with pytest.raises(ValueError):
        HandlerRegistry("diagnostic", [definition])
    with pytest.raises(ValueError):
        HandlerRegistry("profile_inference", [])
    with pytest.raises(ValueError):
        replace(definition, policy=None)

    class Loose(BaseModel):
        value: int

    with pytest.raises(ValueError):
        replace(definition, input_model=Loose)


@pytest.mark.parametrize(
    "change",
    [
        {"attempt_limit": 0},
        {"attempt_limit": True},
        {"reconciliation": None},
        {"effect": None},
        {"timeout_outcome": None},
        {"timeout_grace": timedelta(seconds=1)},
        {"settlement_reserve": timedelta(0)},
        {"hard_duration": timedelta(days=8)},
        {"max_retry_delay": timedelta(0)},
    ],
)
def test_policy_fails_closed(change: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        policy(**change)


@pytest.mark.parametrize(
    "change",
    [
        {"fetch_batch": 5},
        {"concurrency": 129},
        {"progress_interval": 15},
        {"fetch_timeout": float("nan")},
        {"subject": "work.*"},
        {"shutdown_timeout": 0},
    ],
)
def test_runtime_timing_fails_closed(change: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        RuntimeOptions(**({"subject": "work.profile"} | change))


@pytest.mark.parametrize(
    "kind,expected",
    [
        (ResolutionKind.TERMINAL, "ack"),
        (ResolutionKind.STALE, "ack"),
        (ResolutionKind.EXHAUSTED, "ack"),
        (ResolutionKind.MISSING, "term"),
        (ResolutionKind.PROHIBITED, "term"),
        (ResolutionKind.DEFINITION_FAILURE, "term"),
        (ResolutionKind.EARLY, "nak"),
        (ResolutionKind.RUNNING, "nak"),
        (ResolutionKind.RECOVERY_WAIT, "nak"),
    ],
)
async def test_settlement_matrix(kind: ResolutionKind, expected: str) -> None:
    now = datetime.now(UTC)
    runtime, store, events = worker(
        DeliveryResolution(kind, snapshot(), now + timedelta(seconds=2))
    )
    runtime.clock = FakeClock()
    runtime.clock.time = now
    await runtime.process(FakeDelivery(events=events))
    assert events[0] == "resolve_commit"
    if expected == "nak":
        assert events[1] == ("nak", 5 if kind == ResolutionKind.RECOVERY_WAIT else 2)
    else:
        assert events[1] == expected
    assert len(events) == 2 and store.calls == 1


@pytest.mark.parametrize(
    "data,subject",
    [
        (b"{}", "work.profile"),
        (b"not json", "work.profile"),
        (b"x" * 257, "work.profile"),
        (b"{}", "wrong.subject"),
    ],
)
async def test_untrusted_delivery_never_touches_store(
    data: bytes, subject: str
) -> None:
    runtime, store, events = worker(
        DeliveryResolution(ResolutionKind.EXECUTABLE, snapshot())
    )
    await runtime.process(FakeDelivery(data=data, subject=subject, events=events))
    assert events == ["term"] and store.calls == 0


@pytest.mark.parametrize(
    "outcome",
    [
        JobOutcome("succeeded"),
        JobOutcome("interrupted", JobError.EXECUTION_LOST),
        JobOutcome("dead", JobError.PERMANENT_FAILURE),
        JobOutcome("retrying", JobError.PROVIDER_UNAVAILABLE, timedelta(seconds=2)),
    ],
)
async def test_typed_handler_outcomes_commit_before_settlement(
    outcome: JobOutcome,
) -> None:
    calls = []

    async def handle(job: ClaimedJob[Input]) -> JobOutcome | None:
        assert isinstance(job.input, Input) and job.input.value == 1
        assert job.execution_token == "token" and job.attempt == 1
        assert not hasattr(job, "ack")
        calls.append(job)
        return outcome

    runtime, _, events = worker(
        DeliveryResolution(ResolutionKind.EXECUTABLE, snapshot()), handle
    )
    await runtime.process(FakeDelivery(events=events))
    assert len(calls) == 1
    assert events[:2] == ["resolve_commit", ("outcome_commit", outcome.state)]
    assert events[2][0] == "nak" if outcome.state == "retrying" else events[2] == "ack"


async def test_exhaustion_acks_actual_committed_dead_state() -> None:
    async def retry(job: ClaimedJob[Input]) -> JobOutcome:
        return JobOutcome(
            "retrying", JobError.PROVIDER_UNAVAILABLE, timedelta(seconds=2)
        )

    runtime, _, events = worker(
        DeliveryResolution(ResolutionKind.EXECUTABLE, snapshot(attempt_count=3)), retry
    )
    await runtime.process(FakeDelivery(events=events))
    assert events[-1] == "ack"


async def test_ack_failure_redelivery_is_attempt_free_noop() -> None:
    calls = []

    async def handle(job: ClaimedJob[Input]) -> JobOutcome | None:
        calls.append(job)
        return JobOutcome("succeeded")

    runtime, _, events = worker(
        DeliveryResolution(ResolutionKind.EXECUTABLE, snapshot()), handle
    )
    await runtime.process(FakeDelivery(events=events, ack_failure=True))
    await runtime.process(FakeDelivery(events=events))
    assert len(calls) == 1 and events[-1] == "ack"


@pytest.mark.parametrize(
    "mode", ["commit_failure", "stale_token", "exception", "bad_retry", "bad_result"]
)
async def test_unsafe_failures_remain_unsettled(mode: str) -> None:
    async def handle(job: ClaimedJob[Input]) -> JobOutcome | None:
        if mode == "exception":
            raise RuntimeError("private content")
        if mode == "bad_retry":
            return JobOutcome(
                "retrying", JobError.PROVIDER_UNAVAILABLE, timedelta(seconds=11)
            )
        if mode == "bad_result":
            return None
        return JobOutcome("succeeded")

    runtime, store, events = worker(
        DeliveryResolution(ResolutionKind.EXECUTABLE, snapshot()), handle
    )
    store.failure = mode == "commit_failure"
    store.finish_result = mode != "stale_token"
    await runtime.process(FakeDelivery(events=events))
    assert "ack" not in events and "term" not in events
    assert not any(isinstance(event, tuple) and event[0] == "nak" for event in events)


async def test_timeout_progress_never_renews_deadline() -> None:
    cancelled = asyncio.Event()

    async def slow(job: ClaimedJob[Input]) -> JobOutcome:
        try:
            await asyncio.Future()
        finally:
            cancelled.set()

    job = snapshot()
    runtime, _, events = worker(
        DeliveryResolution(ResolutionKind.EXECUTABLE, job),
        slow,
        ack_wait=0.2,
        progress_interval=0.05,
    )
    await runtime.process(FakeDelivery(events=events))
    assert cancelled.is_set() and events.count("progress") > 3
    assert events[-2:] == [("outcome_commit", "dead"), "ack"]
    assert datetime.now(UTC) < job.execution_deadline


async def test_cancellation_resistant_handler_is_bounded_and_unsettled() -> None:
    release = asyncio.Event()

    async def resistant(job: ClaimedJob[Input]) -> JobOutcome:
        while not release.is_set():
            try:
                await release.wait()
            except asyncio.CancelledError:
                continue
        return JobOutcome("succeeded")

    job = snapshot()
    runtime, _, events = worker(
        DeliveryResolution(ResolutionKind.EXECUTABLE, job), resistant
    )
    await runtime.process(FakeDelivery(events=events))
    assert datetime.now(UTC) < job.execution_deadline
    assert events == ["resolve_commit"]
    release.set()
    await asyncio.gather(*runtime._handlers)


async def test_forced_cancellation_leaves_claim_unsettled() -> None:
    started = asyncio.Event()

    async def slow(job: ClaimedJob[Input]) -> JobOutcome:
        started.set()
        await asyncio.Future()

    runtime, _, events = worker(
        DeliveryResolution(ResolutionKind.EXECUTABLE, snapshot()), slow
    )
    task = asyncio.create_task(runtime.process(FakeDelivery(events=events)))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert events == ["resolve_commit"]


@pytest.mark.parametrize("graceful", [True, False])
async def test_shutdown_stops_fetching_bounds_concurrency_and_drains(
    graceful: bool,
) -> None:
    started = asyncio.Event()
    calls = 0
    active = 0
    maximum = 0

    async def handle(job: ClaimedJob[Input]) -> JobOutcome | None:
        nonlocal calls, active, maximum
        calls += 1
        active += 1
        maximum = max(maximum, active)
        started.set()
        try:
            await asyncio.sleep(0.04 if graceful else 10)
            return JobOutcome("succeeded")
        finally:
            active -= 1

    runtime, _, events = worker(
        DeliveryResolution(ResolutionKind.EXECUTABLE, snapshot()),
        handle,
        concurrency=2,
        fetch_batch=2,
        shutdown_timeout=0.1,
    )
    transport = FakeTransport([FakeDelivery(events=events) for _ in range(5)])
    runtime.transport = transport
    runtime.start()
    await started.wait()
    await runtime.close()
    assert maximum == 2 and calls == 2 and transport.closed
    assert transport.batches == [2]
    assert events.count("ack") == (2 if graceful else 0)
    await runtime.close()


@pytest.mark.parametrize(
    "data",
    [
        b"[]",
        b"null",
        b'{"version":1,"version":2}',
        b'{"job_id":"x","job_id":"y"}',
        b"\xff",
    ],
)
def test_envelope_decode_rejects_ambiguous_or_non_object_json(data: bytes) -> None:
    with pytest.raises((ValueError, UnicodeError)):
        DeliveryEnvelope.decode(data)


async def test_timeout_budgets_with_deterministic_clock_and_wait(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    job = snapshot()
    clock = FakeClock()
    clock.time = job.execution_deadline - timedelta(seconds=1)
    waits = []

    async def fake_wait(
        tasks: set[asyncio.Task[JobOutcome]], **kwargs: float
    ) -> tuple[set[asyncio.Task[JobOutcome]], set[asyncio.Task[JobOutcome]]]:
        timeout = kwargs["timeout"]
        waits.append(timeout)
        clock.time += timedelta(seconds=timeout)
        await asyncio.sleep(0)
        done = {task for task in tasks if task.done()}
        return done, set(tasks) - done

    async def slow(claim: ClaimedJob[Input]) -> JobOutcome:
        await asyncio.Future()

    runtime, _, events = worker(
        DeliveryResolution(ResolutionKind.EXECUTABLE, job), slow
    )
    runtime.clock = clock
    monkeypatch.setattr("bike_doc_api.workers.runtime.asyncio.wait", fake_wait)
    await runtime.process(FakeDelivery(events=events))
    assert waits == pytest.approx([0.8, 0.1])
    assert clock.now() < job.execution_deadline
    assert events[-2:] == [("outcome_commit", "dead"), "ack"]


async def test_bounded_shutdown_with_resistant_fetch() -> None:
    release = asyncio.Event()
    entered = asyncio.Event()

    class ResistantTransport(FakeTransport):
        async def fetch(
            self, batch: int, wait_seconds: float
        ) -> Sequence[FakeDelivery]:
            entered.set()
            while not release.is_set():
                try:
                    await release.wait()
                except asyncio.CancelledError:
                    continue
            return [FakeDelivery()]

    runtime, store, _ = worker(
        DeliveryResolution(ResolutionKind.EXECUTABLE, snapshot()),
        cancellation_grace=0.01,
    )
    transport = ResistantTransport()
    runtime.transport = transport
    runtime.start()
    await entered.wait()
    await runtime.close()
    assert transport.closed and store.calls == 0
    release.set()
    await runtime._fetcher
    assert store.calls == 0


async def test_role_configuration_and_reconciliation_seam() -> None:
    from bike_doc_api.core.config import Settings
    from bike_doc_api.workers.role import create_workload_worker

    runtime, store, _ = worker(DeliveryResolution(ResolutionKind.TERMINAL, snapshot()))
    configured = create_workload_worker(
        Settings(), runtime.registry, store, FakeTransport()
    )
    assert configured.options.subject == Settings().nats_profile_subject
    with pytest.raises(ValueError):
        create_workload_worker(
            Settings(worker_concurrency=1, worker_fetch_batch=4),
            runtime.registry,
            store,
            FakeTransport(),
        )


async def test_advisories_observe_fixed_categories_only(
    caplog: pytest.LogCaptureFixture,
) -> None:
    from bike_doc_api.workers.adapters import subscribe_work_advisories

    callbacks = {}

    class Client:
        async def subscribe(
            self, subject: str, cb: Callable[[object], Awaitable[None]]
        ) -> None:
            callbacks[subject] = cb

    await subscribe_work_advisories(Client(), "WORK", "profile")
    with caplog.at_level("WARNING"):
        for callback in callbacks.values():
            await callback(object())  # No message fields or database needed.
    assert len(callbacks) == 2
    assert "maximum_delivery" in caplog.text and "terminated" in caplog.text


async def test_resistant_handlers_retain_local_capacity_after_deadline() -> None:
    release = asyncio.Event()
    started = asyncio.Event()
    calls = 0

    async def resistant(job: ClaimedJob[Input]) -> JobOutcome:
        nonlocal calls
        calls += 1
        started.set()
        while not release.is_set():
            try:
                await release.wait()
            except asyncio.CancelledError:
                continue
        return JobOutcome("succeeded")

    job = snapshot(execution_deadline=datetime.now(UTC) + timedelta(seconds=0.1))
    timing = policy(
        hard_duration=timedelta(seconds=0.1),
        timeout_grace=timedelta(seconds=0.02),
        settlement_reserve=timedelta(seconds=0.02),
    )
    runtime, _, events = worker(
        DeliveryResolution(ResolutionKind.EXECUTABLE, job),
        resistant,
        timing,
        concurrency=1,
        fetch_batch=1,
        cancellation_grace=0.01,
    )
    transport = FakeTransport([FakeDelivery(events=events) for _ in range(3)])
    runtime.transport = transport
    runtime.start()
    await started.wait()
    await asyncio.sleep(0.15)
    assert calls == 1 and transport.batches == [1]
    assert runtime._orphaned
    release.set()
    await runtime.close()
