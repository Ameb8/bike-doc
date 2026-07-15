"""Deterministic resolution of image-inference claims into a V2 profile."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Protocol

from bike_doc_api.models.bike import BikeFactClaim, BikeFieldResolution, BikeProfile
from bike_doc_api.services.profile_registry import (
    FieldRegistryValidationError,
    get_canonical_field,
    get_canonical_field_definition,
)
from bike_doc_api.services.profile_resolution import with_technical_value


class ProfileResolutionConflictError(RuntimeError):
    """The profile changed while a resolution transaction was being committed."""


class PolicyMode(StrEnum):
    """Deployment mode for automatic image-claim resolution."""

    SHADOW = "shadow"
    EVALUATED = "evaluated"
    PROVISIONAL = "provisional"


@dataclass(frozen=True, slots=True)
class ActiveFieldPolicy:
    """A calibrated or provisional policy for one field/evidence class."""

    auto_fill_threshold: float
    auto_overwrite_threshold: float | None = None
    calibration_key: str = "bootstrap-v1"
    policy_version: str = "bootstrap-v1"
    precision_gate_passed: bool = True
    accepted_baseline_version: str | None = "programmatic"
    regression_evidence_passed: bool = True
    promoted: bool = True

    @property
    def is_promoted(self) -> bool:
        """Return whether evaluation evidence authorizes production mutation."""

        return (
            self.promoted
            and self.precision_gate_passed
            and self.accepted_baseline_version is not None
            and self.regression_evidence_passed
        )


@dataclass(frozen=True, slots=True)
class ProfileResolverPolicy:
    """Versioned policy selector kept outside extraction and agent code."""

    mode: PolicyMode
    policies: dict[tuple[str, str], ActiveFieldPolicy]

    @classmethod
    def production(cls) -> ProfileResolverPolicy:
        """Return production policy with no unpromoted field classes enabled."""

        return cls(mode=PolicyMode.EVALUATED, policies={})

    @classmethod
    def shadow(cls) -> ProfileResolverPolicy:
        """Return the explicit policy that records evidence without mutation."""

        return cls(mode=PolicyMode.SHADOW, policies={})

    @classmethod
    def evaluated(
        cls,
        policies: dict[tuple[str, str], ActiveFieldPolicy],
    ) -> ProfileResolverPolicy:
        """Return production mode with only held-out-evaluation-promoted policies."""

        return cls(mode=PolicyMode.EVALUATED, policies=policies)

    @classmethod
    def bootstrap_v1(cls) -> ProfileResolverPolicy:
        """Return the explicit non-production bootstrap field-policy bundle."""

        policy = ActiveFieldPolicy(0.92, 0.97)
        return cls(
            mode=PolicyMode.PROVISIONAL,
            policies={
                ("brakes.rear.mechanism", "direct_visual"): policy,
                ("brakes.rear.actuation", "direct_visual"): policy,
            },
        )

    @property
    def outcome_mode(self) -> str:
        """Return compact telemetry terminology required by the spec."""

        if self.mode is PolicyMode.SHADOW:
            return "shadow"
        return "provisional" if self.mode is PolicyMode.PROVISIONAL else "evaluated"

    def active_policy_for(self, claim: BikeFactClaim) -> ActiveFieldPolicy | None:
        """Return a promoted policy for this exact field/evidence class."""

        if claim.evidence_basis is None:
            return None
        if self.mode is PolicyMode.SHADOW:
            return None
        if self.mode is PolicyMode.PROVISIONAL:
            field = get_canonical_field_definition(claim.field_path)
            if field.policy_bundle != "installed_mechanism":
                return None
        policy = self.policies.get((claim.field_path, claim.evidence_basis))
        if policy is None or (
            self.mode is PolicyMode.EVALUATED and not policy.is_promoted
        ):
            return None
        return policy

    @classmethod
    def from_deployment(
        cls,
        *,
        mode: str,
        policies: Iterable[Any] = (),
    ) -> ProfileResolverPolicy:
        """Build a resolver policy from typed deployment settings.

        Unqualified evaluated entries stay out of the active map. This makes a
        missing or failed gate safe by construction: claims remain evidence,
        while a separately qualified field/evidence class may still mutate.
        """

        if mode == "shadow":
            return cls.shadow()
        if mode == "bootstrap-v1":
            return cls.bootstrap_v1()
        if mode not in {"evaluated", "production"}:
            raise ValueError(f"unknown profile inference policy mode: {mode}")

        active: dict[tuple[str, str], ActiveFieldPolicy] = {}
        seen: set[tuple[str, str]] = set()
        for configured in policies:
            field = get_canonical_field_definition(configured.field_path)
            key = (configured.field_path, configured.evidence_class)
            if key in seen:
                raise ValueError(
                    "duplicate profile inference policy for "
                    f"{configured.field_path}/{configured.evidence_class}",
                )
            seen.add(key)
            if configured.evidence_class not in field.permitted_evidence_bases:
                raise ValueError(
                    f"evidence class {configured.evidence_class!r} is not permitted "
                    f"for {configured.field_path}",
                )
            if not field.image_auto_fill:
                raise ValueError(
                    f"image inference cannot promote {configured.field_path}",
                )
            if not configured.promoted:
                continue
            if not configured.precision_gate_passed:
                continue
            if configured.accepted_baseline_version is None:
                continue
            if not configured.regression_evidence_passed:
                continue
            active[(configured.field_path, configured.evidence_class)] = (
                ActiveFieldPolicy(
                    auto_fill_threshold=configured.auto_fill_threshold,
                    auto_overwrite_threshold=configured.auto_overwrite_threshold,
                    calibration_key=configured.calibration_key,
                    policy_version=configured.policy_version,
                    precision_gate_passed=configured.precision_gate_passed,
                    accepted_baseline_version=configured.accepted_baseline_version,
                    regression_evidence_passed=configured.regression_evidence_passed,
                    promoted=configured.promoted,
                )
            )
        return cls.evaluated(active)


class BikeResolutionRepositoryProtocol(Protocol):
    """Persistence operations used inside one profile-resolution transaction."""

    async def get_resolution(
        self,
        *,
        bike_id: str,
        field_path: str,
    ) -> BikeFieldResolution | None:
        """Return the field's current resolution under the profile lock."""

    async def save_resolution(
        self,
        resolution: BikeFieldResolution,
    ) -> BikeFieldResolution:
        """Persist a changed field resolution."""

    async def add_claim(self, claim: BikeFactClaim) -> BikeFactClaim:
        """Persist a derived claim in the active resolution transaction."""

    async def get_claim(self, claim_id: str) -> BikeFactClaim | None:
        """Return a claim whose disposition may be updated."""

    async def save(self, bike: BikeProfile) -> BikeProfile:
        """Persist an updated resolved profile projection."""


