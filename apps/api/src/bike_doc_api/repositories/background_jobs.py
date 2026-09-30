"""Durable job persistence. Every operation uses the caller's transaction.

No method commits, publishes, or logs inputs/exceptions. Delivery resolution
locks and refreshes the authoritative row, validates its definition, and flushes
one transition before returning. Callers must commit before broker settlement.
Scan results are locked with SKIP LOCKED and must be processed in that same
short transaction; do not retain them across network I/O.
"""

from collections.abc import Callable
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Literal, cast
from uuid import uuid4

from sqlalchemy import func, select, tuple_
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from bike_doc_api.models._ids import generate_prefixed_ulid
from bike_doc_api.models.background_job import BackgroundJob

TERMINAL_STATES = frozenset({"succeeded", "interrupted", "dead"})


class JobError(StrEnum):
    DEFINITION_UNKNOWN = "definition_unknown"
    VERSION_UNSUPPORTED = "version_unsupported"
    INPUT_INVALID = "input_invalid"
    ATTEMPTS_EXHAUSTED = "attempts_exhausted"
    PROVIDER_UNAVAILABLE = "provider_unavailable"
    EXECUTION_TIMEOUT = "execution_timeout"
    EXECUTION_LOST = "execution_lost"
    PERMANENT_FAILURE = "permanent_failure"


class TransportError(StrEnum):
    UNAVAILABLE = "unavailable"
    TIMEOUT = "timeout"
    REJECTED = "rejected"


class ResolutionKind(StrEnum):
    EXECUTABLE = "executable"
    TERMINAL = "terminal"
    STALE = "stale"
    EARLY = "early"
    RUNNING = "running"
    RECOVERY_WAIT = "recovery_wait"
    PROHIBITED = "prohibited"
    MISSING = "missing"
    DEFINITION_FAILURE = "definition_failure"
    EXHAUSTED = "exhausted"


@dataclass(frozen=True)
class JobRecord:
    """Validated immutable definition supplied by a typed producer service."""

    job_kind: str
    workload_class: str
    input_version: int
    input: dict[str, object]
    deduplication_key: str
    attempt_limit: int


@dataclass(frozen=True)
class JobSnapshot:
    """Authoritative persistence information for settlement and execution."""

    id: str
    job_kind: str
    workload_class: str
    input_version: int
    input: dict[str, object]
    state: str
    attempt_count: int
    attempt_limit: int
    desired_generation: int
    confirmed_generation: int
    eligible_at: datetime
    execution_token: str | None
    execution_deadline: datetime | None
    effect_boundary_at: datetime | None
    latest_error_category: str | None

    @classmethod
    def from_row(cls, row: BackgroundJob) -> "JobSnapshot":
        return cls(
            **{name: deepcopy(getattr(row, name)) for name in cls.__dataclass_fields__}
        )


@dataclass(frozen=True)
class ExecutionPolicy:
    """Validated definition's hard duration, including timeout grace."""

    hard_duration: timedelta

    def __post_init__(self) -> None:
        _bounded_duration(self.hard_duration)


@dataclass(frozen=True)
class DeliveryResolution:
    kind: ResolutionKind
    job: JobSnapshot | None = None
    wait_until: datetime | None = None


@dataclass(frozen=True)
class PublicationClaim:
    job_id: str
    workload_class: str
    generation: int
    token: str
    until: datetime


@dataclass(frozen=True)
class JobOutcome:
    state: Literal["succeeded", "retrying", "interrupted", "dead"]
    error: JobError | None = None
    retry_after: timedelta | None = None

    def __post_init__(self) -> None:
        if self.state not in {"succeeded", "retrying", "interrupted", "dead"}:
            raise ValueError("invalid outcome state")
        if self.error is not None and not isinstance(self.error, JobError):
            raise ValueError("error must be a bounded category")
        if self.state == "retrying":
            if self.retry_after is None:
                raise ValueError("retry delay required")
            _bounded_duration(self.retry_after)
        elif self.retry_after is not None:
            raise ValueError("only retry outcomes have a delay")


class JobDefinitionConflictError(Exception):
    """Logical identity already exists with different immutable data."""


