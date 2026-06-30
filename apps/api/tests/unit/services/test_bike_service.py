"""Bike service tests."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from bike_doc_api.core.errors import BikeRepairHistoryConflictError, NotFoundError
from bike_doc_api.models.bike import BikeProfile as BikeProfileModel
from bike_doc_api.models.user import User
from bike_doc_api.schemas.bike import BikeProfileCreate, BikeProfilePatch
from bike_doc_api.services.bikes import BikeService


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


def _user(user_id: str = "usr_owner") -> User:
    return User(
        id=user_id,
        auth_subject=f"auth|{user_id}",
        email=f"{user_id}@example.com",
        display_name=user_id,
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
    service = BikeService(repo)

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
    service = BikeService(repo)

    bikes = await service.list_bikes(current_user=_user())

    assert [bike.id for bike in bikes.items] == ["bike_new", "bike_old"]
    assert [bike.has_repair_sessions for bike in bikes.items] == [False, True]
    assert bikes.next_cursor is None


async def test_get_bike_requires_ownership() -> None:
    service = BikeService(FakeBikeRepository([_bike(user_id="usr_other")]))

    with pytest.raises(NotFoundError):
        await service.get_bike(current_user=_user(), bike_id="bike_owned")


async def test_get_bike_includes_repair_session_history_flag() -> None:
    bike = _bike()
    service = BikeService(
        FakeBikeRepository([bike], repair_session_bike_ids={bike.id}),
    )

    result = await service.get_bike(current_user=_user(), bike_id=bike.id)

    assert result.has_repair_sessions is True


async def test_update_bike_preserves_omitted_fields_and_clears_explicit_nulls() -> None:
    bike = _bike()
    service = BikeService(FakeBikeRepository([bike]))

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
    service = BikeService(FakeBikeRepository([bike]))

    updated = await service.update_bike(
        current_user=_user(),
        bike_id=bike.id,
        patch=BikeProfilePatch(model_year=None),
    )

    assert updated.model_year is None
    assert updated.display_name == "Commuter"


async def test_delete_bike_soft_deletes_profile() -> None:
    bike = _bike()
    service = BikeService(FakeBikeRepository([bike]))

    await service.delete_bike(current_user=_user(), bike_id=bike.id)

    assert bike.deleted_at is not None


async def test_delete_bike_conflicts_when_owned_history_exists() -> None:
    bike = _bike()
    service = BikeService(
        FakeBikeRepository([bike], repair_session_bike_ids={bike.id}),
    )

    with pytest.raises(BikeRepairHistoryConflictError):
        await service.delete_bike(current_user=_user(), bike_id=bike.id)

    assert bike.deleted_at is None


async def test_delete_bike_ignores_other_users_history() -> None:
    bike = _bike()
    service = BikeService(
        FakeBikeRepository(
            [bike],
            repair_session_bike_ids_by_user={"usr_other": {bike.id}},
        ),
    )

    await service.delete_bike(current_user=_user(), bike_id=bike.id)

    assert bike.deleted_at is not None


async def test_delete_bike_requires_ownership() -> None:
    service = BikeService(FakeBikeRepository([_bike(user_id="usr_other")]))

    with pytest.raises(NotFoundError):
        await service.delete_bike(current_user=_user(), bike_id="bike_owned")
