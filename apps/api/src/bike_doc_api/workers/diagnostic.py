"""Diagnostic allowlist and pre-effect retry classification; no broker access."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import timedelta
from typing import Protocol

from bike_doc_api.core.config import Settings
from bike_doc_api.repositories.background_jobs import JobError, JobOutcome, JobSnapshot
from bike_doc_api.schemas.background_jobs import DiagnosticTurnInputV1
from bike_doc_api.workers.registry import (
    ClaimedJob,
    EffectPolicy,
    HandlerDefinition,
    HandlerPolicy,
    HandlerRegistry,
)


class DiagnosticPreparationError(Exception):
    """Bounded classification proven to occur before orchestration."""

    def __init__(self, category: JobError, *, retryable: bool) -> None:
        super().__init__(category.value)
        self.category = category
        self.retryable = retryable


class DiagnosticApplication(Protocol):
    async def execute(self, job: ClaimedJob[DiagnosticTurnInputV1]) -> None: ...


@dataclass(frozen=True)
class DiagnosticRecoveryPolicy:
    """Fail closed until atomic diagnostic terminalization lands in #151.

    Running executions require product-state repair, including pre-effect
    exhaustion. Generic maintenance must not terminalize or replay them alone.
    The later diagnostic repository/policy supplies that transaction.
    """

    def expired_outcome(self, job: JobSnapshot) -> JobOutcome | None:
        return None

    def may_republish(self, job: JobSnapshot) -> bool:
        return job.effect_boundary_at is None and job.attempt_count < job.attempt_limit


def diagnostic_policy(settings: Settings) -> HandlerPolicy:
    handler = settings.diagnostic_worker_handler_seconds
    hard = settings.diagnostic_worker_hard_seconds
    grace = settings.diagnostic_worker_timeout_grace_seconds
    providers = (
        settings.diagnostic_agent_timeout_seconds,
        settings.observation_extraction_timeout_seconds,
        settings.price_lookup_timeout_seconds,
        settings.diagnostic_worker_storage_seconds,
    )
    if not max(providers) < handler < hard - grace:
        raise ValueError(
            "diagnostic provider, handler, grace and settlement must fit deadline"
        )
    if (
        not (
            1
            <= settings.diagnostic_worker_fetch_batch
            <= settings.diagnostic_worker_concurrency
            <= settings.diagnostic_worker_pool_size - 2
        )
        or settings.diagnostic_worker_adk_pool_size
        < settings.diagnostic_worker_concurrency
    ):
        raise ValueError(
            "diagnostic concurrency requires product and ADK pool capacity"
        )
    if settings.diagnostic_worker_shutdown_seconds < grace:
        raise ValueError("diagnostic shutdown budget must cover cancellation grace")
    return HandlerPolicy(
        settings.diagnostic_worker_attempt_limit,
        timedelta(seconds=settings.diagnostic_worker_retry_seconds),
        timedelta(seconds=hard),
        timedelta(seconds=grace),
        timedelta(seconds=hard - handler - grace),
        # A timeout may have interrupted effects. Never replay it automatically.
        JobOutcome("interrupted", JobError.EXECUTION_TIMEOUT),
        EffectPolicy.FENCED,
        DiagnosticRecoveryPolicy(),
    )


class DiagnosticTurnHandler:
    def __init__(
        self, application: DiagnosticApplication, retry_seconds: float
    ) -> None:
        self.application = application
        self.retry_seconds = retry_seconds

    async def __call__(self, job: ClaimedJob[DiagnosticTurnInputV1]) -> JobOutcome:
        try:
            await self.application.execute(job)
        except DiagnosticPreparationError as error:
            if error.retryable:
                return JobOutcome(
                    "retrying", error.category, timedelta(seconds=self.retry_seconds)
                )
            return JobOutcome("dead", error.category)
        # Handled runner errors also succeeded: orchestration durably settled
        # the product turn. Unknown failures and cancellation propagate.
        return JobOutcome("succeeded")


def diagnostic_registry(
    settings: Settings,
    handler: Callable[[ClaimedJob[DiagnosticTurnInputV1]], Awaitable[JobOutcome]],
) -> HandlerRegistry:
    return HandlerRegistry(
        "diagnostic",
        [
            HandlerDefinition(
                "diagnostic_turn",
                1,
                "diagnostic",
                DiagnosticTurnInputV1,
                handler,
                diagnostic_policy(settings),
            )
        ],
    )
