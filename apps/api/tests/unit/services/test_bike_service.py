"""Bike service tests."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from bike_doc_api.core.errors import BikeRepairHistoryConflictError, NotFoundError
from bike_doc_api.models.bike import (
    BikeFactClaim,
    BikeFieldResolution,
    empty_technical_profile,
)
from bike_doc_api.models.bike import (
    BikeProfile as BikeProfileModel,
)
from bike_doc_api.models.repair_session import RepairSession as RepairSessionModel
from bike_doc_api.models.user import User
from bike_doc_api.schemas.bike import BikeProfileCreate, BikeProfilePatch
from bike_doc_api.services.bikes import ResolvedBikeProfileService


class FakeBikeRepository:
    """In-memory bike repository for service tests."""

    def __init__(
        self,
        bikes: list[BikeProfileModel] | None = None,
        *,
        repair_session_bike_ids: set[str] | None = None,
        repair_session_bike_ids_by_user: dict[str, set[str]] | None = None,
    ) -> None:
        self.bikes = bikes or []
        self.claims: list[BikeFactClaim] = []
        self.resolutions: dict[tuple[str, str], BikeFieldResolution] = {}
        self.repair_session_bike_ids_by_user = repair_session_bike_ids_by_user or {
            "usr_owner": repair_session_bike_ids or set()
        }

    async def add(self, bike: BikeProfileModel) -> BikeProfileModel:
        if bike.id is None:
            bike.id = f"bike_{len(self.bikes) + 1}"
        timestamp = datetime(2026, 1, len(self.bikes) + 1, tzinfo=UTC)
        bike.created_at = timestamp
        bike.updated_at = timestamp
        self.bikes.append(bike)
        return bike

    async def get_owned_active(
        self,
        *,
        bike_id: str,
        user_id: str,
    ) -> BikeProfileModel | None:
        for bike in self.bikes:
            if (
                bike.id == bike_id
                and bike.user_id == user_id
                and bike.deleted_at is None
            ):
                return bike
        return None

    async def get_owned_active_for_update(
        self,
        *,
        bike_id: str,
        user_id: str,
    ) -> BikeProfileModel | None:
        return await self.get_owned_active(bike_id=bike_id, user_id=user_id)

    async def list_owned_active(
        self,
        user_id: str,
        *,
        limit: int = 50,
    ) -> list[BikeProfileModel]:
        bikes = [
            bike
            for bike in self.bikes
            if bike.user_id == user_id and bike.deleted_at is None
        ]
        bikes.sort(key=lambda bike: (bike.created_at, bike.id), reverse=True)
        return bikes[:limit]

    async def save(self, bike: BikeProfileModel) -> BikeProfileModel:
        bike.updated_at = datetime(2026, 2, 1, tzinfo=UTC)
        return bike

    async def add_claim(self, claim: BikeFactClaim) -> BikeFactClaim:
        claim.id = f"bfc_{len(self.claims) + 1}"
        self.claims.append(claim)
        return claim

    async def get_claim(self, claim_id: str) -> BikeFactClaim | None:
        return next((claim for claim in self.claims if claim.id == claim_id), None)

    async def get_resolution(
        self,
        *,
        bike_id: str,
        field_path: str,
    ) -> BikeFieldResolution | None:
        return self.resolutions.get((bike_id, field_path))

    async def save_resolution(
        self,
        resolution: BikeFieldResolution,
    ) -> BikeFieldResolution:
        self.resolutions[(resolution.bike_id, resolution.field_path)] = resolution
        return resolution

    async def list_resolutions(self, *, bike_id: str) -> list[BikeFieldResolution]:
        return [
            resolution
            for (resolution_bike_id, _), resolution in self.resolutions.items()
            if resolution_bike_id == bike_id
        ]

    async def list_bike_ids_with_owned_repair_sessions(
        self,
        *,
        user_id: str,
        bike_ids: list[str],
    ) -> set[str]:
        return {
            bike_id
            for bike_id in bike_ids
            if bike_id in self.repair_session_bike_ids_by_user.get(user_id, set())
        }

    async def soft_delete(self, bike: BikeProfileModel) -> BikeProfileModel:
        timestamp = datetime(2026, 3, 1, tzinfo=UTC)
        bike.deleted_at = timestamp
        bike.updated_at = timestamp
        return bike


class FakeDiagnosticRepairSessionRepository:
    """In-memory repair-session lookup for resolved-profile tests."""

    def __init__(self, repair_sessions: list[RepairSessionModel]) -> None:
        self.repair_sessions = repair_sessions

    async def get_owned(
        self,
        *,
        repair_session_id: str,
        user_id: str,
    ) -> RepairSessionModel | None:
        return next(
            (
                repair_session
                for repair_session in self.repair_sessions
                if repair_session.id == repair_session_id
                and repair_session.user_id == user_id
            ),
            None,
        )


def _user(user_id: str = "usr_owner", *, skill_level: str = "unknown") -> User:
    return User(
        id=user_id,
        auth_subject=f"auth|{user_id}",
        email=f"{user_id}@example.com",
        display_name=user_id,
        skill_level=skill_level,
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
    )


def _bike(
    *,
    bike_id: str = "bike_owned",
    user_id: str = "usr_owner",
    display_name: str = "Commuter",
    make: str | None = "Surly",
    model: str | None = "Straggler",
    model_year: int | None = 2021,
    notes: str | None = "Rear rack.",
    deleted_at: datetime | None = None,
    created_at: datetime | None = None,
) -> BikeProfileModel:
    return BikeProfileModel(
        id=bike_id,
        user_id=user_id,
        display_name=display_name,
        make=make,
        model=model,
        model_year=model_year,
        bike_type="gravel",
        frame_material="steel",
        drivetrain="Shimano 2x10",
        brake_type="mechanical_disc",
        wheel_size="700c",
        tire_size="700x38",
        notes=notes,
        deleted_at=deleted_at,
        created_at=created_at or datetime(2026, 1, 1, tzinfo=UTC),
        updated_at=datetime(2026, 1, 1, tzinfo=UTC),
    )


async def test_create_bike_uses_defaults_and_returns_public_profile() -> None:
    repo = FakeBikeRepository()
    service = ResolvedBikeProfileService(repo)

    bike = await service.create_bike(
        current_user=_user(),
        request=BikeProfileCreate(display_name="City Bike"),
    )

    assert bike.id == "bike_1"
    assert bike.user_id == "usr_owner"
    assert bike.display_name == "City Bike"
    assert bike.bike_type == "unknown"
    assert bike.frame_material == "unknown"
    assert bike.brake_type == "unknown"
    assert bike.has_repair_sessions is False


def test_empty_technical_profile_is_a_versioned_v2_projection() -> None:
    projection = empty_technical_profile()

    assert projection["schema_version"] == "bike_profile.v2"
    assert set(projection) >= {
        "identity",
        "frame",
        "brakes",
        "drivetrain",
        "rolling_system",
        "suspension",
        "cockpit",
        "seating",
        "electric_assist",
    }


async def test_list_bikes_returns_owned_active_profiles_only() -> None:
    repo = FakeBikeRepository(
        [
            _bike(bike_id="bike_old", created_at=datetime(2026, 1, 1, tzinfo=UTC)),
            _bike(bike_id="bike_new", created_at=datetime(2026, 1, 2, tzinfo=UTC)),
            _bike(bike_id="bike_other", user_id="usr_other"),
            _bike(
                bike_id="bike_deleted",
                deleted_at=datetime(2026, 1, 3, tzinfo=UTC),
            ),
        ],
        repair_session_bike_ids={"bike_old", "bike_other", "bike_deleted"},
    )
    service = ResolvedBikeProfileService(repo)

    bikes = await service.list_bikes(current_user=_user())

    assert [bike.id for bike in bikes.items] == ["bike_new", "bike_old"]
    assert [bike.has_repair_sessions for bike in bikes.items] == [False, True]
    assert bikes.next_cursor is None


async def test_get_bike_requires_ownership() -> None:
    service = ResolvedBikeProfileService(
        FakeBikeRepository([_bike(user_id="usr_other")]),
    )

    with pytest.raises(NotFoundError):
        await service.get_bike(current_user=_user(), bike_id="bike_owned")


async def test_get_bike_includes_repair_session_history_flag() -> None:
    bike = _bike()
    service = ResolvedBikeProfileService(
        FakeBikeRepository([bike], repair_session_bike_ids={bike.id}),
    )

    result = await service.get_bike(current_user=_user(), bike_id=bike.id)

    assert result.has_repair_sessions is True


async def test_update_bike_preserves_omitted_fields_and_clears_explicit_nulls() -> None:
    bike = _bike()
    service = ResolvedBikeProfileService(FakeBikeRepository([bike]))

    updated = await service.update_bike(
        current_user=_user(),
        bike_id=bike.id,
        patch=BikeProfilePatch(display_name="Updated", notes=None),
    )

    assert updated.display_name == "Updated"
    assert updated.notes is None
    assert updated.make == "Surly"
    assert updated.model == "Straggler"
    assert updated.model_year == 2021
    assert updated.has_repair_sessions is False


async def test_update_bike_can_clear_nullable_model_year() -> None:
    bike = _bike()
    service = ResolvedBikeProfileService(FakeBikeRepository([bike]))

    updated = await service.update_bike(
        current_user=_user(),
        bike_id=bike.id,
        patch=BikeProfilePatch(model_year=None),
    )

    assert updated.model_year is None
    assert updated.display_name == "Commuter"


async def test_manual_technical_patch_creates_claims_and_resolved_v2_leaves() -> None:
    bike = _bike()
    repo = FakeBikeRepository([bike])
    service = ResolvedBikeProfileService(repo)

    updated = await service.update_bike(
        current_user=_user(),
        bike_id=bike.id,
        patch=BikeProfilePatch(brake_type="hydraulic_disc"),
    )

    assert updated.brake_type == "hydraulic_disc"
    assert bike.profile_revision == 1
    assert [
        (claim.field_path, claim.source_type, claim.disposition)
        for claim in repo.claims
    ] == [
        ("brakes.front.mechanism", "manual_profile_edit", "applied"),
        ("brakes.front.actuation", "manual_profile_edit", "applied"),
        ("brakes.rear.mechanism", "manual_profile_edit", "applied"),
        ("brakes.rear.actuation", "manual_profile_edit", "applied"),
    ]
    rear = repo.resolutions[(bike.id, "brakes.rear.actuation")]
    assert rear.current_value == "hydraulic"
    assert rear.current_claim_id == repo.claims[-1].id
    assert rear.resolution_state == "resolved"


async def test_manual_clear_creates_a_barrier_and_changes_revision_once() -> None:
    bike = _bike()
    repo = FakeBikeRepository([bike])
    service = ResolvedBikeProfileService(repo)

    await service.update_bike(
        current_user=_user(),
        bike_id=bike.id,
        patch=BikeProfilePatch(model_year=None),
    )

    resolution = repo.resolutions[(bike.id, "identity.model_year")]
    assert bike.profile_revision == 1
    assert repo.claims[0].source_type == "manual_profile_clear"
    assert resolution.resolution_state == "cleared"
    assert resolution.current_value is None
    assert resolution.manual_clear_barrier_at is not None


async def test_internal_v2_manual_write_and_clear_use_the_same_claim_ledger() -> None:
    bike = _bike()
    repo = FakeBikeRepository([bike])
    service = ResolvedBikeProfileService(repo)

    await service.set_manual_technical_value(
        current_user=_user(),
        bike_id=bike.id,
        field_path="brakes.rear.mechanism",
        value="disc",
    )

    resolution = repo.resolutions[(bike.id, "brakes.rear.mechanism")]
    assert bike.technical_profile["brakes"]["rear"]["mechanism"] == "disc"
    assert resolution.current_claim_id == repo.claims[0].id
    assert repo.claims[0].source_type == "manual_profile_edit"
    assert bike.profile_revision == 1

    await service.set_manual_technical_value(
        current_user=_user(),
        bike_id=bike.id,
        field_path="brakes.rear.mechanism",
        value=None,
    )

    assert repo.claims[-1].source_type == "manual_profile_clear"
    assert resolution.resolution_state == "cleared"
    assert resolution.manual_clear_barrier_at is not None
    assert bike.profile_revision == 2


async def test_duplicate_manual_evidence_does_not_change_profile_revision() -> None:
    bike = _bike()
    repo = FakeBikeRepository([bike])
    service = ResolvedBikeProfileService(repo)

    await service.update_bike(
        current_user=_user(),
        bike_id=bike.id,
        patch=BikeProfilePatch(make="Surly"),
    )
    first_revision = bike.profile_revision
    first_updated_at = bike.updated_at

    await service.update_bike(
        current_user=_user(),
        bike_id=bike.id,
        patch=BikeProfilePatch(make="Surly"),
    )

    assert bike.profile_revision == first_revision
    assert bike.updated_at == first_updated_at
    assert repo.claims[-1].disposition == "supporting"


async def test_display_name_and_notes_remain_outside_the_claim_ledger() -> None:
    bike = _bike()
    repo = FakeBikeRepository([bike])
    service = ResolvedBikeProfileService(repo)

    await service.update_bike(
        current_user=_user(),
        bike_id=bike.id,
        patch=BikeProfilePatch(display_name="Updated", notes="New note"),
    )

    assert bike.display_name == "Updated"
    assert bike.notes == "New note"
    assert repo.claims == []
    assert bike.profile_revision is None


async def test_legacy_read_hides_mixed_positioned_brakes() -> None:
    bike = _bike()
    bike.technical_profile = {
        "brakes": {
            "front": {"mechanism": "disc", "actuation": "mechanical"},
            "rear": {"mechanism": "disc", "actuation": "hydraulic"},
        },
    }
    service = ResolvedBikeProfileService(FakeBikeRepository([bike]))

    result = await service.get_bike(current_user=_user(), bike_id=bike.id)

    assert result.brake_type is None


async def test_legacy_read_preserves_coaster_and_explicit_unknown_brake_values() -> (
    None
):
    bike = _bike()
    bike.technical_profile = {
        "schema_version": "bike_profile.v2",
        "brakes": {
            "front": {"mechanism": None, "actuation": None},
            "rear": {"mechanism": "coaster", "actuation": "none"},
        },
    }
    service = ResolvedBikeProfileService(FakeBikeRepository([bike]))

    coaster = await service.get_bike(current_user=_user(), bike_id=bike.id)
    assert coaster.brake_type == "coaster"

    bike.technical_profile = {
        "schema_version": "bike_profile.v2",
        "brakes": {
            "front": {"mechanism": None, "actuation": None},
            "rear": {"mechanism": None, "actuation": None},
        },
    }
    unknown = await service.get_bike(current_user=_user(), bike_id=bike.id)
    assert unknown.brake_type == "unknown"


async def test_delete_bike_soft_deletes_profile() -> None:
    bike = _bike()
    service = ResolvedBikeProfileService(FakeBikeRepository([bike]))

    await service.delete_bike(current_user=_user(), bike_id=bike.id)

    assert bike.deleted_at is not None


async def test_delete_bike_conflicts_when_owned_history_exists() -> None:
    bike = _bike()
    service = ResolvedBikeProfileService(
        FakeBikeRepository([bike], repair_session_bike_ids={bike.id}),
    )

    with pytest.raises(BikeRepairHistoryConflictError):
        await service.delete_bike(current_user=_user(), bike_id=bike.id)

    assert bike.deleted_at is None


async def test_delete_bike_ignores_other_users_history() -> None:
    bike = _bike()
    service = ResolvedBikeProfileService(
        FakeBikeRepository(
            [bike],
            repair_session_bike_ids_by_user={"usr_other": {bike.id}},
        ),
    )

    await service.delete_bike(current_user=_user(), bike_id=bike.id)

    assert bike.deleted_at is not None


async def test_delete_bike_requires_ownership() -> None:
    service = ResolvedBikeProfileService(
        FakeBikeRepository([_bike(user_id="usr_other")]),
    )

    with pytest.raises(NotFoundError):
        await service.delete_bike(current_user=_user(), bike_id="bike_owned")


async def test_diagnostic_profile_read_uses_the_resolved_profile_projection() -> None:
    bike = _bike()
    repair_session = RepairSessionModel(
        id="rs_diagnostic",
        user_id=bike.user_id,
        bike_id=bike.id,
        phase="diagnostic",
        status="running",
        safety_state="ok",
        active_safety_flags=[],
        latest_event_sequence=0,
    )
    service = ResolvedBikeProfileService(
        FakeBikeRepository([bike]),
        repair_sessions=FakeDiagnosticRepairSessionRepository([repair_session]),
    )

    result = await service.get_diagnostic_bike_profile(
        current_user=_user(skill_level="beginner"),
        repair_session_id=repair_session.id,
        diagnostic_session_id="phs_diagnostic",
    )

    assert result.bike_profile.id == bike.id
    assert result.bike_profile.drivetrain == "Shimano 2x10"
    assert result.bike_profile.brake_type == "mechanical_disc"
    assert result.bike_profile.schema_version == "bike_profile.v2"
    assert result.bike_profile.profile["profile_revision"] == 0
    assert result.bike_profile.field_states == {}
    assert result.user_skill_level == "beginner"