@dataclass(frozen=True, slots=True)
class ProfileMutation:
    """Stable profile mutation dimensions for operational telemetry."""

    field_path: str
    source_transition: str


@dataclass(frozen=True, slots=True)
class ProfileResolutionResult:
    """Observable resolver result without exposing private evidence details."""

    changed: bool
    disposition_counts: dict[str, int]
    mutations: tuple[ProfileMutation, ...] = ()


class ProfileInferenceResolver:
    """Apply only policy-qualified image claims to an already locked profile."""

    def __init__(
        self,
        *,
        bikes: BikeResolutionRepositoryProtocol,
        policy: ProfileResolverPolicy,
    ) -> None:
        self._bikes = bikes
        self._policy = policy

    async def resolve(
        self,
        *,
        bike: BikeProfile,
        claims: list[BikeFactClaim],
    ) -> ProfileResolutionResult:
        """Resolve claims against latest state and mutate once when state changes."""

        changed = False
        mutations: list[ProfileMutation] = []
        now = datetime.now(UTC)
        for claim in claims:
            if not _is_valid_image_claim(claim):
                claim.disposition = "rejected"
                claim.disposition_reason = "invalid_field_scope_or_evidence"
                continue
            resolution = await self._bikes.get_resolution(
                bike_id=bike.id,
                field_path=claim.field_path,
            )
            if resolution is None:
                resolution = BikeFieldResolution(
                    bike_id=bike.id,
                    field_path=claim.field_path,
                    current_value=None,
                    resolution_state="unknown",
                    effective_confidence="unknown",
                )
            policy = self._policy.active_policy_for(claim)
            if self._policy.mode is PolicyMode.SHADOW:
                claim.disposition = "pending"
                claim.disposition_reason = "shadow_policy"
                continue
            if claim_is_blocked_by_clear_barrier(claim, resolution):
                claim.disposition = "pending"
                claim.disposition_reason = "observed_before_manual_clear_barrier"
                continue

            if not _is_eligible_installed_claim(claim, policy):
                claim.disposition = "pending"
                claim.disposition_reason = _pending_reason(claim, policy)
                continue

            assert policy is not None
            confidence = _effective_confidence(claim.model_score, policy)
            previous_source = resolution.source_type or "unknown"
            current_is_unknown = resolution.current_value is None and (
                resolution.resolution_state in {"unknown", "cleared"}
            )
            if current_is_unknown and confidence in {"medium", "high"}:
                claim.disposition = "applied"
                claim.disposition_reason = "auto_fill_policy_satisfied"
                await _supersede_current_claim(self._bikes, resolution)
                resolution.current_value = claim.value
                resolution.resolution_state = "resolved"
                resolution.current_claim_id = claim.id
                resolution.supporting_claim_ids = []
                resolution.conflicting_claim_ids = []
                resolution.effective_confidence = confidence
                resolution.source_type = claim.source_type
                resolution.observed_at = claim.observed_at
                resolution.resolved_at = now
                bike.technical_profile = with_technical_value(
                    bike.technical_profile,
                    field_path=claim.field_path,
                    value=claim.value,
                )
                await self._bikes.save_resolution(resolution)
                mutations.append(
                    ProfileMutation(
                        field_path=claim.field_path,
                        source_transition=f"{previous_source}->{claim.source_type}",
                    ),
                )
                changed = True
                continue

            if current_is_unknown:
                claim.disposition = "pending"
                claim.disposition_reason = "below_auto_fill_threshold"
                continue

            if resolution.current_claim_id == claim.id:
                claim.disposition = "applied"
                claim.disposition_reason = "current_resolution_claim"
                continue

            if claim.value == resolution.current_value:
                claim.disposition = "supporting"
                claim.disposition_reason = "matches_current_resolution"
                supporting_claim_ids = resolution.supporting_claim_ids or []
                if claim.id not in supporting_claim_ids:
                    resolution.supporting_claim_ids = [
                        *supporting_claim_ids,
                        claim.id,
                    ]
                    resolution.effective_confidence = _stronger_confidence(
                        resolution.effective_confidence,
                        confidence,
                    )
                    if resolution.resolution_state == "unknown":
                        resolution.resolution_state = "resolved"
                    await self._bikes.save_resolution(resolution)
                    changed = True
                continue

            if _can_supersede_current(claim, resolution, policy):
                claim.disposition = "applied"
                claim.disposition_reason = "newer_installed_evidence_superseded_current"
                await _supersede_current_claim(self._bikes, resolution)
                resolution.current_value = claim.value
                resolution.resolution_state = "resolved"
                resolution.current_claim_id = claim.id
                resolution.supporting_claim_ids = []
                resolution.conflicting_claim_ids = []
                resolution.effective_confidence = confidence
                resolution.source_type = claim.source_type
                resolution.observed_at = claim.observed_at
                resolution.resolved_at = now
                bike.technical_profile = with_technical_value(
                    bike.technical_profile,
                    field_path=claim.field_path,
                    value=claim.value,
                )
                await self._bikes.save_resolution(resolution)
                mutations.append(
                    ProfileMutation(
                        field_path=claim.field_path,
                        source_transition=f"{previous_source}->{claim.source_type}",
                    ),
                )
                changed = True
                continue

            claim.disposition = "conflict"
            claim.disposition_reason = "current_resolution_retained_by_field_policy"
            conflicting_claim_ids = resolution.conflicting_claim_ids or []
            if claim.id not in conflicting_claim_ids:
                resolution.conflicting_claim_ids = [
                    *conflicting_claim_ids,
                    claim.id,
                ]
                resolution.resolution_state = "disputed"
                await self._bikes.save_resolution(resolution)
                changed = True
                continue

            claim.disposition = "conflict"
            claim.disposition_reason = "current_resolution_retained_by_field_policy"

        if await recompute_derived_brake_summary(
            bikes=self._bikes,
            bike=bike,
        ):
            changed = True
            mutations.append(
                ProfileMutation(
                    field_path="brakes.legacy_summary",
                    source_transition="derived_resolution->derived_resolution",
                ),
            )

        if changed:
            bike.profile_revision = (bike.profile_revision or 0) + 1
            bike.updated_at = now
            await self._bikes.save(bike)
        disposition_counts: dict[str, int] = {}
        for claim in claims:
            disposition_counts[claim.disposition] = (
                disposition_counts.get(claim.disposition, 0) + 1
            )
        return ProfileResolutionResult(
            changed=changed,
            disposition_counts=disposition_counts,
            mutations=tuple(mutations),
        )


