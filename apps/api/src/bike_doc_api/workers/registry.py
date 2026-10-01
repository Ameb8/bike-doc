"""Allowlisted, transport-independent typed execution definitions."""

import json
import re
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from bike_doc_api.repositories.background_jobs import (
    ExecutionPolicy,
    JobError,
    JobOutcome,
    JobSnapshot,
)


class ReconciliationPolicy(Protocol):
    def expired_outcome(self, job: JobSnapshot) -> JobOutcome | None: ...
    def may_republish(self, job: JobSnapshot) -> bool: ...


class DeliveryEnvelope(BaseModel):
    model_config = ConfigDict(
        extra="forbid", strict=True, frozen=True, hide_input_in_errors=True
    )
    version: int = Field(ge=1, le=1)
    job_id: str = Field(pattern=r"^job_[0-7][0-9A-HJKMNP-TV-Z]{25}$")
    publication_generation: int = Field(gt=0, le=9223372036854775807)

    @classmethod
    def decode(cls, data: bytes) -> "DeliveryEnvelope":
        """Reject oversized and ambiguous JSON before selecting any definition."""
        if len(data) > 256:
            raise ValueError("oversized envelope")

        def unique_fields(pairs: list[tuple[str, object]]) -> dict[str, object]:
            fields: dict[str, object] = {}
            for key, value in pairs:
                if key in fields:
                    raise ValueError("duplicate envelope field")
                fields[key] = value
            return fields

        return cls.model_validate(json.loads(data, object_pairs_hook=unique_fields))


class EffectPolicy(StrEnum):
    IDEMPOTENT = "idempotent"
    FENCED = "fenced"


@dataclass(frozen=True)
class ClaimedJob[InputT: BaseModel]:
    id: str
    input: InputT
    attempt: int
    execution_token: str
    execution_deadline: datetime


@dataclass(frozen=True)
class HandlerPolicy:
    attempt_limit: int
    max_retry_delay: timedelta
    hard_duration: timedelta
    timeout_grace: timedelta
    settlement_reserve: timedelta
    timeout_outcome: JobOutcome
    effect: EffectPolicy
    reconciliation: ReconciliationPolicy

    def __post_init__(self) -> None:
        if type(self.attempt_limit) is not int or not 1 <= self.attempt_limit <= 100:
            raise ValueError("invalid application attempt policy")
        for duration in (
            self.max_retry_delay,
            self.hard_duration,
            self.timeout_grace,
            self.settlement_reserve,
        ):
            if not timedelta(0) < duration <= timedelta(days=7):
                raise ValueError("invalid timing policy")
        if self.timeout_grace + self.settlement_reserve >= self.hard_duration:
            raise ValueError("timeout grace and settlement must fit hard deadline")
        if (
            not isinstance(self.effect, EffectPolicy)
            or not callable(getattr(self.reconciliation, "expired_outcome", None))
            or not callable(getattr(self.reconciliation, "may_republish", None))
        ):
            raise ValueError("effect and reconciliation policies required")
        if not isinstance(self.timeout_outcome, JobOutcome):
            raise ValueError("timeout outcome required")
        self.validate_outcome(self.timeout_outcome)

    def validate_outcome(self, outcome: JobOutcome) -> None:
        if not isinstance(outcome, JobOutcome):
            raise ValueError("handler must return a bounded outcome")
        if outcome.retry_after and outcome.retry_after > self.max_retry_delay:
            raise ValueError("retry exceeds registered policy")


class RegisteredDefinition(Protocol):
    @property
    def kind(self) -> str: ...
    @property
    def version(self) -> int: ...
    @property
    def workload(self) -> str: ...
    @property
    def policy(self) -> HandlerPolicy: ...

    def validate(self, job: JobSnapshot) -> ExecutionPolicy | JobError: ...
    async def invoke(self, job: JobSnapshot) -> JobOutcome: ...


@dataclass(frozen=True)
class HandlerDefinition[InputT: BaseModel]:
    kind: str
    version: int
    workload: str
    input_model: type[InputT]
    handler: Callable[[ClaimedJob[InputT]], Awaitable[JobOutcome]]
    policy: HandlerPolicy

    def __post_init__(self) -> None:
        if (
            not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", self.kind)
            or type(self.version) is not int
            or not 1 <= self.version <= 32767
        ):
            raise ValueError("exact kind and positive version required")
        if not self.workload or not isinstance(self.policy, HandlerPolicy):
            raise ValueError("workload and complete policy required")
        if not callable(self.handler):
            raise ValueError("handler required")
        if (
            not issubclass(self.input_model, BaseModel)
            or self.input_model.model_config.get("extra") != "forbid"
            or self.input_model.model_config.get("strict") is not True
            or self.input_model.model_config.get("frozen") is not True
        ):
            raise ValueError("immutable strict input model required")

    def validate(self, job: JobSnapshot) -> ExecutionPolicy | JobError:
        try:
            self.input_model.model_validate(job.input, strict=True)
        except ValidationError:
            return JobError.INPUT_INVALID
        if job.attempt_limit != self.policy.attempt_limit:
            return JobError.PERMANENT_FAILURE
        return ExecutionPolicy(self.policy.hard_duration)

    async def invoke(self, job: JobSnapshot) -> JobOutcome:
        assert job.execution_token and job.execution_deadline
        return await self.handler(
            ClaimedJob(
                job.id,
                self.input_model.model_validate(job.input, strict=True),
                job.attempt_count,
                job.execution_token,
                job.execution_deadline,
            )
        )


class HandlerRegistry:
    def __init__(
        self, workload: str, definitions: Sequence[RegisteredDefinition]
    ) -> None:
        self.workload = workload
        self._definitions: dict[tuple[str, int], RegisteredDefinition] = {}
        if not workload or not definitions:
            raise ValueError("complete workload registry required")
        for definition in definitions:
            key = (definition.kind, definition.version)
            if (
                definition.workload != workload
                or key in self._definitions
                or not isinstance(definition.policy, HandlerPolicy)
            ):
                raise ValueError("duplicate or incompatible definition")
            self._definitions[key] = definition

    def select(self, job: JobSnapshot) -> RegisteredDefinition:
        return self._definitions[(job.job_kind, job.input_version)]

    def validate(self, job: JobSnapshot) -> ExecutionPolicy | JobError:
        if job.workload_class != self.workload:
            return JobError.PERMANENT_FAILURE
        definition = self._definitions.get((job.job_kind, job.input_version))
        if definition is None:
            return (
                JobError.VERSION_UNSUPPORTED
                if any(kind == job.job_kind for kind, _ in self._definitions)
                else JobError.DEFINITION_UNKNOWN
            )
        return definition.validate(job)

    def reconciliation_policies(self) -> dict[tuple[str, int], ReconciliationPolicy]:
        return {
            key: definition.policy.reconciliation
            for key, definition in self._definitions.items()
        }
