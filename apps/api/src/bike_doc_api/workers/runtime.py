"""One workload's bounded pull, execution, persistence, and settlement lifecycle."""

import asyncio
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from math import isfinite
from typing import Protocol

import structlog
from pydantic import ValidationError

from bike_doc_api.repositories.background_jobs import (
    DeliveryResolution,
    ExecutionPolicy,
    JobError,
    JobOutcome,
    JobSnapshot,
    ResolutionKind,
)
from bike_doc_api.workers.registry import DeliveryEnvelope, HandlerRegistry

logger = structlog.get_logger(__name__)


class Clock(Protocol):
    def now(self) -> datetime: ...
    async def sleep(self, seconds: float) -> None: ...


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(UTC)

    async def sleep(self, seconds: float) -> None:
        await asyncio.sleep(seconds)


class Delivery(Protocol):
    @property
    def subject(self) -> str: ...
    @property
    def data(self) -> bytes: ...
    async def ack(self) -> None: ...
    async def nak(self, delay: float) -> None: ...
    async def terminate(self) -> None: ...
    async def progress(self) -> None: ...


class PullTransport(Protocol):
    async def fetch(self, batch: int, wait_seconds: float) -> Sequence[Delivery]: ...
    async def close(self) -> None: ...


class JobStore(Protocol):
    """Each returned transition is committed; transactions never span handler I/O."""

    async def resolve(
        self,
        envelope: DeliveryEnvelope,
        workload: str,
        validate: Callable[[JobSnapshot], ExecutionPolicy | JobError],
    ) -> DeliveryResolution: ...
    async def finish(
        self, job: JobSnapshot, outcome: JobOutcome
    ) -> JobSnapshot | None: ...


@dataclass(frozen=True)
class RuntimeOptions:
    subject: str
    concurrency: int = 4
    fetch_batch: int = 4
    fetch_timeout: float = 1
    ack_wait: float = 30
    progress_interval: float = 10
    shutdown_timeout: float = 30
    cancellation_grace: float = 1
    recovery_delay: float = 5

    def __post_init__(self) -> None:
        if not self.subject or "*" in self.subject or ">" in self.subject:
            raise ValueError("exact workload subject required")
        if (
            type(self.concurrency) is not int
            or type(self.fetch_batch) is not int
            or not 1 <= self.fetch_batch <= self.concurrency <= 128
        ):
            raise ValueError(
                "fetch must fit bounded concurrency and broker pending limit"
            )
        for value in (
            self.fetch_timeout,
            self.ack_wait,
            self.progress_interval,
            self.shutdown_timeout,
            self.cancellation_grace,
            self.recovery_delay,
        ):
            if not isfinite(value) or not 0 < value <= 604800:
                raise ValueError("timing must be finite, positive, and bounded")
        if self.progress_interval >= self.ack_wait / 2:
            raise ValueError("progress must leave acknowledgement timing margin")


