"""Deterministic maintenance fakes: no database, broker, or provider calls."""

import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import httpx
import pytest
from pydantic import ValidationError

import bike_doc_api.main as main
from bike_doc_api.core.config import Settings
from bike_doc_api.maintenance.runtime import JobMaintenance, publication_message
from bike_doc_api.repositories.background_jobs import (
    JobError,
    JobOutcome,
    JobSnapshot,
    PublicationClaim,
    TransportError,
)

NOW = datetime(2026, 9, 30, tzinfo=UTC)
CLAIM = PublicationClaim(
    "job_01K00000000000000000000000",
    "profile_inference",
    3,
    "token",
    NOW + timedelta(seconds=30),
)


class FakeClock:
    def now(self) -> datetime:
        return NOW

    async def sleep(self, seconds: float) -> None:
        await asyncio.Future[None]()


class FakePublisher:
    def __init__(self) -> None:
        self.messages: list[tuple[str, bytes, str]] = []
        self.failure: Exception | None = None
        self.acknowledged = asyncio.Event()
        self.closed = False

    async def publish(self, subject: str, payload: bytes, message_id: str) -> None:
        if self.failure:
            raise self.failure
        self.messages.append((subject, payload, message_id))
        self.acknowledged.set()

    async def close(self) -> None:
        self.closed = True


class FakePolicy:
    def expired_outcome(self, job: JobSnapshot) -> JobOutcome | None:
        return JobOutcome("retrying", JobError.EXECUTION_LOST, timedelta(seconds=2))

    def may_republish(self, job: JobSnapshot) -> bool:
        return job.effect_boundary_at is None


def snapshot(**changes: object) -> JobSnapshot:
    job = JobSnapshot(
        CLAIM.job_id,
        "profile_inference",
        "profile_inference",
        1,
        {},
        "queued",
        0,
        3,
        3,
        0,
        NOW,
        None,
        None,
        None,
        None,
    )
    return replace(job, **changes)


class FakeRepository:
    def __init__(self) -> None:
        self.claims = [CLAIM]
        self.confirmed: list[PublicationClaim] = []
        self.failed: list[tuple[PublicationClaim, TransportError, timedelta]] = []
        self.scans: dict[str, list[JobSnapshot]] = {"expired": [], "no_progress": []}
        self.recovered: list[dict[str, object]] = []
        self.advanced: list[dict[str, object]] = []
        self.in_transaction = False
        self.confirm_failure: BaseException | None = None

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator["FakeRepository"]:
        assert not self.in_transaction
        self.in_transaction = True
        try:
            yield self
        finally:
            self.in_transaction = False

    async def claim_publications(self, **kwargs: object) -> list[PublicationClaim]:
        return self.claims

    async def confirm_publication(self, claim: PublicationClaim) -> bool:
        if self.confirm_failure:
            raise self.confirm_failure
        self.confirmed.append(claim)
        return True

    async def fail_publication(
        self, claim: PublicationClaim, *, error: TransportError, retry_after: timedelta
    ) -> bool:
        self.failed.append((claim, error, retry_after))
        return True

    async def claim_scan(self, *, purpose: str, **kwargs: object) -> list[JobSnapshot]:
        return self.scans[purpose]

    async def recover_expired(self, **kwargs: object) -> bool:
        self.recovered.append(kwargs)
        return True

    async def advance_publication(self, **kwargs: object) -> bool:
        self.advanced.append(kwargs)
        return True


def maintenance(
    repository: FakeRepository, publisher: FakePublisher, **settings: object
) -> JobMaintenance:
    return JobMaintenance(
        Settings(environment="test", **settings),
        repository.transaction,
        publisher,
        {("profile_inference", 1): FakePolicy()},
        clock=FakeClock(),
        jitter=lambda: 1,
    )


