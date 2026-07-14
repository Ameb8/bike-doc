"""Shadow-mode extraction of profile evidence from accepted image turns."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any, Protocol

from pydantic import ValidationError

from bike_doc_api.models.artifact import ArtifactRef
from bike_doc_api.models.bike import BikeFactClaim, BikeProfile
from bike_doc_api.models.profile_inference import ProfileInferenceRun
from bike_doc_api.models.repair_session import RepairSession, RepairTurn
from bike_doc_api.schemas.profile_inference import (
    INFERENCE_SCHEMA_VERSION,
    REAR_BRAKE_TRACER_FIELDS,
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
from bike_doc_api.services.profile_registry import (
    FieldRegistryValidationError,
    get_canonical_field,
    normalize_canonical_value,
)


class ProfileInferenceStatus(StrEnum):
    """Durable status values for shadow inference processing."""

    RUNNING = "running"
    COMPLETED = "completed"
    ABSTAINED = "abstained"
    RETRYABLE = "retryable"
    FAILED = "failed"
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
    """Own shadow inference validation, idempotency, and evidence persistence."""

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
        """Extract pending rear-brake evidence from one accepted image turn."""

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
            ProfileInferenceStatus.FAILED,
        }:
            return _outcome_for_run(
                existing,
                policy_mode=self._resolver_policy.outcome_mode,
            )
        if (
            existing is not None
            and existing.status == ProfileInferenceStatus.RUNNING
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
            status=ProfileInferenceStatus.RUNNING,
            claim_count=0,
            attempt_count=1,
            started_at=now,
        )
        if existing is None:
            await self._runs.add(run)
        else:
            run.status = ProfileInferenceStatus.RUNNING
            run.failure_code = None
            run.attempt_count += 1
            run.started_at = now
            run.completed_at = None
            await self._runs.save(run)
        await self._commit_if_configured()

        try:
            artifacts = await self._load_ready_images(
                artifact_ids=artifact_ids,
                repair_session=repair_session,
            )
        except _ArtifactContextError as exc:
            return await self._finish(
                run,
                status=exc.status,
                failure_code=exc.failure_code,
            )

        try:
            request = await self._build_request(turn, repair_session, bike, artifacts)
        except Exception:
            return await self._finish(
                run,
                status=ProfileInferenceStatus.RETRYABLE,
                failure_code="artifact_unavailable",
            )

        try:
            raw_output = await self._extractor.extract(request)
        except Exception:
            return await self._finish(
                run,
                status=ProfileInferenceStatus.RETRYABLE,
                failure_code="extractor_failure",
            )

        try:
            output = ProfileInferenceOutput.model_validate(raw_output)
            claims = _validated_tracer_claims(output, artifacts)
        except (ValidationError, FieldRegistryValidationError, ValueError):
            return await self._finish(
                run,
                status=ProfileInferenceStatus.FAILED,
                failure_code="schema_invalid",
            )

        if not claims:
            return await self._finish(run, status=ProfileInferenceStatus.ABSTAINED)

        for attempt in range(self._resolution_retry_limit):
            latest_bike = await self._bikes.get_owned_active_for_update(
                bike_id=bike.id,
                user_id=repair_session.user_id,
            )
            if latest_bike is None:
                return await self._finish(
                    run,
                    status=ProfileInferenceStatus.FAILED,
                    failure_code="bike_unavailable",
                )
            try:
                persisted_claims: list[BikeFactClaim] = []
                for claim in claims:
                    artifact_by_id = {artifact.id: artifact for artifact in artifacts}
                    evidence_refs = [
                        {
                            "type": "artifact",
                            "id": artifact_id,
                            "content_sha256": artifact_by_id[
                                artifact_id
                            ].content_sha256,
                        }
                        for artifact_id in claim.artifact_ids
                    ]
                    duplicate = await self._find_duplicate_claim(
                        bike_id=bike.id,
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
                                bike_id=bike.id,
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
                                observed_at=turn.created_at,
                                evidence_basis=claim.evidence_basis,
                                visibility=claim.visibility,
                                model_score=claim.confidence_score,
                                evidence_cues=claim.evidence_cues,
                                disposition="pending",
                                disposition_reason="pending_resolution",
                            ),
                        ),
                    )
                await self._resolver.resolve(
                    bike=latest_bike,
                    claims=persisted_claims,
                )
                break
            except ProfileResolutionConflictError:
                if self._rollback is not None:
                    await self._rollback()
                if attempt + 1 == self._resolution_retry_limit:
                    raise
                continue
            except Exception:
                if self._rollback is not None:
                    await self._rollback()
                raise
        run.claim_count = len(claims)
        return await self._finish(run, status=ProfileInferenceStatus.COMPLETED)

    async def _find_duplicate_claim(
        self,
        *,
        bike_id: str,
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
        failure_code: str | None = None,
    ) -> ProfileInferenceOutcome:
        """Persist a terminal run state without touching diagnostic state."""

        run.status = status
        run.failure_code = failure_code
        run.completed_at = datetime.now(UTC)
        await self._runs.save(run)
        await self._commit_if_configured()
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
        if abstention.field_path not in REAR_BRAKE_TRACER_FIELDS:
            raise ValueError("abstention is outside the rear-brake tracer")
        if abstention.field_path in abstained_paths:
            raise ValueError("abstention field path is repeated")
        abstained_paths.add(abstention.field_path)

    claims: list[ProfileInferenceClaim] = []
    values_by_path: dict[str, Any] = {}
    for claim in output.claims:
        if claim.field_path not in REAR_BRAKE_TRACER_FIELDS:
            raise ValueError("claim is outside the rear-brake tracer")
        if claim.subject_relation != output.scene.target_relation:
            raise ValueError("claim subject relation does not match the scene")
        claim.value = normalize_canonical_value(claim.field_path, claim.value)
        field = get_canonical_field(claim.field_path, claim.value)
        if (
            field.scope != "rear"
            or claim.evidence_basis not in field.permitted_evidence_bases
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

    mechanism = values_by_path.get("brakes.rear.mechanism")
    actuation = values_by_path.get("brakes.rear.actuation")
    if mechanism == "coaster" and actuation not in {None, "none"}:
        raise ValueError("coaster brakes can only use none actuation")
    if actuation == "none" and mechanism not in {None, "coaster"}:
        raise ValueError("none actuation requires a coaster mechanism")
    return claims


def _outcome_for_run(
    run: ProfileInferenceRun,
    *,
    policy_mode: str = "evaluated",
) -> ProfileInferenceOutcome:
    """Map persisted run state into the service's compact result."""

    return ProfileInferenceOutcome(
        status=ProfileInferenceStatus(run.status),
        run_id=run.id,
        claim_count=run.claim_count,
        policy_mode=policy_mode,
    )
