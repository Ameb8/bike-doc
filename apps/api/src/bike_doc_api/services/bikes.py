"""Bike profile service."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Protocol

from bike_doc_api.core.errors import NotFoundError
from bike_doc_api.models.bike import BikeProfile as BikeProfileModel
from bike_doc_api.models.user import User
from bike_doc_api.schemas.bike import (
    BikeProfile,
    BikeProfileCreate,
    BikeProfileList,
    BikeProfilePatch,
    bike_profile_from_model,
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

    async def list_owned_active(
        self,
        user_id: str,
        *,
        limit: int = DEFAULT_BIKE_LIMIT,
    ) -> list[BikeProfileModel]:
        """Return non-deleted bike profiles for a user."""

    async def save(self, bike: BikeProfileModel) -> BikeProfileModel:
        """Persist changes to an existing bike profile."""

    async def soft_delete(self, bike: BikeProfileModel) -> BikeProfileModel:
        """Soft-delete a bike profile."""

    async def list_bike_ids_with_owned_repair_sessions(
        self,
        *,
        user_id: str,
        bike_ids: list[str],
    ) -> set[str]:
        """Return bike ids with one or more owned repair sessions."""


class BikeService:
    """Application-owned bike profile behavior."""

    def __init__(
        self,
        bikes: BikeRepositoryProtocol,
        *,
        commit: Callable[[], Awaitable[None]] | None = None,
    ) -> None:
        self._bikes = bikes
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
                bike_profile_from_model(
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
        if self._commit is not None:
            await self._commit()
        return bike_profile_from_model(created, has_repair_sessions=False)

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
        return bike_profile_from_model(
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

        bike = await self._get_owned_bike(current_user=current_user, bike_id=bike_id)
        for field_name in patch.model_fields_set:
            value = getattr(patch, field_name)
            if field_name in {"bike_type", "frame_material", "brake_type"}:
                setattr(bike, field_name, None if value is None else value.value)
                continue
            setattr(bike, field_name, value)

        updated = await self._bikes.save(bike)
        if self._commit is not None:
            await self._commit()
        bike_ids_with_repair_sessions = (
            await self._bikes.list_bike_ids_with_owned_repair_sessions(
                user_id=current_user.id,
                bike_ids=[updated.id],
            )
        )
        return bike_profile_from_model(
            updated,
            has_repair_sessions=updated.id in bike_ids_with_repair_sessions,
        )

    async def delete_bike(
        self,
        *,
        current_user: User,
        bike_id: str,
    ) -> None:
        """Soft-delete an owned bike profile."""

        bike = await self._get_owned_bike(current_user=current_user, bike_id=bike_id)
        await self._bikes.soft_delete(bike)
        if self._commit is not None:
            await self._commit()

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