async def test_exact_envelope_ack_precedes_confirmation_outside_transaction() -> None:
    repository, publisher = FakeRepository(), FakePublisher()
    original = publisher.publish

    async def publish(*args: object) -> None:
        assert not repository.in_transaction
        assert not repository.confirmed
        await original(*args)

    publisher.publish = publish
    await maintenance(repository, publisher).publish_once()
    subject, payload, message_id = publisher.messages[0]
    assert subject == Settings().nats_profile_subject
    assert json.loads(payload) == {
        "version": 1,
        "job_id": CLAIM.job_id,
        "publication_generation": 3,
    }
    assert message_id == publication_message(CLAIM)[1] == f"{CLAIM.job_id}:3"
    assert repository.confirmed == [CLAIM]


@pytest.mark.parametrize(
    "failure", [asyncio.CancelledError(), RuntimeError("database lost")]
)
async def test_acknowledgement_window_leaves_claim_unconfirmed(
    failure: BaseException,
) -> None:
    repository, publisher = FakeRepository(), FakePublisher()
    repository.confirm_failure = failure
    with pytest.raises(type(failure)):
        await maintenance(repository, publisher).publish_once()
    assert publisher.acknowledged.is_set()
    assert not repository.confirmed and not repository.failed


async def test_unavailability_redacts_errors_and_caps_jittered_backoff(
    capsys: pytest.CaptureFixture[str],
) -> None:
    repository, publisher = FakeRepository(), FakePublisher()
    publisher.failure = ConnectionError(
        "nats://secret:password@private-host user content"
    )
    host = maintenance(repository, publisher)
    for _ in range(10):
        await host.publish_once()
    assert not repository.confirmed
    assert all(error == TransportError.UNAVAILABLE for _, error, _ in repository.failed)
    delays = [delay.total_seconds() for _, _, delay in repository.failed]
    assert delays == [2, 4, 8, 16, 32, 60, 60, 60, 60, 60]
    assert "secret" not in capsys.readouterr().out
    publisher.failure = None
    await host.publish_once()
    assert repository.confirmed == [CLAIM]


async def test_unknown_workload_never_publishes() -> None:
    repository, publisher = FakeRepository(), FakePublisher()
    repository.claims = [replace(CLAIM, workload_class="arbitrary.subject")]
    await maintenance(repository, publisher).publish_once()
    assert not publisher.messages
    assert repository.failed[0][1] == TransportError.REJECTED


async def test_publish_timeout_retains_intent() -> None:
    repository, publisher = FakeRepository(), FakePublisher()
    publisher.publish = AsyncMock(side_effect=lambda *args: None)

    async def hang(*args: object) -> None:
        await asyncio.Future[None]()

    publisher.publish = hang
    await maintenance(
        repository, publisher, job_publish_timeout_seconds=0.1
    ).publish_once()
    assert repository.failed[0][1] == TransportError.TIMEOUT


async def test_reconciliation_is_policy_gated_and_never_publishes() -> None:
    repository, publisher = FakeRepository(), FakePublisher()
    repository.scans["expired"] = [
        snapshot(state="running", execution_token="execution"),
        snapshot(job_kind="diagnostic_turn", execution_token="unsafe"),
    ]
    repository.scans["no_progress"] = [
        snapshot(),
        snapshot(input_version=2),
        snapshot(effect_boundary_at=NOW),
    ]
    await maintenance(repository, publisher).reconcile_once()
    assert len(repository.recovered) == len(repository.advanced) == 1
    assert repository.recovered[0]["execution_token"] == "execution"
    assert repository.advanced[0]["observed_generation"] == 3
    assert repository.advanced[0]["no_progress_before"] == NOW - timedelta(seconds=300)
    assert not publisher.messages


async def test_start_returns_immediately_and_shutdown_cancels_blocked_work() -> None:
    repository, publisher = FakeRepository(), FakePublisher()
    entered = asyncio.Event()

    async def hang(*args: object) -> None:
        entered.set()
        await asyncio.Future[None]()

    publisher.publish = hang
    host = maintenance(repository, publisher)
    host.start()
    await asyncio.wait_for(entered.wait(), 1)
    await asyncio.wait_for(host.close(), 1)
    assert publisher.closed
    assert not repository.confirmed
    assert all(task.done() for task in host._tasks)


