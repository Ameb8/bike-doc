"""Bike repository."""

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bike_doc_api.models.bike import BikeFactClaim, BikeFieldResolution, BikeProfile
from bike_doc_api.models.repair_session import RepairSession


class BikeRepository:
    """Persistence operations for bike profiles."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, bike: BikeProfile) -> BikeProfile:
        """Add a bike profile to the current transaction."""
        self._session.add(bike)
        await self._session.flush()
        return bike

    async def get(self, bike_id: str) -> BikeProfile | None:
        """Return a bike profile by ID."""
        return await self._session.get(BikeProfile, bike_id)

    async def get_owned_active(
        self,
        *,
        bike_id: str,
        user_id: str,
    ) -> BikeProfile | None:
        """Return a non-deleted bike profile owned by a user."""
        result = await self._session.execute(
            select(BikeProfile).where(
                BikeProfile.id == bike_id,
                BikeProfile.user_id == user_id,
                BikeProfile.deleted_at.is_(None),
            ),
        )
        return result.scalar_one_or_none()

    async def get_owned_active_for_update(
        self,
        *,
        bike_id: str,
        user_id: str,
    ) -> BikeProfile | None:
        """Lock one owned active profile for claim resolution."""
        result = await self._session.execute(
            select(BikeProfile)
            .where(
                BikeProfile.id == bike_id,
                BikeProfile.user_id == user_id,
                BikeProfile.deleted_at.is_(None),
            )
            .with_for_update(),
        )
        return result.scalar_one_or_none()

    async def list_owned_active(
        self,
        user_id: str,
        *,
        limit: int = 50,
    ) -> list[BikeProfile]:
        """Return non-deleted bike profiles for a user."""
        result = await self._session.execute(
            select(BikeProfile)
            .where(BikeProfile.user_id == user_id, BikeProfile.deleted_at.is_(None))
            .order_by(BikeProfile.created_at.desc(), BikeProfile.id.desc())
            .limit(limit),
        )
        return list(result.scalars().all())

    async def save(self, bike: BikeProfile) -> BikeProfile:
        """Flush mutations for an existing bike profile."""

        bike.updated_at = datetime.now(UTC)
        await self._session.flush()
        return bike

    async def add_claim(self, claim: BikeFactClaim) -> BikeFactClaim:
        """Append an immutable bike fact claim to the active transaction."""
        self._session.add(claim)
        await self._session.flush()
        return claim

    async def get_claim(self, claim_id: str) -> BikeFactClaim | None:
        """Return a claim so a later resolution may update its disposition."""
        return await self._session.get(BikeFactClaim, claim_id)

    async def list_claims(
        self,
        *,
        bike_id: str,
        field_path: str | None = None,
    ) -> list[BikeFactClaim]:
        """Return immutable claim history used for replay-safe resolution."""
        statement = select(BikeFactClaim).where(BikeFactClaim.bike_id == bike_id)
        if field_path is not None:
            statement = statement.where(BikeFactClaim.field_path == field_path)
        result = await self._session.execute(
            statement.order_by(BikeFactClaim.created_at, BikeFactClaim.id),
        )
        return list(result.scalars().all())

    async def get_resolution(
        self,
        *,
        bike_id: str,
        field_path: str,
    ) -> BikeFieldResolution | None:
        """Return one field's latest resolution under the profile lock."""
        return await self._session.get(
            BikeFieldResolution,
            {"bike_id": bike_id, "field_path": field_path},
        )

    async def save_resolution(
        self,
        resolution: BikeFieldResolution,
    ) -> BikeFieldResolution:
        """Insert or flush a current field resolution."""
        self._session.add(resolution)
        await self._session.flush()
        return resolution

    async def list_resolutions(self, *, bike_id: str) -> list[BikeFieldResolution]:
        """Return compact resolution metadata for an internal profile read."""
        result = await self._session.execute(
            select(BikeFieldResolution)
            .where(BikeFieldResolution.bike_id == bike_id)
            .order_by(BikeFieldResolution.field_path),
        )
        return list(result.scalars().all())

    async def list_bike_ids_with_owned_repair_sessions(
        self,
        *,
        user_id: str,
        bike_ids: list[str],
    ) -> set[str]:
        """Return bike ids with one or more owned repair sessions."""

        if not bike_ids:
            return set()

        result = await self._session.execute(
            select(RepairSession.bike_id)
            .where(
                RepairSession.user_id == user_id,
                RepairSession.bike_id.in_(bike_ids),
            )
            .distinct(),
        )
        return set(result.scalars().all())

    async def soft_delete(self, bike: BikeProfile) -> BikeProfile:
        """Soft-delete a bike profile."""

        timestamp = datetime.now(UTC)
        bike.deleted_at = timestamp
        bike.updated_at = timestamp
        await self._session.flush()
        return bike