def claim_is_blocked_by_clear_barrier(
    claim: BikeFactClaim,
    resolution: BikeFieldResolution,
) -> bool:
    """Return whether a claim predates the user's explicit clear."""

    barrier = resolution.manual_clear_barrier_at
    return barrier is not None and claim.observed_at <= barrier


async def _supersede_current_claim(
    repository: BikeResolutionRepositoryProtocol,
    resolution: BikeFieldResolution,
) -> None:
    """Change only disposition metadata while retaining immutable provenance."""

    if resolution.current_claim_id is None:
        return
    get_claim = getattr(repository, "get_claim", None)
    if get_claim is None:
        current = next(
            (
                claim
                for claim in getattr(repository, "claims", [])
                if claim.id == resolution.current_claim_id
            ),
            None,
        )
    else:
        current = await get_claim(resolution.current_claim_id)
    if current is not None and current.disposition in {"applied", "supporting"}:
        current.disposition = "superseded"
        current.disposition_reason = "superseded_by_newer_installed_evidence"


def _can_supersede_current(
    claim: BikeFactClaim,
    resolution: BikeFieldResolution,
    policy: ActiveFieldPolicy,
) -> bool:
    """Apply source, recency, and overwrite-threshold rules together."""

    field = get_canonical_field_definition(claim.field_path)
    return (
        resolution.current_value is not None
        and resolution.source_type in field.image_auto_supersedes
        and resolution.observed_at is not None
        and claim.observed_at > resolution.observed_at
        and policy.auto_overwrite_threshold is not None
        and claim.model_score is not None
        and claim.model_score >= policy.auto_overwrite_threshold
    )


