"""Bounded polling, exact-generation publication, and policy-gated recovery."""

import asyncio
import json
import random
from collections.abc import Awaitable, Callable, Mapping
from contextlib import AbstractAsyncContextManager, suppress
from datetime import UTC, datetime, timedelta
from time import monotonic
from typing import Protocol

import structlog

from bike_doc_api.core.config import Settings
from bike_doc_api.core.queue_telemetry import QueueTelemetry, queue_telemetry
from bike_doc_api.repositories.background_jobs import (
    BackgroundJobRepository,
    JobOutcome,
    JobSnapshot,
    PublicationClaim,
    TransportError,
)

logger = structlog.get_logger(__name__)


class Publisher(Protocol):
    """Returning means the broker acknowledged durable publication."""

    async def publish(
        self, subject: str, payload: bytes, message_id: str
    ) -> bool | None: ...

    async def close(self) -> None: ...


class Clock(Protocol):
    def now(self) -> datetime: ...

    async def sleep(self, seconds: float) -> None: ...


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(UTC)

    async def sleep(self, seconds: float) -> None:
        await asyncio.sleep(seconds)


class ReconciliationPolicy(Protocol):
    """Pure bounded decisions on locked state; absent policies prohibit recovery.

    Registered workload policies supply decisions without provider imports;
    the transaction repository owns any required atomic product repair.
    """

    def expired_outcome(self, job: JobSnapshot) -> JobOutcome | None: ...

    def may_republish(self, job: JobSnapshot) -> bool: ...


Transaction = Callable[[], AbstractAsyncContextManager[BackgroundJobRepository]]


def publication_message(claim: PublicationClaim) -> tuple[bytes, str]:
    """Generation is a wake-up epoch, not an independently ordered event."""
    return (
        json.dumps(
            {
                "version": 1,
                "job_id": claim.job_id,
                "publication_generation": claim.generation,
            },
            separators=(",", ":"),
        ).encode(),
        f"{claim.job_id}:{claim.generation}",
    )