class PullWorker:
    def __init__(
        self,
        registry: HandlerRegistry,
        store: JobStore,
        transport: PullTransport,
        options: RuntimeOptions,
        *,
        clock: Clock | None = None,
    ) -> None:
        self.registry = registry
        self.store = store
        self.transport = transport
        self.options = options
        self.clock = clock or SystemClock()
        self._fetcher: asyncio.Task[None] | None = None
        self._active: set[asyncio.Task[None]] = set()
        self._handlers: set[asyncio.Task[JobOutcome]] = set()
        self._orphaned: set[asyncio.Task[JobOutcome]] = set()
        self._closed = False

    def start(self) -> None:
        if self._closed:
            raise RuntimeError("worker is closed")
        if self._fetcher is None:
            self._fetcher = asyncio.create_task(self._pull(), name="job-pull")

    def _stopping(self) -> bool:
        return self._closed

    async def _pull(self) -> None:
        while not self._stopping():
            capacity = (
                self.options.concurrency - len(self._active) - len(self._orphaned)
            )
            if capacity <= 0:
                await asyncio.wait(
                    [*self._active, *self._orphaned],
                    return_when=asyncio.FIRST_COMPLETED,
                )
                continue
            try:
                messages = await self.transport.fetch(
                    min(capacity, self.options.fetch_batch), self.options.fetch_timeout
                )
            except Exception:
                logger.warning("job_worker_fetch_failed")
                await self.clock.sleep(self.options.fetch_timeout)
                continue
            if self._closed:
                return
            for message in messages:
                task = asyncio.create_task(self.process(message), name="job-delivery")
                self._active.add(task)
                task.add_done_callback(self._active.discard)

    async def process(self, delivery: Delivery) -> None:
        """Cancellation and commit ambiguity leave transport unsettled."""
        try:
            if delivery.subject != self.options.subject:
                logger.warning("job_worker_delivery_rejected", reason="subject")
                await delivery.terminate()
                return
            try:
                envelope = DeliveryEnvelope.decode(delivery.data)
            except (ValidationError, ValueError, UnicodeError):
                logger.warning("job_worker_delivery_rejected", reason="envelope")
                await delivery.terminate()
                return
            progress = asyncio.create_task(self._progress(delivery))
            try:
                resolution = await self.store.resolve(
                    envelope, self.registry.workload, self.registry.validate
                )
                logger.info(
                    "job_worker_resolved",
                    resolution=resolution.kind.value,
                    workload=self.registry.workload,
                )
                await self._resolved(delivery, resolution)
            finally:
                progress.cancel()
                await asyncio.gather(progress, return_exceptions=True)
        except Exception:
            # Never log exception strings, payloads, or transport URLs.
            logger.warning(
                "job_worker_delivery_unsettled", workload=self.registry.workload
            )

    async def _progress(self, delivery: Delivery) -> None:
        while True:
            await self.clock.sleep(self.options.progress_interval)
            try:
                await delivery.progress()
            except Exception:
                logger.warning("job_worker_progress_failed")

    async def _resolved(
        self, delivery: Delivery, resolution: DeliveryResolution
    ) -> None:
        kind = resolution.kind
        if kind in {
            ResolutionKind.TERMINAL,
            ResolutionKind.STALE,
            ResolutionKind.EXHAUSTED,
        }:
            await delivery.ack()
        elif kind in {
            ResolutionKind.PROHIBITED,
            ResolutionKind.MISSING,
            ResolutionKind.DEFINITION_FAILURE,
        }:
            await delivery.terminate()
        elif kind in {ResolutionKind.EARLY, ResolutionKind.RUNNING}:
            assert resolution.wait_until is not None
            delay = (resolution.wait_until - self.clock.now()).total_seconds()
            await delivery.nak(max(0.001, delay))
        elif kind == ResolutionKind.RECOVERY_WAIT:
            await delivery.nak(self.options.recovery_delay)
        elif kind == ResolutionKind.EXECUTABLE:
            assert resolution.job is not None
            await self._execute(delivery, resolution.job)
        else:
            raise ValueError("unrecognized resolution")

    async def _execute(self, delivery: Delivery, job: JobSnapshot) -> None:
        definition = self.registry.select(job)
        policy = definition.policy
        assert job.execution_deadline is not None
        remaining = (job.execution_deadline - self.clock.now()).total_seconds()
        timeout = (
            remaining
            - (policy.timeout_grace + policy.settlement_reserve).total_seconds()
        )
        if timeout <= 0:
            return  # Claim took too long; deadline recovery owns it.
        # Convert once: wall clock changes cannot renew execution.
        loop = asyncio.get_running_loop()
        hard_end = loop.time() + remaining
        handler = asyncio.create_task(definition.invoke(job), name="job-handler")
        self._handlers.add(handler)
        handler.add_done_callback(self._handler_done)
        try:
            done, _ = await asyncio.wait({handler}, timeout=timeout)
            if not done:
                handler.cancel()
                grace = min(
                    policy.timeout_grace.total_seconds(),
                    max(
                        0,
                        hard_end
                        - loop.time()
                        - policy.settlement_reserve.total_seconds(),
                    ),
                )
                done, _ = await asyncio.wait({handler}, timeout=grace)
                if not done:
                    return  # Resistant handler is fenced at deadline; never settle it.
                outcome = policy.timeout_outcome
            else:
                outcome = handler.result()
            policy.validate_outcome(outcome)
            # Conditional persistence rejects stale tokens and post-boundary retries.
            budget = hard_end - loop.time()
            if budget <= 0:
                return
            async with asyncio.timeout(budget):
                persisted = await self.store.finish(job, outcome)
            if persisted is None:
                return
            logger.info(
                "job_worker_outcome",
                state=persisted.state,
                workload=self.registry.workload,
            )
            if persisted.state == "retrying":
                delay = (persisted.eligible_at - self.clock.now()).total_seconds()
                await delivery.nak(max(0.001, delay))
            else:
                await delivery.ack()
        finally:
            if not handler.done():
                # Keep its local capacity reserved until cancellation really completes.
                self._orphaned.add(handler)
                handler.cancel()

    def _handler_done(self, task: asyncio.Task[JobOutcome]) -> None:
        self._handlers.discard(task)
        self._orphaned.discard(task)
        if not task.cancelled():
            task.exception()  # Retrieve detached failures without logging content.

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._fetcher:
            self._fetcher.cancel()
            await asyncio.wait({self._fetcher}, timeout=self.options.cancellation_grace)
        active = set(self._active)
        if active:
            _, pending = await asyncio.wait(
                active, timeout=self.options.shutdown_timeout
            )
            for task in pending:
                task.cancel()
            if pending:
                await asyncio.wait(pending, timeout=self.options.cancellation_grace)
        for handler in self._handlers:
            handler.cancel()
        if self._handlers:
            await asyncio.wait(self._handlers, timeout=self.options.cancellation_grace)
        async with asyncio.timeout(self.options.cancellation_grace + 8):
            await self.transport.close()