def _bounded_duration(duration: timedelta) -> None:
    if not timedelta(0) < duration <= timedelta(days=7):
        raise ValueError("duration must be positive and at most seven days")


def _batch_limit(limit: int) -> None:
    if not 1 <= limit <= 1000:
        raise ValueError("batch limit must be between 1 and 1000")


class BackgroundJobRepository:
    """SQL/state-machine seam shared by workers and maintenance callers."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def _now(self) -> datetime:
        # Read AFTER acquiring a row lock; transaction-start now() is too old
        # when a caller waited on another transaction's lock.
        return cast(
            datetime,
            (await self._session.execute(select(func.clock_timestamp()))).scalar_one(),
        )

    async def _lock(self, job_id: str) -> BackgroundJob | None:
        return (
            await self._session.scalars(
                select(BackgroundJob)
                .where(BackgroundJob.id == job_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        ).first()

    async def record(self, definition: JobRecord) -> JobSnapshot:
        """Insert or resolve identity through PostgreSQL uniqueness, without commit."""
        values = {
            name: getattr(definition, name) for name in definition.__dataclass_fields__
        }
        await self._session.execute(
            insert(BackgroundJob)
            .values(id=generate_prefixed_ulid("job_"), **values)
            .on_conflict_do_nothing(constraint="uq_background_jobs_identity")
        )
        row = (
            await self._session.scalars(
                select(BackgroundJob)
                .where(
                    BackgroundJob.job_kind == definition.job_kind,
                    BackgroundJob.deduplication_key == definition.deduplication_key,
                )
                .execution_options(populate_existing=True)
            )
        ).one()
        if any(getattr(row, name) != value for name, value in values.items()):
            raise JobDefinitionConflictError("logical job definition conflict")
        return JobSnapshot.from_row(row)

    async def claim_publications(
        self, *, limit: int, claim_duration: timedelta
    ) -> list[PublicationClaim]:
        _batch_limit(limit)
        _bounded_duration(claim_duration)
        now = await self._now()
        rows = await self._session.scalars(
            select(BackgroundJob)
            .where(
                BackgroundJob.desired_generation > BackgroundJob.confirmed_generation,
                BackgroundJob.publication_eligible_at <= now,
                (BackgroundJob.publication_claim_until.is_(None))
                | (BackgroundJob.publication_claim_until <= now),
            )
            .order_by(BackgroundJob.publication_eligible_at, BackgroundJob.id)
            .limit(limit)
            .with_for_update(skip_locked=True)
            .execution_options(populate_existing=True)
        )
        claims = []
        for row in rows:
            row.publication_claim_token = uuid4().hex
            row.publication_claim_generation = row.desired_generation
            row.publication_claim_until = now + claim_duration
            row.updated_at = now
            claims.append(
                PublicationClaim(
                    row.id,
                    row.workload_class,
                    row.desired_generation,
                    row.publication_claim_token,
                    row.publication_claim_until,
                )
            )
        await self._session.flush()
        return claims

    async def confirm_publication(self, claim: PublicationClaim) -> bool:
        row = await self._lock(claim.job_id)
        now = await self._now()
        if not self._matches_publication(row, claim, now):
            return False
        assert row is not None
        row.confirmed_generation = max(row.confirmed_generation, claim.generation)
        row.publication_confirmed_at = now
        row.transport_error = None
        self._clear_publication_claim(row)
        row.updated_at = now
        await self._session.flush()
        return True

    async def fail_publication(
        self, claim: PublicationClaim, *, error: TransportError, retry_after: timedelta
    ) -> bool:
        _bounded_duration(retry_after)
        if not isinstance(error, TransportError):
            raise ValueError("transport error must be a bounded category")
        row = await self._lock(claim.job_id)
        now = await self._now()
        if not self._matches_publication(row, claim, now):
            return False
        assert row is not None
        row.transport_error = error.value
        row.publication_eligible_at = now + retry_after
        self._clear_publication_claim(row)
        row.updated_at = now
        await self._session.flush()
        return True

    @staticmethod
    def _matches_publication(
        row: BackgroundJob | None, claim: PublicationClaim, now: datetime
    ) -> bool:
        return (
            row is not None
            and row.publication_claim_token == claim.token
            and row.publication_claim_generation == claim.generation
            and row.publication_claim_until is not None
            and row.publication_claim_until > now
        )

    @staticmethod
    def _clear_publication_claim(row: BackgroundJob) -> None:
        row.publication_claim_token = None
        row.publication_claim_generation = None
        row.publication_claim_until = None

    async def resolve_delivery(
        self,
        *,
        job_id: str,
        generation: int,
        workload_class: str,
        validate_definition: Callable[[JobSnapshot], ExecutionPolicy | JobError],
    ) -> DeliveryResolution:
        """Resolve under one row lock; validator is pure, synchronous, and allowlisted.

        The transport adapter validates envelope identity and maps its subject to
        an allowlisted workload before calling. Expired executions wait for
        reconciliation; deliveries never advance recovery publication intent.
        """
        row = await self._lock(job_id)
        if row is None:
            return DeliveryResolution(ResolutionKind.MISSING)
        snapshot = JobSnapshot.from_row(row)
        if (
            generation < 1
            or generation > row.desired_generation
            or row.workload_class != workload_class
        ):
            return DeliveryResolution(ResolutionKind.PROHIBITED, snapshot)
        if row.state in TERMINAL_STATES:
            return DeliveryResolution(ResolutionKind.TERMINAL, snapshot)
        if generation < row.desired_generation:
            return DeliveryResolution(ResolutionKind.STALE, snapshot)
        now = await self._now()
        if row.state == "running":
            assert row.execution_deadline is not None
            kind = (
                ResolutionKind.RUNNING
                if row.execution_deadline > now
                else ResolutionKind.RECOVERY_WAIT
            )
            return DeliveryResolution(kind, snapshot, row.execution_deadline)
        definition = validate_definition(snapshot)
        if isinstance(definition, JobError):
            self._finish(row, JobOutcome("dead", definition), now)
            await self._session.flush()
            return DeliveryResolution(
                ResolutionKind.DEFINITION_FAILURE, JobSnapshot.from_row(row)
            )
        if row.eligible_at > now:
            return DeliveryResolution(ResolutionKind.EARLY, snapshot, row.eligible_at)
        if row.attempt_count >= row.attempt_limit:
            self._finish(row, JobOutcome("dead", JobError.ATTEMPTS_EXHAUSTED), now)
            await self._session.flush()
            return DeliveryResolution(
                ResolutionKind.EXHAUSTED, JobSnapshot.from_row(row)
            )
        row.state = "running"
        row.attempt_count += 1
        row.first_started_at = row.first_started_at or now
        row.execution_started_at = now
        row.execution_deadline = now + definition.hard_duration
        row.execution_token = uuid4().hex
        row.updated_at = now
        await self._session.flush()
        return DeliveryResolution(ResolutionKind.EXECUTABLE, JobSnapshot.from_row(row))

    async def apply_outcome(
        self, *, job_id: str, execution_token: str, outcome: JobOutcome
    ) -> bool:
        row = await self._lock(job_id)
        now = await self._now()
        if (
            row is None
            or row.state != "running"
            or row.execution_token != execution_token
            or row.execution_deadline is None
            or row.execution_deadline <= now
        ):
            return False
        if outcome.state == "retrying" and row.effect_boundary_at is not None:
            return False
        self._finish(row, outcome, now)
        await self._session.flush()
        return True

    async def apply_outcome_and_load(
        self, *, job_id: str, execution_token: str, outcome: JobOutcome
    ) -> JobSnapshot | None:
        """Return the actual persisted outcome (including exhaustion/eligibility).

        The caller commits before using this result to settle a delivery.
        """
        if not await self.apply_outcome(
            job_id=job_id, execution_token=execution_token, outcome=outcome
        ):
            return None
        row = await self._lock(job_id)
        assert row is not None
        return JobSnapshot.from_row(row)

    @staticmethod
    def _finish(row: BackgroundJob, outcome: JobOutcome, now: datetime) -> None:
        if outcome.state == "retrying" and row.attempt_count >= row.attempt_limit:
            outcome = JobOutcome("dead", JobError.ATTEMPTS_EXHAUSTED)
        row.state = outcome.state
        row.latest_error_category = outcome.error.value if outcome.error else None
        row.execution_token = None
        row.execution_started_at = None
        row.execution_deadline = None
        row.updated_at = now
        if outcome.state == "retrying":
            assert outcome.retry_after is not None
            row.eligible_at = now + outcome.retry_after
        else:
            row.terminal_at = now

    async def claim_scan(
        self,
        *,
        purpose: Literal["expired", "no_progress", "retention"],
        before: datetime,
        limit: int,
        definitions: frozenset[tuple[str, int]] | None = None,
    ) -> list[JobSnapshot]:
        """Bounded locked candidates; callers apply policy in the same transaction.

        `before` is the configured no-progress or retention horizon. Retention
        configuration must exceed broker lifetime and all recovery/audit horizons.
        This operation does not delete rows or terminalize diagnostic products.
        """
        _batch_limit(limit)
        now = await self._now()
        query = select(BackgroundJob)
        if definitions is not None:
            query = query.where(
                tuple_(BackgroundJob.job_kind, BackgroundJob.input_version).in_(
                    definitions
                )
            )
        if purpose == "expired":
            query = query.where(
                BackgroundJob.state == "running",
                BackgroundJob.execution_deadline <= now,
                BackgroundJob.execution_deadline <= before,
            ).order_by(BackgroundJob.execution_deadline, BackgroundJob.id)
        elif purpose == "no_progress":
            query = query.where(
                BackgroundJob.state.in_(("queued", "retrying")),
                BackgroundJob.eligible_at <= now,
                BackgroundJob.attempt_count < BackgroundJob.attempt_limit,
                BackgroundJob.effect_boundary_at.is_(None),
                BackgroundJob.updated_at <= before,
            ).order_by(BackgroundJob.updated_at, BackgroundJob.id)
        elif purpose == "retention":
            query = query.where(
                BackgroundJob.state.in_(TERMINAL_STATES),
                BackgroundJob.terminal_at <= before,
                BackgroundJob.desired_generation == BackgroundJob.confirmed_generation,
                BackgroundJob.publication_claim_token.is_(None),
            ).order_by(BackgroundJob.terminal_at, BackgroundJob.id)
        else:
            raise ValueError("unknown scan purpose")
        rows = await self._session.scalars(
            query.limit(limit)
            .with_for_update(skip_locked=True)
            .execution_options(populate_existing=True)
        )
        return [JobSnapshot.from_row(row) for row in rows]

    async def advance_publication(
        self, *, job_id: str, observed_generation: int, no_progress_before: datetime
    ) -> bool:
        """Reconciler-only notification recovery; never a semantic retry."""
        row = await self._lock(job_id)
        now = await self._now()
        if (
            row is None
            or row.state not in {"queued", "retrying"}
            or row.effect_boundary_at is not None
            or row.eligible_at > now
            or row.attempt_count >= row.attempt_limit
            or row.desired_generation != observed_generation
            or row.updated_at > no_progress_before
        ):
            return False
        row.desired_generation += 1
        row.publication_eligible_at = now
        row.updated_at = now
        await self._session.flush()
        return True

    async def recover_expired(
        self, *, job_id: str, execution_token: str, outcome: JobOutcome
    ) -> bool:
        """Conditional maintenance transition after the hard deadline.

        Kind-specific callers own product terminalization in this transaction.
        Post-boundary executions cannot retry; recovery never reports success.
        """
        if outcome.state == "succeeded":
            raise ValueError("lost execution cannot be recovered as success")
        row = await self._lock(job_id)
        now = await self._now()
        if (
            row is None
            or row.state != "running"
            or row.execution_token != execution_token
            or row.execution_deadline is None
            or row.execution_deadline > now
            or (outcome.state == "retrying" and row.effect_boundary_at is not None)
        ):
            return False
        self._finish(row, outcome, now)
        if row.state == "retrying":
            # Recovery creates notification intent, never a new application
            # attempt. Preserve the policy's future eligibility for publication.
            row.desired_generation += 1
            row.publication_eligible_at = row.eligible_at
        await self._session.flush()
        return True