class JobMaintenance:
    """Small reusable start/close lifecycle; transactions never span broker I/O."""

    def __init__(
        self,
        settings: Settings,
        transaction: Transaction,
        publisher: Publisher,
        policies: Mapping[tuple[str, int], ReconciliationPolicy],
        *,
        clock: Clock | None = None,
        telemetry: QueueTelemetry = queue_telemetry,
        observe: Callable[[], Awaitable[None]] | None = None,
        close_observer: Callable[[], Awaitable[None]] | None = None,
        jitter: Callable[[], float] = random.random,
    ) -> None:
        self._close_observer = close_observer
        self._observe = observe
        self._telemetry = telemetry
        self._settings = settings
        self._transaction = transaction
        self._publisher = publisher
        self._policies = dict(policies)
        self._clock = clock or SystemClock()
        self._jitter = jitter
        self._subjects = {
            "diagnostic": settings.nats_diagnostic_subject,
            "profile_inference": settings.nats_profile_subject,
        }
        self._last_success: dict[str, float] = {}
        self._tasks: list[asyncio.Task[None]] = []
        self._failure_rounds = 0

    def healthy(self) -> bool:
        return (
            len(self._tasks) == 3
            and all(not task.done() for task in self._tasks)
            and all(
                name in self._last_success
                and monotonic() - self._last_success[name] < max(30, interval * 3)
                for name, interval in (
                    ("publication", self._settings.job_publication_poll_seconds),
                    ("reconciliation", self._settings.job_reconciliation_poll_seconds),
                    ("cleanup", self._settings.job_cleanup_poll_seconds),
                )
            )
        )

    def start(self) -> None:
        """Return immediately, including when PostgreSQL or NATS is unavailable."""
        if self._tasks or not self._settings.job_maintenance_enabled:
            return
        self._tasks = [
            asyncio.create_task(self._run_publication(), name="job-publication"),
            asyncio.create_task(self._run_reconciliation(), name="job-reconciliation"),
            asyncio.create_task(self._run_cleanup(), name="job-cleanup"),
        ]

    async def close(self) -> None:
        """Cancel in-flight work; abandoned claims expire rather than confirm."""
        for task in self._tasks:
            task.cancel()
        # wait(), unlike wait_for(), does not extend the bound waiting for a
        # cancellation-resistant dependency. Native adapters honor cancellation.
        deadline = (
            asyncio.get_running_loop().time()
            + self._settings.job_shutdown_timeout_seconds
        )
        pending: set[asyncio.Task[None]] = set()
        if self._tasks:
            done, pending = await asyncio.wait(
                self._tasks, timeout=self._settings.job_shutdown_timeout_seconds
            )
            for task in done:
                if not task.cancelled() and task.exception() is not None:
                    logger.warning("job_maintenance_shutdown_failed")
        closing = asyncio.create_task(self._close_resources())
        done, closing_pending = await asyncio.wait(
            [closing], timeout=max(0, deadline - asyncio.get_running_loop().time())
        )
        pending.update(closing_pending)
        for task in pending:
            task.cancel()
        for task in done:
            if not task.cancelled() and task.exception() is not None:
                logger.warning("job_maintenance_shutdown_failed")
        if pending:
            logger.warning("job_maintenance_shutdown_timeout")

    async def _close_resources(self) -> None:
        try:
            await self._publisher.close()
        finally:
            if self._close_observer is not None:
                with suppress(Exception):
                    await self._close_observer()

    def _backoff(self) -> timedelta:
        ceiling = min(
            self._settings.job_backoff_max_seconds,
            self._settings.job_backoff_initial_seconds
            * 2 ** min(self._failure_rounds, 20),
        )
        # Equal jitter keeps a positive floor and a strictly bounded ceiling.
        return timedelta(seconds=ceiling * (0.5 + 0.5 * self._jitter()))

    async def publish_once(self) -> None:
        """Commit short claims before publishing a bounded concurrent batch."""
        if self._observe is not None:
            try:
                await self._observe()
            except Exception:
                self._telemetry.observe("maintenance", maintenance="observation_failed")
        async with self._transaction() as repository:
            claims = await repository.claim_publications(
                limit=self._settings.job_publication_batch_limit,
                claim_duration=timedelta(
                    seconds=self._settings.job_publication_claim_seconds
                ),
            )
        tasks = [asyncio.create_task(self._publish(claim)) for claim in claims]
        try:
            results = await asyncio.gather(*tasks)
        finally:
            # A failed DB confirmation must not leave sibling publications
            # detached from the lifecycle or race the next polling batch.
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
        if any(not result for result in results):
            self._failure_rounds = min(self._failure_rounds + 1, 20)
        elif results:
            self._failure_rounds = 0

    async def _publish(self, claim: PublicationClaim) -> bool:
        started = monotonic()
        subject = self._subjects.get(claim.workload_class)
        error = TransportError.REJECTED
        if subject is not None:
            payload, message_id = publication_message(claim)
            try:
                async with asyncio.timeout(self._settings.job_publish_timeout_seconds):
                    duplicate = await self._publisher.publish(
                        subject, payload, message_id
                    )
            except TimeoutError:
                error = TransportError.TIMEOUT
            except Exception:
                error = TransportError.UNAVAILABLE
            else:
                self._telemetry.observe(
                    "publication_ack",
                    monotonic() - started,
                    workload=claim.workload_class,
                )
                self._telemetry.observe(
                    "publication",
                    workload=claim.workload_class,
                    publication="duplicate" if duplicate else "confirmed",
                )
                # Cancellation/DB failure here deliberately leaves the durable
                # claim unconfirmed, even though the broker accepted the message.
                async with self._transaction() as repository:
                    await repository.confirm_publication(claim)
                return True
        async with self._transaction() as repository:
            await repository.fail_publication(
                claim, error=error, retry_after=self._backoff()
            )
        self._telemetry.observe(
            "publication", workload=claim.workload_class, publication=error.value
        )
        logger.warning("job_publication_failed", category=error.value)
        return False

    async def reconcile_once(self) -> None:
        if not self._policies:
            return
        now = self._clock.now()
        horizon = now - timedelta(seconds=self._settings.job_no_progress_seconds)
        # Separate short bounded scans avoid holding both batches simultaneously.
        recovered = []
        async with self._transaction() as repository:
            expired = await repository.claim_scan(
                purpose="expired",
                before=now,
                limit=self._settings.job_reconciliation_batch_limit,
                definitions=frozenset(self._policies),
            )
            for job in expired:
                policy = self._policies.get((job.job_kind, job.input_version))
                outcome = policy.expired_outcome(job) if policy else None
                if outcome is not None and job.execution_token is not None:
                    result = await repository.recover_expired_and_load(
                        job_id=job.id,
                        execution_token=job.execution_token,
                        outcome=outcome,
                    )
                    recovered.append((job.job_kind, outcome.state, result))
        for kind, requested_state, result in recovered:
            changed = result is not None
            if result is not None:
                self._telemetry.observe("recovery", kind=kind, outcome=result.state)
                self._telemetry.observe(
                    "outcome",
                    kind=kind,
                    version=str(result.input_version),
                    workload=result.workload_class,
                    outcome=result.state,
                )
            logger.info(
                "job_recovery",
                kind=kind,
                requested_state=requested_state,
                changed=changed,
            )
        repaired = []
        async with self._transaction() as repository:
            candidates = await repository.claim_scan(
                purpose="no_progress",
                before=horizon,
                limit=self._settings.job_reconciliation_batch_limit,
                definitions=frozenset(self._policies),
            )
            for job in candidates:
                policy = self._policies.get((job.job_kind, job.input_version))
                if policy is not None and policy.may_republish(job):
                    changed = await repository.advance_publication(
                        job_id=job.id,
                        observed_generation=job.desired_generation,
                        no_progress_before=horizon,
                    )
                    repaired.append((job.job_kind, changed))
        for kind, changed in repaired:
            if changed:
                self._telemetry.observe(
                    "maintenance", maintenance="notification_repair"
                )
            logger.info("job_notification_repair", kind=kind, changed=changed)

    async def _run_publication(self) -> None:
        while True:
            try:
                await self.publish_once()
                self._last_success["publication"] = monotonic()
            except Exception:
                self._telemetry.observe(
                    "maintenance", maintenance="publication_scan_failed"
                )
                logger.warning("job_publication_scan_failed")
            await self._clock.sleep(self._settings.job_publication_poll_seconds)

    async def _run_reconciliation(self) -> None:
        while True:
            try:
                await self.reconcile_once()
                self._last_success["reconciliation"] = monotonic()
            except Exception:
                self._telemetry.observe(
                    "maintenance", maintenance="reconciliation_scan_failed"
                )
                logger.warning("job_reconciliation_scan_failed")
            await self._clock.sleep(self._settings.job_reconciliation_poll_seconds)

    async def cleanup_once(self) -> int:
        """One locked batch per cadence, independent of live broker contents."""
        before = self._clock.now() - timedelta(
            seconds=self._settings.job_terminal_retention_seconds
        )
        deleted = 0
        async with self._transaction() as repository:
            candidates = await repository.claim_scan(
                purpose="retention",
                before=before,
                limit=self._settings.job_cleanup_batch_limit,
            )
            for job in candidates:
                deleted += await repository.delete_retained(
                    job_id=job.id, before=before
                )
        self._telemetry.observe(
            "maintenance", len(candidates), maintenance="cleanup_scanned"
        )
        self._telemetry.observe("maintenance", deleted, maintenance="cleanup_deleted")
        logger.info("job_cleanup", scanned=len(candidates), deleted=deleted)
        return deleted

    async def _run_cleanup(self) -> None:
        while True:
            try:
                await self.cleanup_once()
                self._last_success["cleanup"] = monotonic()
            except Exception:
                self._telemetry.observe(
                    "maintenance", maintenance="cleanup_scan_failed"
                )
                logger.warning("job_cleanup_scan_failed")
            await self._clock.sleep(self._settings.job_cleanup_poll_seconds)
