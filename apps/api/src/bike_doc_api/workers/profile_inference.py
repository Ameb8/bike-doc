"""Typed profile workload adapter and shared-runtime policy."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import timedelta
from typing import Protocol

from bike_doc_api.core.config import Settings
from bike_doc_api.repositories.background_jobs import (
    ExecutionPolicy,
    JobError,
    JobOutcome,
    JobSnapshot,
)
from bike_doc_api.schemas.background_jobs import ProfileInferenceInputV1
from bike_doc_api.schemas.profile_inference import (
    EXTRACTOR_VERSION,
    INFERENCE_SCHEMA_VERSION,
)
from bike_doc_api.services.profile_inference import (
    ProfileInferenceOutcome,
    ProfileInferenceStatus,
)
from bike_doc_api.workers.registry import (
    ClaimedJob,
    EffectPolicy,
    HandlerDefinition,
    HandlerPolicy,
    HandlerRegistry,
)


class InferenceApplication(Protocol):
    async def process_submitted_profile_evidence(
        self,
        turn_id: str,
        *,
        application_attempt: int | None = None,
        inference_schema_version: str = INFERENCE_SCHEMA_VERSION,
    ) -> ProfileInferenceOutcome: ...


@dataclass(frozen=True)
class ProfileRecoveryPolicy:
    retry_seconds: float

    def expired_outcome(self, job: JobSnapshot) -> JobOutcome | None:
        if job.effect_boundary_at is not None:
            return None
        if job.attempt_count >= job.attempt_limit:
            return JobOutcome("dead", JobError.ATTEMPTS_EXHAUSTED)
        return JobOutcome(
            "retrying", JobError.EXECUTION_LOST, timedelta(seconds=self.retry_seconds)
        )

    def may_republish(self, job: JobSnapshot) -> bool:
        return job.effect_boundary_at is None and job.attempt_count < job.attempt_limit


def profile_policy(settings: Settings) -> HandlerPolicy:
    if settings.profile_inference_extractor_version != EXTRACTOR_VERSION:
        raise ValueError("unsupported profile extractor implementation version")
    handler = settings.profile_worker_handler_seconds
    hard = settings.profile_worker_hard_seconds
    grace = settings.profile_worker_timeout_grace_seconds
    if not settings.profile_inference_timeout_seconds < handler < hard - grace:
        raise ValueError(
            "profile provider, handler, grace and settlement must fit hard deadline"
        )
    if (
        not 1
        <= settings.worker_fetch_batch
        <= settings.worker_concurrency
        <= settings.profile_worker_pool_size - 2
    ):
        raise ValueError(
            "profile fetch/concurrency requires two spare database connections"
        )
    return HandlerPolicy(
        settings.profile_inference_max_attempts,
        timedelta(seconds=settings.profile_worker_retry_seconds),
        timedelta(seconds=hard),
        timedelta(seconds=grace),
        timedelta(seconds=hard - handler - grace),
        JobOutcome(
            "retrying",
            JobError.EXECUTION_TIMEOUT,
            timedelta(seconds=settings.profile_worker_retry_seconds),
        ),
        EffectPolicy.IDEMPOTENT,
        ProfileRecoveryPolicy(settings.profile_worker_retry_seconds),
    )


class ProfileInferenceHandler:
    def __init__(self, application: InferenceApplication, retry_seconds: float) -> None:
        self.application = application
        self.retry_seconds = retry_seconds

    async def __call__(self, job: ClaimedJob[ProfileInferenceInputV1]) -> JobOutcome:
        outcome = await self.application.process_submitted_profile_evidence(
            job.input.turn_id,
            application_attempt=job.attempt,
            inference_schema_version=job.input.inference_schema_version,
        )
        if outcome.status in {
            ProfileInferenceStatus.COMPLETED,
            ProfileInferenceStatus.ABSTAINED,
            ProfileInferenceStatus.SKIPPED,
        }:
            return JobOutcome("succeeded")
        if outcome.status == ProfileInferenceStatus.EXHAUSTED:
            return JobOutcome("dead", JobError.ATTEMPTS_EXHAUSTED)
        if outcome.status == ProfileInferenceStatus.TERMINAL_FAILURE:
            return JobOutcome("dead", JobError.PERMANENT_FAILURE)
        return JobOutcome(
            "retrying",
            JobError.PROVIDER_UNAVAILABLE,
            timedelta(seconds=self.retry_seconds),
        )


@dataclass(frozen=True)
class ProfileDefinition(HandlerDefinition[ProfileInferenceInputV1]):
    extractor_version: str

    def validate(self, job: JobSnapshot) -> ExecutionPolicy | JobError:
        result = super().validate(job)
        if isinstance(result, JobError):
            return result
        instruction = ProfileInferenceInputV1.model_validate(job.input)
        if (
            instruction.inference_schema_version != INFERENCE_SCHEMA_VERSION
            or instruction.extractor_version != self.extractor_version
        ):
            return JobError.VERSION_UNSUPPORTED
        return result


def profile_registry(
    settings: Settings,
    handler: Callable[[ClaimedJob[ProfileInferenceInputV1]], Awaitable[JobOutcome]],
) -> HandlerRegistry:
    return HandlerRegistry(
        "profile_inference",
        [
            ProfileDefinition(
                "profile_inference",
                1,
                "profile_inference",
                ProfileInferenceInputV1,
                handler,
                profile_policy(settings),
                settings.profile_inference_extractor_version,
            )
        ],
    )
