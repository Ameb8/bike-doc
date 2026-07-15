"""Lifecycle-safe extraction of profile evidence from accepted image turns."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from time import monotonic
from typing import Any, Protocol

from pydantic import ValidationError

from bike_doc_api.models.artifact import ArtifactRef
from bike_doc_api.models.bike import BikeFactClaim, BikeProfile
from bike_doc_api.models.profile_inference import ProfileInferenceRun
from bike_doc_api.models.repair_session import RepairSession, RepairTurn
from bike_doc_api.schemas.profile_inference import (
    BRAKE_INFERENCE_FIELD_PATHS,
    INFERENCE_SCHEMA_VERSION,
    InferenceImage,
    ProfileInferenceClaim,
    ProfileInferenceOutput,
    ProfileInferenceRequest,
)
from bike_doc_api.services.profile_inference_resolution import (
    ProfileInferenceResolver,
    ProfileResolutionConflictError,
    ProfileResolverPolicy,
)
from bike_doc_api.services.profile_inference_telemetry import (
    ProfileInferenceTelemetry,
    default_profile_inference_telemetry,
)
from bike_doc_api.services.profile_registry import (
    FieldRegistryValidationError,
    get_canonical_field,
    normalize_canonical_value,
)


class ProfileInferenceStatus(StrEnum):
    """Stable lifecycle outcomes for profile-inference processing."""

    STARTED = "started"
    RUNNING = "started"  # Compatibility alias for callers of the tracer.
    COMPLETED = "completed"
    ABSTAINED = "abstained"
    RETRYABLE_FAILURE = "retryable_failure"
    RETRYABLE = "retryable_failure"  # Compatibility alias.
    TERMINAL_FAILURE = "terminal_failure"
    FAILED = "terminal_failure"  # Compatibility alias.
    EXHAUSTED = "exhausted"
    RETRIED = "retried"
    SKIPPED = "skipped"


@dataclass(frozen=True, slots=True)
class ProfileInferenceOutcome:
    """Compact result returned by the internal deep service boundary."""

    status: ProfileInferenceStatus
    run_id: str | None
    claim_count: int = 0
    policy_mode: str = "evaluated"


class ProfileInferenceExtractor(Protocol):
    """Isolated structured extraction adapter."""

    async def extract(self, request: ProfileInferenceRequest) -> dict[str, Any]:
        """Return one raw structured model response."""


class _ArtifactContextError(Exception):
    """A submitted artifact cannot safely be used as inference evidence."""

    def __init__(
        self,
        *,
        status: ProfileInferenceStatus,
        failure_code: str,
    ) -> None:
        self.status = status
        self.failure_code = failure_code
        super().__init__(failure_code)


class TurnRepositoryProtocol(Protocol):
    async def get(self, turn_id: str) -> RepairTurn | None:
        """Return a turn by its server-owned ID."""


class RepairSessionRepositoryProtocol(Protocol):
    async def get(self, repair_session_id: str) -> RepairSession | None:
        """Return a repair session by ID."""


class BikeRepositoryProtocol(Protocol):
    async def get_owned_active(
        self, *, bike_id: str, user_id: str
    ) -> BikeProfile | None:
        """Return an active bike only when it remains user-owned."""

    async def get_owned_active_for_update(
        self, *, bike_id: str, user_id: str
    ) -> BikeProfile | None:
        """Lock an owned active profile before deterministic resolution."""

    async def add_claim(self, claim: BikeFactClaim) -> BikeFactClaim:
        """Persist one immutable bike-fact claim."""

    async def get_claim(self, claim_id: str) -> BikeFactClaim | None:
        """Return a claim whose disposition may be updated."""

    async def list_claims(
        self,
        *,
        bike_id: str,
        field_path: str | None = None,
    ) -> list[BikeFactClaim]:
        """Return claims used to avoid duplicate evidence on replay."""

    async def get_resolution(self, *, bike_id: str, field_path: str) -> Any:
        """Return one current field resolution."""

    async def save_resolution(self, resolution: Any) -> Any:
        """Persist one changed field resolution."""

    async def save(self, bike: BikeProfile) -> BikeProfile:
        """Persist the resolved bike projection."""


class ArtifactRepositoryProtocol(Protocol):
    async def get_owned(self, *, artifact_id: str, user_id: str) -> ArtifactRef | None:
        """Return an artifact only when it remains user-owned."""


class ProfileInferenceRunRepositoryProtocol(Protocol):
    async def get_by_identity(
        self,
        *,
        turn_id: str,
        inference_schema_version: str,
        extractor_version: str,
    ) -> ProfileInferenceRun | None:
        """Return a run by its idempotency tuple."""

    async def add(self, run: ProfileInferenceRun) -> ProfileInferenceRun:
        """Persist a new run."""

    async def save(self, run: ProfileInferenceRun) -> ProfileInferenceRun:
        """Persist updated run state."""


class StorageProviderProtocol(Protocol):
    async def get_object(self, *, path: str, bucket: str | None) -> bytes:
        """Load private artifact bytes from app-owned storage metadata."""


class ProfileInferenceService:
    """Own inference validation, idempotency, retries, and evidence persistence."""

    def __init__(
        self,
        *,
        turns: TurnRepositoryProtocol,
        repair_sessions: RepairSessionRepositoryProtocol,
        bikes: BikeRepositoryProtocol,
        artifacts: ArtifactRepositoryProtocol,
        runs: ProfileInferenceRunRepositoryProtocol,
        storage: StorageProviderProtocol,
        extractor: ProfileInferenceExtractor,
        extractor_version: str,
        running_lease_seconds: float = 60.0,
        resolution_retry_limit: int = 3,
        resolver_policy: ProfileResolverPolicy | None = None,
        max_attempts: int = 1,
        telemetry: ProfileInferenceTelemetry | None = None,
        commit: Callable[[], Awaitable[None]] | None = None,
        rollback: Callable[[], Awaitable[None]] | None = None,
    ) -> None:
        self._turns = turns
        self._repair_sessions = repair_sessions
        self._bikes = bikes
        self._artifacts = artifacts
        self._runs = runs
        self._storage = storage
        self._extractor = extractor
        self._extractor_version = extractor_version
        self._running_lease_seconds = running_lease_seconds
        self._resolution_retry_limit = max(1, resolution_retry_limit)
        self._max_attempts = max(1, max_attempts)
        self._telemetry = telemetry or default_profile_inference_telemetry()
        self._resolver_policy = resolver_policy or ProfileResolverPolicy.production()
        self._resolver = ProfileInferenceResolver(
            bikes=bikes,
            policy=self._resolver_policy,
        )
        self._commit = commit
        self._rollback = rollback

    async def process_submitted_profile_evidence(
        self,
        turn_id: str,
    ) -> ProfileInferenceOutcome:
        """Process one accepted image turn behind a bounded retry boundary."""

        context = await self._load_base_context(turn_id)
        if context is None:
            return ProfileInferenceOutcome(ProfileInferenceStatus.SKIPPED, None)
        turn, repair_session, bike, artifact_ids = context
        if not artifact_ids:
            return ProfileInferenceOutcome(ProfileInferenceStatus.SKIPPED, None)

        existing = await self._runs.get_by_identity(
            turn_id=turn.id,
            inference_schema_version=INFERENCE_SCHEMA_VERSION,
            extractor_version=self._extractor_version,
        )
        now = datetime.now(UTC)
        if existing is not None and existing.status in {
            ProfileInferenceStatus.COMPLETED,
            ProfileInferenceStatus.ABSTAINED,
            ProfileInferenceStatus.TERMINAL_FAILURE,
            ProfileInferenceStatus.EXHAUSTED,
        }:
            return _outcome_for_run(
                existing,
                policy_mode=self._resolver_policy.outcome_mode,
            )
        if (
            existing is not None
            and existing.status == ProfileInferenceStatus.STARTED
            and existing.started_at
            > now - timedelta(seconds=self._running_lease_seconds)
        ):
            return _outcome_for_run(
                existing,
                policy_mode=self._resolver_policy.outcome_mode,
            )

        run = existing or ProfileInferenceRun(
            turn_id=turn.id,
            repair_session_id=repair_session.id,
            bike_id=bike.id,
            inference_schema_version=INFERENCE_SCHEMA_VERSION,
            extractor_version=self._extractor_version,
            input_artifact_ids=artifact_ids,
            status=ProfileInferenceStatus.STARTED,
            claim_count=0,
            attempt_count=1,
            max_attempts=self._max_attempts,
            lifecycle_outcomes=["started"],
            started_at=now,
        )
        if existing is None:
            await self._runs.add(run)
        else:
            run.status = ProfileInferenceStatus.STARTED
            run.failure_code = None
            run.failure_class = None
            run.attempt_count += 1
            run.retry_count = (run.retry_count or 0) + 1
            run.max_attempts = self._max_attempts
            run.started_at = now
            run.completed_at = None
            _record_lifecycle(run, "retried")
            await self._runs.save(run)
        await self._commit_if_configured()
        self._telemetry.event(
            "profile_inference_run_started",
            fields={
                "outcome": "started",
                "attempt": run.attempt_count,
                "policy_mode": self._resolver_policy.outcome_mode,
                "schema_version": INFERENCE_SCHEMA_VERSION,
                "extractor_version": self._extractor_version,
            },
        )

        while True:
            try:
                artifacts = await self._load_ready_images(
                    artifact_ids=artifact_ids,
                    repair_session=repair_session,
                )
            except _ArtifactContextError as exc:
                if exc.status is ProfileInferenceStatus.RETRYABLE_FAILURE:
                    should_retry = await self._record_retryable_failure(
                        run,
                        failure_class="artifact",
                        failure_code=exc.failure_code,
                        retry_limit=self._max_attempts,
                    )
                    if should_retry:
                        continue
                    return _outcome_for_run(
                        run,
                        policy_mode=self._resolver_policy.outcome_mode,
                    )
                return await self._finish(
                    run,
                    status=ProfileInferenceStatus.TERMINAL_FAILURE,
                    failure_class="artifact",
                    failure_code=exc.failure_code,
                )

            try:
                request = await self._build_request(
                    turn,
                    repair_session,
                    bike,
                    artifacts,
                )
            except Exception:
                should_retry = await self._record_retryable_failure(
                    run,
                    failure_class="artifact",
                    failure_code="artifact_unavailable",
                    retry_limit=self._max_attempts,
                )
                if should_retry:
                    continue
                return _outcome_for_run(
                    run,
                    policy_mode=self._resolver_policy.outcome_mode,
                )

            provider_started = monotonic()
            try:
                raw_output = await self._extractor.extract(request)
            except Exception:
                self._record_provider_latency(provider_started)
                should_retry = await self._record_retryable_failure(
                    run,
                    failure_class="provider",
                    failure_code="extractor_failure",
                    retry_limit=self._max_attempts,
                )
                if should_retry:
                    continue
                return _outcome_for_run(
                    run,
                    policy_mode=self._resolver_policy.outcome_mode,
                )
            self._record_provider_latency(provider_started)

            try:
                output = ProfileInferenceOutput.model_validate(raw_output)
                claims = _validated_tracer_claims(output, artifacts)
            except (ValidationError, FieldRegistryValidationError, ValueError):
                self._telemetry.event(
                    "profile_inference_validation_failure",
                    fields={
                        "outcome": "terminal_failure",
                        "failure_class": "validation",
                        "failure_code": "schema_invalid",
                        "schema_version": INFERENCE_SCHEMA_VERSION,
                    },
                )
                self._telemetry.metric(
                    "profile_inference_validation_failures_total",
                    dimensions={"schema_version": INFERENCE_SCHEMA_VERSION},
                )
                return await self._finish(
                    run,
                    status=ProfileInferenceStatus.TERMINAL_FAILURE,
                    failure_class="validation",
                    failure_code="schema_invalid",
                )

            if not claims:
                run.claim_count = 0
                return await self._finish(
                    run,
                    status=ProfileInferenceStatus.ABSTAINED,
                )

            resolution_result: Any | None = None
            for resolution_attempt in range(self._resolution_retry_limit):
                latest_bike = await self._bikes.get_owned_active_for_update(
                    bike_id=bike.id,
                    user_id=repair_session.user_id,
                )
                if latest_bike is None:
                    return await self._finish(
                        run,
                        status=ProfileInferenceStatus.TERMINAL_FAILURE,
                        failure_class="transaction",
                        failure_code="bike_unavailable",
                    )
                resolver_started: float | None = None
                try:
                    persisted_claims = await self._persist_claims(
                        run=run,
                        bike_id=bike.id,
                        claims=claims,
                        artifacts=artifacts,
                        observed_at=turn.created_at,
                    )
                    resolver_started = monotonic()
                    resolution_result = await self._resolver.resolve(
                        bike=latest_bike,
                        claims=persisted_claims,
                    )
                    self._record_resolver_latency(resolver_started)
                    break
                except ProfileResolutionConflictError:
                    if resolver_started is not None:
                        self._record_resolver_latency(resolver_started)
                    if self._rollback is not None:
                        await self._rollback()
                    if resolution_attempt + 1 == self._resolution_retry_limit:
                        return await self._finish(
                            run,
                            status=ProfileInferenceStatus.EXHAUSTED,
                            failure_class="transaction",
                            failure_code="transaction_conflict",
                        )
                    await self._record_retryable_failure(
                        run,
                        failure_class="transaction",
                        failure_code="transaction_conflict",
                        retry_limit=self._resolution_retry_limit,
                        attempt=resolution_attempt + 1,
                    )
                    continue
                except Exception:
                    if resolver_started is not None:
                        self._record_resolver_latency(resolver_started)
                    if self._rollback is not None:
                        await self._rollback()
                    if resolution_attempt + 1 == self._resolution_retry_limit:
                        return await self._finish(
                            run,
                            status=ProfileInferenceStatus.EXHAUSTED,
                            failure_class="transaction",
                            failure_code="transaction_failure",
                        )
                    await self._record_retryable_failure(
                        run,
                        failure_class="transaction",
                        failure_code="transaction_failure",
                        retry_limit=self._resolution_retry_limit,
                        attempt=resolution_attempt + 1,
                    )

            run.claim_count = len(claims)
            self._record_resolution_telemetry(resolution_result, claims)
            return await self._finish(
                run,
                status=ProfileInferenceStatus.COMPLETED,
            )

    async def _persist_claims(
        self,
        *,
        run: ProfileInferenceRun,
        bike_id: str,
        claims: list[ProfileInferenceClaim],
        artifacts: list[ArtifactRef],
        observed_at: datetime,
    ) -> list[BikeFactClaim]:
        """Persist one deterministic claim set, reusing replayed evidence."""

        artifact_by_id = {artifact.id: artifact for artifact in artifacts}
        persisted_claims: list[BikeFactClaim] = []
        for claim in claims:
            evidence_refs = [
                {
                    "type": "artifact",
                    "id": artifact_id,
                    "content_sha256": artifact_by_id[artifact_id].content_sha256,
                }
                for artifact_id in claim.artifact_ids
            ]
            duplicate = await self._find_duplicate_claim(
                bike_id=bike_id,
                run_id=run.id,
                field_path=claim.field_path,
                value=claim.value,
                evidence_refs=evidence_refs,
                content_hashes={
                    artifact_by_id[artifact_id].content_sha256
                    for artifact_id in claim.artifact_ids
                },
            )
            if duplicate is not None:
                persisted_claims.append(duplicate)
                continue
            persisted_claims.append(
                await self._bikes.add_claim(
                    BikeFactClaim(
                        bike_id=bike_id,
                        field_path=claim.field_path,
                        value=claim.value,
                        source_type="image_inference",
                        source_ref={
                            "type": "profile_inference_run",
                            "id": run.id,
                            "subject_relation": claim.subject_relation,
                        },
                        evidence_refs=evidence_refs,
                        scope_assumption=claim.subject_relation,
                        observed_at=observed_at,
                        evidence_basis=claim.evidence_basis,
                        visibility=claim.visibility,
                        model_score=claim.confidence_score,
                        evidence_cues=claim.evidence_cues,
                        disposition="pending",
                        disposition_reason="pending_resolution",
                    ),
                ),
            )
        return persisted_claims

    async def _record_retryable_failure(
        self,
        run: ProfileInferenceRun,
        *,
        failure_class: str,
        failure_code: str,
        retry_limit: int,
        attempt: int | None = None,
    ) -> bool:
        """Persist a retry transition and return whether another attempt is safe."""

        retry_number = attempt or ((run.retry_count or 0) + 1)
        run.status = ProfileInferenceStatus.RETRYABLE_FAILURE
        run.failure_class = failure_class
        run.failure_code = failure_code
        _record_lifecycle(run, "retryable_failure")
        self._telemetry.event(
            "profile_inference_retryable_failure",
            fields={
                "outcome": "retryable_failure",
                "failure_class": failure_class,
                "failure_code": failure_code,
                "attempt": run.attempt_count,
            },
        )
        self._telemetry.metric(
            "profile_inference_failures_total",
            dimensions={"outcome": "retryable_failure", "failure_class": failure_class},
        )
        if retry_limit <= 1 or retry_number >= retry_limit:
            if retry_limit > 1:
                run.status = ProfileInferenceStatus.EXHAUSTED
                _record_lifecycle(run, "exhausted")
                self._telemetry.event(
                    "profile_inference_run_exhausted",
                    fields={
                        "outcome": "exhausted",
                        "failure_class": failure_class,
                        "failure_code": failure_code,
                        "retry_count": run.retry_count or 0,
                    },
                )
                self._telemetry.metric(
                    "profile_inference_runs_total",
                    dimensions={"outcome": "exhausted"},
                )
            await self._runs.save(run)
            await self._commit_if_configured()
            return False

        run.retry_count = (run.retry_count or 0) + 1
        run.attempt_count += 1
        run.status = ProfileInferenceStatus.STARTED
        _record_lifecycle(run, "retried")
        self._telemetry.event(
            "profile_inference_run_retried",
            fields={
                "outcome": "retried",
                "failure_class": failure_class,
                "failure_code": failure_code,
                "attempt": run.attempt_count,
                "retry_count": run.retry_count,
            },
        )
        await self._runs.save(run)
        await self._commit_if_configured()
        return True

    def _record_provider_latency(self, started_at: float) -> None:
        """Record provider timing and optional numeric usage metadata only."""

        latency_ms = (monotonic() - started_at) * 1000
        usage = getattr(self._extractor, "last_usage", None)
        fields: dict[str, object] = {
            "provider": _provider_name(self._extractor),
            "latency_ms": latency_ms,
        }
        if isinstance(usage, dict):
            for key in ("input_tokens", "output_tokens", "total_tokens", "cost_usd"):
                value = usage.get(key)
                if isinstance(value, (int, float)):
                    fields[key] = value
        self._telemetry.metric(
            "profile_inference_provider_latency_ms",
            value=latency_ms,
            dimensions={"provider": fields["provider"]},
        )
        self._telemetry.event("profile_inference_provider_completed", fields=fields)

    def _record_resolution_telemetry(
        self,
        result: Any,
        claims: list[ProfileInferenceClaim],
    ) -> None:
        """Record claim dispositions and stable profile source transitions."""

        self._telemetry.metric(
            "profile_inference_claims_returned_total",
            value=len(claims),
            dimensions={"schema_version": INFERENCE_SCHEMA_VERSION},
        )
        for disposition, count in getattr(result, "disposition_counts", {}).items():
            self._telemetry.event(
                "profile_inference_claims_classified",
                fields={"disposition": disposition, "claim_count": count},
            )
            self._telemetry.metric(
                "profile_inference_claims_total",
                value=count,
                dimensions={"disposition": disposition},
            )
        for mutation in getattr(result, "mutations", ()):
            self._telemetry.event(
                "profile_inference_profile_mutated",
                fields={
                    "field_path": mutation.field_path,
                    "source_transition": mutation.source_transition,
                },
            )
            self._telemetry.metric(
                "profile_inference_profile_mutations_total",
                dimensions={
                    "field_path": mutation.field_path,
                    "source_transition": mutation.source_transition,
                },
            )

    def _record_resolver_latency(self, started_at: float) -> None:
        """Record deterministic resolver latency without claim details."""

        self._telemetry.metric(
            "profile_inference_resolver_latency_ms",
            value=(monotonic() - started_at) * 1000,
        )

    async def _find_duplicate_claim(
        self,
        *,
        bike_id: str,
        run_id: str,
        field_path: str,
        value: Any,
        evidence_refs: list[dict[str, str]],
        content_hashes: set[str],
    ) -> BikeFactClaim | None:
        """Reuse an identical artifact-backed fact across retries or replays."""
        list_claims = getattr(self._bikes, "list_claims", None)
        if list_claims is None:
            return None
        existing_claims = await list_claims(bike_id=bike_id, field_path=field_path)
        return next(
            (
                existing
                for existing in existing_claims
                if existing.source_type == "image_inference"
                and existing.source_ref.get("id") == run_id
                and existing.value == value
                and (
                    existing.evidence_refs == evidence_refs
                    or {
                        ref.get("content_sha256")
                        for ref in existing.evidence_refs
                        if ref.get("content_sha256") is not None
                    }
                    == content_hashes
                )
            ),
            None,
        )

    async def _load_base_context(
        self,
        turn_id: str,
    ) -> tuple[RepairTurn, RepairSession, BikeProfile, list[str]] | None:
        """Reload the app-owned turn, session, bike, and submitted IDs."""

        turn = await self._turns.get(turn_id)
        if turn is None:
            return None
        repair_session = await self._repair_sessions.get(turn.repair_session_id)
        if repair_session is None:
            return None
        bike = await self._bikes.get_owned_active(
            bike_id=repair_session.bike_id,
            user_id=repair_session.user_id,
        )
        if bike is None:
            return None
        try:
            artifact_ids = _turn_artifact_ids(turn)
        except ValueError:
            return None
        return turn, repair_session, bike, artifact_ids

    async def _load_ready_images(
        self,
        *,
        artifact_ids: list[str],
        repair_session: RepairSession,
    ) -> list[ArtifactRef]:
        """Revalidate submitted artifacts before provider access."""

        artifacts: list[ArtifactRef] = []
        for artifact_id in artifact_ids:
            artifact = await self._artifacts.get_owned(
                artifact_id=artifact_id,
                user_id=repair_session.user_id,
            )
            if artifact is None or artifact.repair_session_id != repair_session.id:
                raise _ArtifactContextError(
                    status=ProfileInferenceStatus.FAILED,
                    failure_code="artifact_invalid",
                )
            if artifact.status != "ready":
                raise _ArtifactContextError(
                    status=ProfileInferenceStatus.RETRYABLE,
                    failure_code="artifact_unavailable",
                )
            if artifact.media_type != "image" or not artifact.mime_type.startswith(
                "image/"
            ):
                raise _ArtifactContextError(
                    status=ProfileInferenceStatus.FAILED,
                    failure_code="artifact_invalid",
                )
            artifacts.append(artifact)
        return artifacts

    async def _build_request(
        self,
        turn: RepairTurn,
        repair_session: RepairSession,
        bike: BikeProfile,
        artifacts: list[ArtifactRef],
    ) -> ProfileInferenceRequest:
        """Build the intentionally minimal extractor input from submitted images."""

        images = [
            InferenceImage(
                artifact_id=artifact.id,
                mime_type=artifact.mime_type,
                content=await self._storage.get_object(
                    path=artifact.storage_path,
                    bucket=artifact.storage_bucket,
                ),
            )
            for artifact in artifacts
        ]
        text = turn.message.get("text")
        caption = text if isinstance(text, str) and text.strip() else None
        return ProfileInferenceRequest(
            bike_id=bike.id,
            repair_session_id=repair_session.id,
            caption=caption,
            images=images,
        )

    async def _finish(
        self,
        run: ProfileInferenceRun,
        *,
        status: ProfileInferenceStatus,
        failure_class: str | None = None,
        failure_code: str | None = None,
    ) -> ProfileInferenceOutcome:
        """Persist a terminal run state without touching diagnostic state."""

        run.status = status
        run.failure_class = failure_class
        run.failure_code = failure_code
        run.completed_at = datetime.now(UTC)
        outcome = _lifecycle_outcome(status)
        if outcome is not None:
            _record_lifecycle(run, outcome)
        await self._runs.save(run)
        await self._commit_if_configured()
        if outcome is not None:
            event_name = {
                "completed": "profile_inference_run_completed",
                "abstained": "profile_inference_run_abstained",
                "terminal_failure": "profile_inference_run_terminal_failure",
                "retryable_failure": "profile_inference_retryable_failure",
                "exhausted": "profile_inference_run_exhausted",
            }[outcome]
            fields: dict[str, object] = {"outcome": outcome}
            if failure_class is not None:
                fields["failure_class"] = failure_class
            if failure_code is not None:
                fields["failure_code"] = failure_code
            self._telemetry.event(event_name, fields=fields)
            self._telemetry.metric(
                "profile_inference_runs_total",
                dimensions={"outcome": outcome},
            )
        return _outcome_for_run(
            run,
            policy_mode=self._resolver_policy.outcome_mode,
        )

    async def _commit_if_configured(self) -> None:
        if self._commit is not None:
            await self._commit()


def _turn_artifact_ids(turn: RepairTurn) -> list[str]:
    """Return each referenced artifact once, in accepted action order."""

    raw_ids = turn.message.get("artifact_ids")
    if not isinstance(raw_ids, list) or not all(
        isinstance(value, str) for value in raw_ids
    ):
        raise ValueError("turn artifacts are malformed")
    return list(dict.fromkeys(raw_ids))


def _record_lifecycle(run: ProfileInferenceRun, outcome: str) -> None:
    """Append only compact, bounded lifecycle labels to the durable run."""

    outcomes = list(getattr(run, "lifecycle_outcomes", []) or [])
    if len(outcomes) < 32:
        outcomes.append(outcome)
    run.lifecycle_outcomes = outcomes[-32:]


def _lifecycle_outcome(status: ProfileInferenceStatus) -> str | None:
    """Map a persisted status to its stable lifecycle outcome."""

    return {
        ProfileInferenceStatus.COMPLETED: "completed",
        ProfileInferenceStatus.ABSTAINED: "abstained",
        ProfileInferenceStatus.RETRYABLE_FAILURE: "retryable_failure",
        ProfileInferenceStatus.TERMINAL_FAILURE: "terminal_failure",
        ProfileInferenceStatus.EXHAUSTED: "exhausted",
    }.get(status)


def _provider_name(extractor: ProfileInferenceExtractor) -> str:
    """Return a bounded provider label without exposing implementation details."""

    provider = getattr(extractor, "provider", None)
    return provider if provider in {"gemini", "fake", "unknown"} else "unknown"


def _validated_tracer_claims(
    output: ProfileInferenceOutput,
    artifacts: list[ArtifactRef],
) -> list[ProfileInferenceClaim]:
    """Validate registry, scope, invariants, and evidence against this run input."""

    valid_artifact_ids = {artifact.id for artifact in artifacts}
    if output.claims and (
        not output.scene.contains_bicycle
        or output.scene.multiple_bicycles
        or output.scene.target_relation
        not in {
            "installed_on_target_bike",
            "likely_installed_on_target_bike",
            "loose_component",
            "packaging_or_reference",
        }
    ):
        raise ValueError("scene cannot support installed target-bike claims")
    abstained_paths: set[str] = set()
    for abstention in output.abstentions:
        if abstention.field_path not in BRAKE_INFERENCE_FIELD_PATHS:
            raise ValueError("abstention is outside the brake inference registry")
        if abstention.field_path in abstained_paths:
            raise ValueError("abstention field path is repeated")
        abstained_paths.add(abstention.field_path)

    claims: list[ProfileInferenceClaim] = []
    values_by_path: dict[str, Any] = {}
    for claim in output.claims:
        if claim.field_path not in BRAKE_INFERENCE_FIELD_PATHS:
            raise ValueError("claim is outside the brake inference registry")
        if claim.subject_relation != output.scene.target_relation:
            raise ValueError("claim subject relation does not match the scene")
        claim.value = normalize_canonical_value(claim.field_path, claim.value)
        field = get_canonical_field(claim.field_path, claim.value)
        if field.scope not in {"front", "rear"} or (
            claim.evidence_basis not in field.permitted_evidence_bases
        ):
            raise ValueError("claim field scope or evidence basis is invalid")
        if not set(claim.artifact_ids).issubset(valid_artifact_ids):
            raise ValueError("claim references an artifact outside this run")
        if claim.field_path in values_by_path:
            raise ValueError("claim field path is repeated")
        if claim.field_path in abstained_paths:
            raise ValueError("claim cannot also be explicitly abstained")
        values_by_path[claim.field_path] = claim.value
        claims.append(claim)

    front_mechanism = values_by_path.get("brakes.front.mechanism")
    front_actuation = values_by_path.get("brakes.front.actuation")
    rear_mechanism = values_by_path.get("brakes.rear.mechanism")
    rear_actuation = values_by_path.get("brakes.rear.actuation")
    if front_mechanism == "coaster" or front_actuation == "none":
        raise ValueError("only a rear coaster brake may use coaster/none semantics")
    if rear_mechanism == "coaster" and rear_actuation not in {None, "none"}:
        raise ValueError("coaster brakes can only use none actuation")
    if rear_actuation == "none" and rear_mechanism not in {None, "coaster"}:
        raise ValueError("none actuation requires a coaster mechanism")
    return claims


def _outcome_for_run(
    run: ProfileInferenceRun,
    *,
    policy_mode: str = "evaluated",
) -> ProfileInferenceOutcome:
    """Map persisted run state into the service's compact result."""

    status = {
        "running": ProfileInferenceStatus.STARTED,
        "retryable": ProfileInferenceStatus.RETRYABLE_FAILURE,
        "failed": ProfileInferenceStatus.TERMINAL_FAILURE,
    }.get(run.status, run.status)
    return ProfileInferenceOutcome(
        status=ProfileInferenceStatus(status),
        run_id=run.id,
        claim_count=run.claim_count,
        policy_mode=policy_mode,
    )
