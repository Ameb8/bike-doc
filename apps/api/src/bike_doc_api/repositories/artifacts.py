"""Artifact repository."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bike_doc_api.models.artifact import ArtifactRef


class ArtifactRepository:
    """Persistence operations for artifact metadata."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, artifact: ArtifactRef) -> ArtifactRef:
        """Add an artifact reference to the current transaction."""
        self._session.add(artifact)
        await self._session.flush()
        return artifact

    async def get(self, artifact_id: str) -> ArtifactRef | None:
        """Return an artifact reference by ID."""
        return await self._session.get(ArtifactRef, artifact_id)

    async def get_owned(
        self,
        *,
        artifact_id: str,
        user_id: str,
    ) -> ArtifactRef | None:
        """Return an artifact owned by a user."""
        result = await self._session.execute(
            select(ArtifactRef).where(
                ArtifactRef.id == artifact_id,
                ArtifactRef.user_id == user_id,
            ),
        )
        return result.scalar_one_or_none()

    async def get_by_client_artifact_id(
        self,
        *,
        user_id: str,
        client_artifact_id: str,
    ) -> ArtifactRef | None:
        """Return an artifact by user-scoped idempotency key."""
        result = await self._session.execute(
            select(ArtifactRef).where(
                ArtifactRef.user_id == user_id,
                ArtifactRef.client_artifact_id == client_artifact_id,
            ),
        )
        return result.scalar_one_or_none()

    async def list_for_repair_session(
        self,
        repair_session_id: str,
        *,
        limit: int = 50,
    ) -> list[ArtifactRef]:
        """Return artifacts associated with a repair session."""
        result = await self._session.execute(
            select(ArtifactRef)
            .where(ArtifactRef.repair_session_id == repair_session_id)
            .order_by(ArtifactRef.created_at.desc(), ArtifactRef.id.desc())
            .limit(limit),
        )
        return list(result.scalars().all())

    async def list_for_bike(
        self,
        bike_id: str,
        *,
        limit: int = 50,
    ) -> list[ArtifactRef]:
        """Return artifacts associated with a bike profile."""
        result = await self._session.execute(
            select(ArtifactRef)
            .where(ArtifactRef.bike_id == bike_id)
            .order_by(ArtifactRef.created_at.desc(), ArtifactRef.id.desc())
            .limit(limit),
        )
        return list(result.scalars().all())
