"""Bike profile service."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol, cast

from bike_doc_api.core.errors import (
    BikeRepairHistoryConflictError,
    NotFoundError,
    SessionStateConflictError,
    StaleSessionError,
)
from bike_doc_api.models.bike import (
    BikeFactClaim,
    BikeFieldResolution,
)
from bike_doc_api.models.bike import (
    BikeProfile as BikeProfileModel,
)
from bike_doc_api.models.repair_session import (
    RepairPhaseSession as RepairPhaseSessionModel,
)
from bike_doc_api.models.repair_session import RepairSession as RepairSessionModel
from bike_doc_api.models.user import User
from bike_doc_api.schemas.bike import (
    BikeProfile,
    BikeProfileCreate,
    BikeProfileList,
    BikeProfilePatch,
    BikeType,
    BrakeType,
    FrameMaterial,
)
from bike_doc_api.schemas.common import RepairSessionPhase
from bike_doc_api.services.profile_registry import get_canonical_field
from bike_doc_api.services.profile_resolution import (
    has_technical_value_path,
    manual_legacy_field_claims,
    technical_value,
    with_technical_value,
)

DEFAULT_BIKE_LIMIT = 50
MAX_BIKE_LIMIT = 100


class BikeRepositoryProtocol(Protocol):
    """Bike persistence operations required by the service."""

    async def add(self, bike: BikeProfileModel) -> BikeProfileModel:
        """Add a bike profile to the current transaction."""

    async def get_owned_active(
        self,
        *,
        bike_id: str,
        user_id: str,
    ) -> BikeProfileModel | None:
        """Return a non-deleted bike profile owned by a user."""

    async def get_owned_active_for_update(
        self,
        *,
        bike_id: str,
        user_id: str,
    ) -> BikeProfileModel | None:
        """Lock one owned active profile for a transactional resolution."""

    async def list_owned_active(
        self,
        user_id: str,
        *,
        limit: int = DEFAULT_BIKE_LIMIT,
    ) -> list[BikeProfileModel]:
        """Return non-deleted bike profiles for a user."""

    async def save(self, bike: BikeProfileModel) -> BikeProfileModel:
        """Persist changes to an existing bike profile."""

    async def add_claim(self, claim: BikeFactClaim) -> BikeFactClaim:
        """Append an immutable technical claim."""

    async def get_claim(self, claim_id: str) -> BikeFactClaim | None:
        """Return a claim so its mutable disposition may be updated."""

    async def get_resolution(
        self,
        *,
        bike_id: str,
        field_path: str,
    ) -> BikeFieldResolution | None:
        """Return the current resolution for one canonical field."""

    async def save_resolution(
        self,
        resolution: BikeFieldResolution,
    ) -> BikeFieldResolution:
        """Persist one changed field resolution."""

    async def list_resolutions(self, *, bike_id: str) -> list[BikeFieldResolution]:
        """Return profile resolution metadata for internal reads."""

    async def soft_delete(self, bike: BikeProfileModel) -> BikeProfileModel:
        """Soft-delete a bike profile."""

    async def list_bike_ids_with_owned_repair_sessions(
        self,
        *,
        user_id: str,
        bike_ids: list[str],
    ) -> set[str]:
        """Return bike ids with one or more owned repair sessions."""


class DiagnosticRepairSessionRepositoryProtocol(Protocol):
    """Repair-session lookup required for an agent profile read."""

    async def get_owned(
        self,
        *,
        repair_session_id: str,
        user_id: str,
    ) -> RepairSessionModel | None:
        """Return an owned repair session."""


class DiagnosticPhaseSessionRepositoryProtocol(Protocol):
    """Phase-session lookup required for an agent profile read."""

    async def get_for_session_phase(
        self,
        *,
        repair_session_id: str,
        phase: str,
    ) -> RepairPhaseSessionModel | None:
        """Return the current phase session for a repair-session phase."""


@dataclass(frozen=True, slots=True)
class DiagnosticBikeProfile:
    """Resolved profile context attached to a diagnostic repair session."""

    bike_profile: DiagnosticBikeProfileProjection
    user_skill_level: str


@dataclass(frozen=True, slots=True)
class DiagnosticBikeProfileProjection:
    """Compact V2 agent context with V1 values retained only for compatibility."""

    id: str
    display_name: str
    make: str | None
    model: str | None
    model_year: int | None
    bike_type: str
    frame_material: str | None
    drivetrain: str | None
    brake_type: str | None
    wheel_size: str | None
    tire_size: str | None
    notes: str | None
    schema_version: str
    profile: dict[str, Any]
    field_states: dict[str, dict[str, Any]]
    conflicts: list[dict[str, Any]]


class ResolvedBikeProfileService:
    """Own all current profile reads, writes, and projections.

    Callers use public CRUD methods or the diagnostic-session lookup without
    knowing whether technical fields come from legacy columns or a future
    resolved claim projection.
    """

    def __init__(
        self,
        bikes: BikeRepositoryProtocol,
        *,
        repair_sessions: DiagnosticRepairSessionRepositoryProtocol | None = None,
        phase_sessions: DiagnosticPhaseSessionRepositoryProtocol | None = None,
        commit: Callable[[], Awaitable[None]] | None = None,
    ) -> None:
        self._bikes = bikes
        self._repair_sessions = repair_sessions
        self._phase_sessions = phase_sessions
        self._commit = commit

    async def list_bikes(
        self,
        *,
        current_user: User,
        limit: int = DEFAULT_BIKE_LIMIT,
        cursor: str | None = None,
    ) -> BikeProfileList:
        """Return owned bike profiles in reverse creation order."""

        _ = cursor
        bikes = await self._bikes.list_owned_active(current_user.id, limit=limit)
        bike_ids_with_repair_sessions = (
            await self._bikes.list_bike_ids_with_owned_repair_sessions(
                user_id=current_user.id,
                bike_ids=[bike.id for bike in bikes],
            )
        )
        return BikeProfileList(
            items=[
                _public_profile_from_model(
                    bike,
                    has_repair_sessions=bike.id in bike_ids_with_repair_sessions,
                )
                for bike in bikes
            ],
            next_cursor=None,
        )

    async def create_bike(
        self,
        *,
        current_user: User,
        request: BikeProfileCreate,
    ) -> BikeProfile:
        """Create a bike profile for the current user."""

        created = await self._bikes.add(
            BikeProfileModel(
                user_id=current_user.id,
                display_name=request.display_name,
                make=request.make,
                model=request.model,
                model_year=request.model_year,
                bike_type=request.bike_type.value,
                frame_material=request.frame_material.value,
                drivetrain=request.drivetrain,
                brake_type=request.brake_type.value,
                wheel_size=request.wheel_size,
                tire_size=request.tire_size,
                notes=request.notes,
            ),
        )
        await self._apply_manual_technical_fields(
            bike=created,
            field_values={
                "make": request.make,
                "model": request.model,
                "model_year": request.model_year,
                "bike_type": request.bike_type.value,
                "frame_material": request.frame_material.value,
                "drivetrain": request.drivetrain,
                "brake_type": request.brake_type.value,
                "wheel_size": request.wheel_size,
                "tire_size": request.tire_size,
            },
            include_clears=False,
        )
        if self._commit is not None:
            await self._commit()
        return _public_profile_from_model(created, has_repair_sessions=False)

    async def get_bike(
        self,
        *,
        current_user: User,
        bike_id: str,
    ) -> BikeProfile:
        """Return an owned bike profile."""

        bike = await self._get_owned_bike(current_user=current_user, bike_id=bike_id)
        bike_ids_with_repair_sessions = (
            await self._bikes.list_bike_ids_with_owned_repair_sessions(
                user_id=current_user.id,
                bike_ids=[bike.id],
            )
        )
        return _public_profile_from_model(
            bike,
            has_repair_sessions=bike.id in bike_ids_with_repair_sessions,
        )

    async def update_bike(
        self,
        *,
        current_user: User,
        bike_id: str,
        patch: BikeProfilePatch,
    ) -> BikeProfile:
        """Patch an owned bike profile, preserving omitted fields."""

        bike = await self._get_owned_bike_for_update(
            current_user=current_user,
            bike_id=bike_id,
        )
        direct_change = False
        technical_fields: dict[str, str | int | None] = {}
        for field_name in patch.model_fields_set:
            value = getattr(patch, field_name)
            if field_name in {"display_name", "notes"}:
                if getattr(bike, field_name) != value:
                    setattr(bike, field_name, value)
                    direct_change = True
                continue
            technical_fields[field_name] = (
                value.value
                if field_name in {"bike_type", "frame_material", "brake_type"}
                else value
            )

        technical_change = await self._apply_manual_technical_fields(
            bike=bike,
            field_values=technical_fields,
            include_clears=True,
        )
        updated = bike
        if direct_change and not technical_change:
            updated = await self._bikes.save(bike)
        if self._commit is not None:
            await self._commit()
        bike_ids_with_repair_sessions = (
            await self._bikes.list_bike_ids_with_owned_repair_sessions(
                user_id=current_user.id,
                bike_ids=[updated.id],
            )
        )
        return _public_profile_from_model(
            updated,
            has_repair_sessions=updated.id in bike_ids_with_repair_sessions,
        )

    async def _apply_manual_technical_fields(
        self,
        *,
        bike: BikeProfileModel,
        field_values: dict[str, str | int | None],
        include_clears: bool,
    ) -> bool:
        """Append manual claims and atomically update the resolved projection."""

        changed = False
        timestamp = datetime.now(UTC)
        for field_name, value in field_values.items():
            if not include_clears and (value is None or value == "unknown"):
                continue
            for new_claim in manual_legacy_field_claims(field_name, value):
                if new_claim.value is not None:
                    get_canonical_field(new_claim.field_path, new_claim.value)
                claim = await self._bikes.add_claim(
                    BikeFactClaim(
                        bike_id=bike.id,
                        field_path=new_claim.field_path,
                        value=new_claim.value,
                        source_type=new_claim.source_type,
                        source_ref={"type": "bike_profile", "id": bike.id},
                        scope_assumption=new_claim.scope_assumption,
                        observed_at=timestamp,
                        disposition="applied",
                        disposition_reason="manual_profile_write",
                    ),
                )
                resolution = await self._bikes.get_resolution(
                    bike_id=bike.id,
                    field_path=new_claim.field_path,
                )
                if resolution is None:
                    resolution = BikeFieldResolution(
                        bike_id=bike.id,
                        field_path=new_claim.field_path,
                        current_value=None,
                        resolution_state="unknown",
                        effective_confidence="unknown",
                    )
                if new_claim.source_type == "manual_profile_clear":
                    await self._supersede_current_claim(resolution)
                    resolution.current_value = None
                    resolution.resolution_state = "cleared"
                    resolution.current_claim_id = claim.id
                    resolution.supporting_claim_ids = []
                    resolution.conflicting_claim_ids = []
                    resolution.effective_confidence = "unknown"
                    resolution.source_type = claim.source_type
                    resolution.observed_at = timestamp
                    resolution.resolved_at = timestamp
                    resolution.manual_clear_barrier_at = timestamp
                    bike.technical_profile = with_technical_value(
                        bike.technical_profile,
                        field_path=new_claim.field_path,
                        value=None,
                    )
                    changed = True
                elif (
                    resolution.current_value == new_claim.value
                    and resolution.resolution_state == "resolved"
                ):
                    claim.disposition = "supporting"
                    claim.disposition_reason = "matches_current_manual_resolution"
                    resolution.supporting_claim_ids = [
                        *resolution.supporting_claim_ids,
                        claim.id,
                    ]
                else:
                    await self._supersede_current_claim(resolution)
                    resolution.current_value = new_claim.value
                    resolution.resolution_state = "resolved"
                    resolution.current_claim_id = claim.id
                    resolution.supporting_claim_ids = []
                    resolution.conflicting_claim_ids = []
                    resolution.effective_confidence = "high"
                    resolution.source_type = claim.source_type
                    resolution.observed_at = timestamp
                    resolution.resolved_at = timestamp
                    bike.technical_profile = with_technical_value(
                        bike.technical_profile,
                        field_path=new_claim.field_path,
                        value=new_claim.value,
                    )
                    changed = True
                await self._bikes.save_resolution(resolution)
        if changed:
            bike.profile_revision = (bike.profile_revision or 0) + 1
            bike.updated_at = timestamp
            await self._bikes.save(bike)
        return changed

    async def _supersede_current_claim(
        self,
        resolution: BikeFieldResolution,
    ) -> None:
        """Retain prior provenance while marking it no longer current."""

        if resolution.current_claim_id is None:
            return
        current_claim = await self._bikes.get_claim(resolution.current_claim_id)
        if current_claim is not None and current_claim.disposition == "applied":
            current_claim.disposition = "superseded"
            current_claim.disposition_reason = "superseded_by_manual_profile_write"

    async def delete_bike(
        self,
        *,
        current_user: User,
        bike_id: str,
    ) -> None:
        """Soft-delete an owned bike profile."""

        bike = await self._get_owned_bike(current_user=current_user, bike_id=bike_id)
        bike_ids_with_repair_sessions = (
            await self._bikes.list_bike_ids_with_owned_repair_sessions(
                user_id=current_user.id,
                bike_ids=[bike.id],
            )
        )
        if bike.id in bike_ids_with_repair_sessions:
            raise BikeRepairHistoryConflictError()
        await self._bikes.soft_delete(bike)
        if self._commit is not None:
            await self._commit()

    async def get_diagnostic_bike_profile(
        self,
        *,
        current_user: User,
        repair_session_id: str,
        diagnostic_session_id: str,
    ) -> DiagnosticBikeProfile:
        """Return the resolved profile attached to an active diagnostic session."""

        repair_session = await self._get_owned_diagnostic_session(
            current_user=current_user,
            repair_session_id=repair_session_id,
            diagnostic_session_id=diagnostic_session_id,
        )
        bike = await self._bikes.get_owned_active(
            bike_id=repair_session.bike_id,
            user_id=current_user.id,
        )
        if bike is None:
            raise NotFoundError()
        return DiagnosticBikeProfile(
            bike_profile=await self._diagnostic_projection(bike),
            user_skill_level=current_user.skill_level,
        )

    async def _diagnostic_projection(
        self,
        bike: BikeProfileModel,
    ) -> DiagnosticBikeProfileProjection:
        """Build compact agent context without exposing the claim ledger."""

        legacy = _public_profile_from_model(bike)
        resolutions = await self._bikes.list_resolutions(bike_id=bike.id)
        field_states: dict[str, dict[str, Any]] = {}
        conflicts: list[dict[str, Any]] = []
        for resolution in resolutions:
            field_states[resolution.field_path] = {
                "resolution_state": resolution.resolution_state,
                "effective_confidence": resolution.effective_confidence,
                "source_type": resolution.source_type,
                "observed_at": resolution.observed_at,
            }
            if resolution.resolution_state == "disputed":
                conflicts.append(
                    {
                        "field_path": resolution.field_path,
                        "current_value": resolution.current_value,
                        "candidate_values": [],
                    },
                )
        return DiagnosticBikeProfileProjection(
            id=legacy.id,
            display_name=legacy.display_name,
            make=legacy.make,
            model=legacy.model,
            model_year=legacy.model_year,
            bike_type=legacy.bike_type.value,
            frame_material=(
                legacy.frame_material.value
                if legacy.frame_material is not None
                else None
            ),
            drivetrain=legacy.drivetrain,
            brake_type=legacy.brake_type.value
            if legacy.brake_type is not None
            else None,
            wheel_size=legacy.wheel_size,
            tire_size=legacy.tire_size,
            notes=legacy.notes,
            schema_version="bike_profile.v2",
            profile={
                "schema_version": "bike_profile.v2",
                "id": bike.id,
                "user_id": bike.user_id,
                "display_name": bike.display_name,
                "profile_revision": bike.profile_revision or 0,
                **(bike.technical_profile or {}),
                "notes": bike.notes,
            },
            field_states=field_states,
            conflicts=conflicts,
        )

    async def _get_owned_bike(
        self,
        *,
        current_user: User,
        bike_id: str,
    ) -> BikeProfileModel:
        bike = await self._bikes.get_owned_active(
            bike_id=bike_id,
            user_id=current_user.id,
        )
        if bike is None:
            raise NotFoundError()
        return bike

    async def _get_owned_bike_for_update(
        self,
        *,
        current_user: User,
        bike_id: str,
    ) -> BikeProfileModel:
        bike = await self._bikes.get_owned_active_for_update(
            bike_id=bike_id,
            user_id=current_user.id,
        )
        if bike is None:
            raise NotFoundError()
        return bike

    async def _get_owned_diagnostic_session(
        self,
        *,
        current_user: User,
        repair_session_id: str,
        diagnostic_session_id: str,
    ) -> RepairSessionModel:
        """Return an owned active diagnostic session or raise a domain error."""

        if self._repair_sessions is None:
            msg = "Diagnostic profile reads require a repair-session repository."
            raise RuntimeError(msg)
        repair_session = await self._repair_sessions.get_owned(
            repair_session_id=repair_session_id,
            user_id=current_user.id,
        )
        if repair_session is None:
            raise NotFoundError()
        if repair_session.phase != RepairSessionPhase.DIAGNOSTIC.value:
            raise SessionStateConflictError()
        if self._phase_sessions is not None:
            phase_session = await self._phase_sessions.get_for_session_phase(
                repair_session_id=repair_session.id,
                phase=RepairSessionPhase.DIAGNOSTIC.value,
            )
            if phase_session is None or phase_session.id != diagnostic_session_id:
                raise StaleSessionError()
        return repair_session


def _public_profile_from_model(
    bike: BikeProfileModel,
    *,
    has_repair_sessions: bool = False,
) -> BikeProfile:
    """Project the current resolved profile into the legacy public contract."""

    make = cast(str | None, _projection_or_legacy(bike, "identity.make", bike.make))
    model = cast(str | None, _projection_or_legacy(bike, "identity.model", bike.model))
    model_year = cast(
        int | None,
        _projection_or_legacy(bike, "identity.model_year", bike.model_year),
    )
    bike_type = cast(
        str | None,
        _projection_or_legacy(bike, "identity.bike_type", bike.bike_type),
    )
    frame_material = cast(
        str | None,
        _projection_or_legacy(bike, "frame.material", bike.frame_material),
    )
    drivetrain = cast(
        str | None,
        _projection_or_legacy(
            bike,
            "drivetrain.legacy_description",
            bike.drivetrain,
        ),
    )
    brake_type = _legacy_brake_type(bike)
    wheel_size = _matching_positioned_value(
        bike,
        "rolling_system.front.wheel.nominal_size",
        "rolling_system.rear.wheel.nominal_size",
        bike.wheel_size,
    )
    tire_size = _matching_positioned_value(
        bike,
        "rolling_system.front.tire.marked_size",
        "rolling_system.rear.tire.marked_size",
        bike.tire_size,
    )

    return BikeProfile(
        id=bike.id,
        user_id=bike.user_id,
        display_name=bike.display_name,
        has_repair_sessions=has_repair_sessions,
        make=make,
        model=model,
        model_year=model_year,
        bike_type=BikeType(bike_type or "unknown"),
        frame_material=FrameMaterial(frame_material or "unknown"),
        drivetrain=drivetrain,
        brake_type=BrakeType(brake_type) if brake_type is not None else None,
        wheel_size=wheel_size,
        tire_size=tire_size,
        notes=bike.notes,
        created_at=bike.created_at,
        updated_at=bike.updated_at,
    )


def _projection_or_legacy(
    bike: BikeProfileModel,
    field_path: str,
    legacy_value: str | int | None,
) -> str | int | None:
    if has_technical_value_path(bike.technical_profile, field_path):
        return technical_value(bike.technical_profile, field_path)
    return legacy_value


def _matching_positioned_value(
    bike: BikeProfileModel,
    front_path: str,
    rear_path: str,
    legacy_value: str | None,
) -> str | None:
    front_exists = has_technical_value_path(bike.technical_profile, front_path)
    rear_exists = has_technical_value_path(bike.technical_profile, rear_path)
    if not front_exists and not rear_exists:
        return legacy_value
    front = technical_value(bike.technical_profile, front_path)
    rear = technical_value(bike.technical_profile, rear_path)
    return front if front is not None and front == rear else None


def _legacy_brake_type(bike: BikeProfileModel) -> str | None:
    front_mechanism_path = "brakes.front.mechanism"
    rear_mechanism_path = "brakes.rear.mechanism"
    if not (
        has_technical_value_path(bike.technical_profile, front_mechanism_path)
        or has_technical_value_path(bike.technical_profile, rear_mechanism_path)
    ):
        return bike.brake_type
    front_mechanism = technical_value(bike.technical_profile, front_mechanism_path)
    rear_mechanism = technical_value(bike.technical_profile, rear_mechanism_path)
    front_actuation = technical_value(bike.technical_profile, "brakes.front.actuation")
    rear_actuation = technical_value(bike.technical_profile, "brakes.rear.actuation")
    if front_mechanism == rear_mechanism == "disc":
        if front_actuation == rear_actuation == "mechanical":
            return "mechanical_disc"
        if front_actuation == rear_actuation == "hydraulic":
            return "hydraulic_disc"
    if front_mechanism == rear_mechanism == "rim_other":
        return "rim"
    if has_technical_value_path(bike.technical_profile, "brakes.legacy_summary"):
        return technical_value(bike.technical_profile, "brakes.legacy_summary")
    return None