def _stronger_confidence(left: str, right: str) -> str:
    """Return the strongest compact confidence without exposing raw scores."""

    rank = {"unknown": 0, "low": 1, "medium": 2, "high": 3}
    return left if rank[left] >= rank[right] else right


async def recompute_derived_brake_summary(
    *,
    bikes: BikeResolutionRepositoryProtocol,
    bike: BikeProfile,
) -> bool:
    """Project the legacy brake summary from resolved positioned facts only."""
    paths = (
        "brakes.front.mechanism",
        "brakes.front.actuation",
        "brakes.rear.mechanism",
        "brakes.rear.actuation",
    )
    resolutions = {
        path: await bikes.get_resolution(bike_id=bike.id, field_path=path)
        for path in paths
    }
    existing = await bikes.get_resolution(
        bike_id=bike.id,
        field_path="brakes.legacy_summary",
    )
    front_mechanism = resolutions[paths[0]]
    front_actuation = resolutions[paths[1]]
    rear_mechanism = resolutions[paths[2]]
    rear_actuation = resolutions[paths[3]]

    component_resolutions = (
        front_mechanism,
        front_actuation,
        rear_mechanism,
        rear_actuation,
    )
    if all(resolution is None for resolution in component_resolutions):
        return False

    conflict_ids: list[str] = []
    for resolution in component_resolutions:
        if resolution is None:
            continue
        if resolution.current_claim_id is not None:
            conflict_ids.append(resolution.current_claim_id)
        conflict_ids.extend(
            claim_id
            for claim_id in resolution.conflicting_claim_ids or []
            if claim_id is not None
        )

    def resolved_value(resolution: BikeFieldResolution | None) -> object | None:
        if resolution is None or resolution.resolution_state != "resolved":
            return None
        return resolution.current_value

    fm = resolved_value(front_mechanism)
    fa = resolved_value(front_actuation)
    rm = resolved_value(rear_mechanism)
    ra = resolved_value(rear_actuation)
    summary: str | None = None
    disputed = any(
        resolution is not None and resolution.resolution_state == "disputed"
        for resolution in component_resolutions
    )
    if fm == rm == "disc" and fa == ra == "mechanical":
        summary = "mechanical_disc"
    elif fm == rm == "disc" and fa == ra == "hydraulic":
        summary = "hydraulic_disc"
    elif fm == rm == "rim_other":
        summary = "rim"
    elif rm == "coaster" and ra == "none" and fm is None and fa is None:
        summary = "coaster"
    elif all(value is not None for value in (fm, rm)):
        disputed = True

    if summary is None:
        if existing is None and not disputed:
            return False
        next_state = "disputed" if disputed else "unknown"
        next_conflicts = list(dict.fromkeys(conflict_ids)) if disputed else []
        if existing is None:
            existing = BikeFieldResolution(
                bike_id=bike.id,
                field_path="brakes.legacy_summary",
                current_value=None,
                resolution_state="unknown",
                effective_confidence="unknown",
            )
        if (
            existing.current_value is None
            and existing.resolution_state == next_state
            and (existing.conflicting_claim_ids or []) == next_conflicts
        ):
            return False
        await _supersede_current_claim(bikes, existing)
        existing.current_value = None
        existing.current_claim_id = None
        existing.supporting_claim_ids = []
        existing.conflicting_claim_ids = next_conflicts
        existing.resolution_state = next_state
        existing.effective_confidence = "unknown"
        existing.source_type = "derived_resolution"
        existing.resolved_at = datetime.now(UTC)
        await bikes.save_resolution(existing)
        bike.technical_profile = with_technical_value(
            bike.technical_profile,
            field_path="brakes.legacy_summary",
            value=None,
        )
        return True

    if (
        existing is not None
        and existing.current_value == summary
        and existing.resolution_state == "resolved"
    ):
        return False

    if existing is None:
        existing = BikeFieldResolution(
            bike_id=bike.id,
            field_path="brakes.legacy_summary",
            current_value=None,
            resolution_state="unknown",
            effective_confidence="unknown",
        )
    await _supersede_current_claim(bikes, existing)
    observed_times = [
        resolution.observed_at
        for resolution in component_resolutions
        if resolution is not None and resolution.observed_at is not None
    ]
    observed_at = max(observed_times, default=datetime.now(UTC))
    derived_claim = await bikes.add_claim(
        BikeFactClaim(
            bike_id=bike.id,
            field_path="brakes.legacy_summary",
            value=summary,
            source_type="derived_resolution",
            source_ref={"type": "resolved_brake_components", "bike_id": bike.id},
            evidence_refs=[],
            observed_at=observed_at,
            disposition="applied",
            disposition_reason="derived_from_resolved_brake_components",
        ),
    )
    existing.current_value = summary
    existing.resolution_state = "resolved"
    existing.current_claim_id = derived_claim.id
    existing.supporting_claim_ids = []
    existing.conflicting_claim_ids = []
    existing.effective_confidence = "high"
    existing.source_type = "derived_resolution"
    existing.observed_at = observed_at
    existing.resolved_at = datetime.now(UTC)
    await bikes.save_resolution(existing)
    bike.technical_profile = with_technical_value(
        bike.technical_profile,
        field_path="brakes.legacy_summary",
        value=summary,
    )
    return True


