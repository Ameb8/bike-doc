"""Persistence operations for profile-inference runs."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bike_doc_api.models.profile_inference import ProfileInferenceRun


class ProfileInferenceRunRepository:
    """Store and retrieve versioned inference runs."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, run: ProfileInferenceRun) -> ProfileInferenceRun:
        """Add a new run in the current transaction."""

        self._session.add(run)
        await self._session.flush()
        return run

    async def get_by_identity(
        self,
        *,
        turn_id: str,
        inference_schema_version: str,
        extractor_version: str,
    ) -> ProfileInferenceRun | None:
        """Return a run by its normal-processing idempotency tuple."""

        result = await self._session.execute(
            select(ProfileInferenceRun).where(
                ProfileInferenceRun.turn_id == turn_id,
                ProfileInferenceRun.inference_schema_version
                == inference_schema_version,
                ProfileInferenceRun.extractor_version == extractor_version,
            ),
        )
        return result.scalar_one_or_none()

    async def save(self, run: ProfileInferenceRun) -> ProfileInferenceRun:
        """Flush mutable run status fields."""

        await self._session.flush()
        return run