async def test_shutdown_bounds_unresponsive_resource_close() -> None:
    repository, publisher = FakeRepository(), FakePublisher()
    publisher.close = AsyncMock(side_effect=asyncio.Event().wait)
    host = maintenance(repository, publisher, job_shutdown_timeout_seconds=0.1)
    await asyncio.wait_for(host.close(), 0.5)
    await asyncio.sleep(0)


async def test_disabled_host_does_no_background_work() -> None:
    repository, publisher = FakeRepository(), FakePublisher()
    host = maintenance(repository, publisher, job_maintenance_enabled=False)
    host.start()
    assert not host._tasks
    await host.close()


@pytest.mark.parametrize(
    "settings",
    [
        {"job_publication_batch_limit": 0},
        {"job_publication_claim_seconds": 10},
        {"job_backoff_initial_seconds": 100, "job_backoff_max_seconds": 60},
        {"job_no_progress_seconds": 30},
        {"job_publication_poll_seconds": float("inf")},
        {"job_reconciliation_poll_seconds": float("nan")},
    ],
)
def test_invalid_maintenance_settings(settings: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        Settings(**settings)


async def test_fastapi_lifespan_runs_maintenance_while_broker_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository, publisher = FakeRepository(), FakePublisher()
    publisher.failure = ConnectionError("unavailable")
    host = maintenance(repository, publisher)
    monkeypatch.setattr(main, "create_job_maintenance", lambda _: host)
    monkeypatch.setattr(
        main, "open_adk_session_service", AsyncMock(return_value=AsyncMock())
    )
    monkeypatch.setattr(main.NatsEventNotifications, "start", lambda _: None)
    app = main.create_app(Settings(environment="test"))
    async with app.router.lifespan_context(app):
        for _ in range(10):
            await asyncio.sleep(0)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            # A valid HTTP response remains available, independent of the loop.
            assert (await client.get("/openapi.json")).status_code == 200
        assert repository.failed and not repository.confirmed
    assert publisher.closed and all(task.done() for task in host._tasks)


async def test_committed_work_is_discovered_by_repeated_bounded_polling() -> None:
    repository, publisher = FakeRepository(), FakePublisher()
    repository.claims = []

    class PollClock(FakeClock):
        def __init__(self) -> None:
            self.sleeps: asyncio.Queue[float] = asyncio.Queue()
            self.resume = asyncio.Event()

        async def sleep(self, seconds: float) -> None:
            self.sleeps.put_nowait(seconds)
            await self.resume.wait()
            self.resume.clear()

    clock = PollClock()
    host = JobMaintenance(
        Settings(environment="test"), repository.transaction, publisher, {}, clock=clock
    )
    host.start()
    try:
        # One sleep belongs to the reconciler, one to the publisher.
        sleeps = [await asyncio.wait_for(clock.sleeps.get(), 1) for _ in range(2)]
        assert sorted(sleeps) == [1, 30]
        assert not publisher.messages
        repository.claims = [CLAIM]
        clock.resume.set()
        await asyncio.wait_for(publisher.acknowledged.wait(), 1)
        for _ in range(10):
            if repository.confirmed:
                break
            await asyncio.sleep(0)
        assert repository.confirmed == [CLAIM]
    finally:
        await host.close()


def test_maintenance_settings_load_prefixed_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("BIKE_DOC_API_JOB_MAINTENANCE_ENABLED", "false")
    monkeypatch.setenv("BIKE_DOC_API_JOB_PUBLICATION_BATCH_LIMIT", "8")
    settings = Settings()
    assert settings.job_maintenance_enabled is False
    assert settings.job_publication_batch_limit == 8