def _is_eligible_installed_claim(
    claim: BikeFactClaim,
    policy: ActiveFieldPolicy | None,
) -> bool:
    """Apply the non-negotiable installed-evidence policy gates."""

    return (
        policy is not None
        and claim.scope_assumption == "installed_on_target_bike"
        and claim.evidence_basis is not None
        and claim.visibility == "clear"
        and claim.model_score is not None
    )


def _is_valid_image_claim(claim: BikeFactClaim) -> bool:
    """Defend the resolver against invalid claims persisted by another caller."""

    try:
        field = get_canonical_field(claim.field_path, claim.value)
    except FieldRegistryValidationError:
        return False
    return (
        field.image_auto_fill
        and field.volatility_class not in {"derived", "user_managed"}
        and claim.evidence_basis in field.permitted_evidence_bases
        and bool(claim.evidence_refs)
    )


def _effective_confidence(score: float | None, policy: ActiveFieldPolicy) -> str:
    """Map a score through policy rather than exposing it as a probability."""

    assert score is not None
    if (
        policy.auto_overwrite_threshold is not None
        and score >= policy.auto_overwrite_threshold
    ):
        return "high"
    if score >= policy.auto_fill_threshold:
        return "medium"
    return "low"


def _pending_reason(
    claim: BikeFactClaim,
    policy: ActiveFieldPolicy | None,
) -> str:
    """Return a compact disposition reason without leaking private model data."""

    if policy is None:
        return "no_active_field_policy"
    if claim.scope_assumption != "installed_on_target_bike":
        return "not_installed_on_target_bike"
    if claim.visibility != "clear":
        return "visibility_not_clear"
    if claim.evidence_basis != "direct_visual":
        return "evidence_not_direct_visual"
    return "below_auto_fill_threshold"
