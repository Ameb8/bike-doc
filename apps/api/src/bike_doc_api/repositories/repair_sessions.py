"""Repair session repository."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bike_doc_api.models.repair_session import (
    RepairPhaseSession,
    RepairSession,
    RepairTurn,
)


class RepairSessionRepository:
    """Persistence operations for repair sessions."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, repair_session: RepairSession) -> RepairSession:
        """Add a repair session to the current transaction."""
        self._session.add(repair_session)
        await self._session.flush()
        return repair_session

    async def get(self, repair_session_id: str) -> RepairSession | None:
        """Return a repair session by ID."""
        return await self._session.get(RepairSession, repair_session_id)

    async def get_for_update(self, repair_session_id: str) -> RepairSession | None:
        """Return and lock a repair session row by ID."""
        result = await self._session.execute(
            select(RepairSession)
            .where(RepairSession.id == repair_session_id)
            .with_for_update(),
        )
        return result.scalar_one_or_none()

    async def get_owned(
        self,
        *,
        repair_session_id: str,
        user_id: str,
    ) -> RepairSession | None:
        """Return a repair session owned by a user."""
        result = await self._session.execute(
            select(RepairSession).where(
                RepairSession.id == repair_session_id,
                RepairSession.user_id == user_id,
            ),
        )
        return result.scalar_one_or_none()

    async def get_owned_for_update(
        self,
        *,
        repair_session_id: str,
        user_id: str,
    ) -> RepairSession | None:
        """Return and lock an owned repair session row."""
        result = await self._session.execute(
            select(RepairSession)
            .where(
                RepairSession.id == repair_session_id,
                RepairSession.user_id == user_id,
            )
            .with_for_update(),
        )
        return result.scalar_one_or_none()

    async def get_by_client_session_id(
        self,
        *,
        user_id: str,
        client_session_id: str,
    ) -> RepairSession | None:
        """Return a repair session by user-scoped idempotency key."""
        result = await self._session.execute(
            select(RepairSession).where(
                RepairSession.user_id == user_id,
                RepairSession.client_session_id == client_session_id,
            ),
        )
        return result.scalar_one_or_none()

    async def list_owned(
        self,
        user_id: str,
        *,
        status: str | None = None,
        limit: int = 50,
    ) -> list[RepairSession]:
        """Return repair sessions for a user."""
        statement = select(RepairSession).where(RepairSession.user_id == user_id)
        if status is not None:
            statement = statement.where(RepairSession.status == status)
        result = await self._session.execute(
            statement.order_by(
                RepairSession.created_at.desc(),
                RepairSession.id.desc(),
            ).limit(limit),
        )
        return list(result.scalars().all())


class RepairPhaseSessionRepository:
    """Persistence operations for phase-to-ADK-session mappings."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, phase_session: RepairPhaseSession) -> RepairPhaseSession:
        """Add a phase session to the current transaction."""
        self._session.add(phase_session)
        await self._session.flush()
        return phase_session

    async def get(self, phase_session_id: str) -> RepairPhaseSession | None:
        """Return a phase session by ID."""
        return await self._session.get(RepairPhaseSession, phase_session_id)

    async def get_for_session_phase(
        self,
        *,
        repair_session_id: str,
        phase: str,
    ) -> RepairPhaseSession | None:
        """Return a phase session for one repair session phase."""
        result = await self._session.execute(
            select(RepairPhaseSession).where(
                RepairPhaseSession.repair_session_id == repair_session_id,
                RepairPhaseSession.phase == phase,
            ),
        )
        return result.scalar_one_or_none()

    async def list_for_session(
        self,
        repair_session_id: str,
    ) -> list[RepairPhaseSession]:
        """Return phase sessions for a repair session."""
        result = await self._session.execute(
            select(RepairPhaseSession)
            .where(RepairPhaseSession.repair_session_id == repair_session_id)
            .order_by(RepairPhaseSession.created_at.asc(), RepairPhaseSession.id.asc()),
        )
        return list(result.scalars().all())


class RepairTurnRepository:
    """Persistence operations for accepted repair turns."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, turn: RepairTurn) -> RepairTurn:
        """Add a repair turn to the current transaction."""
        self._session.add(turn)
        await self._session.flush()
        return turn

    async def get(self, turn_id: str) -> RepairTurn | None:
        """Return a repair turn by ID."""
        return await self._session.get(RepairTurn, turn_id)

    async def get_by_client_turn_id(
        self,
        *,
        repair_session_id: str,
        client_turn_id: str,
    ) -> RepairTurn | None:
        """Return a repair turn by session-scoped idempotency key."""
        result = await self._session.execute(
            select(RepairTurn).where(
                RepairTurn.repair_session_id == repair_session_id,
                RepairTurn.client_turn_id == client_turn_id,
            ),
        )
        return result.scalar_one_or_none()

    async def list_for_session(
        self,
        repair_session_id: str,
        *,
        limit: int = 50,
    ) -> list[RepairTurn]:
        """Return turns for a repair session in creation order."""
        result = await self._session.execute(
            select(RepairTurn)
            .where(RepairTurn.repair_session_id == repair_session_id)
            .order_by(RepairTurn.created_at.asc(), RepairTurn.id.asc())
            .limit(limit),
        )
        return list(result.scalars().all())
