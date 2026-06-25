"""ADK session integration boundary."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Protocol

from sqlalchemy.exc import IntegrityError

from bike_doc_api.models._ids import generate_prefixed_ulid
from bike_doc_api.models.repair_session import (
    RepairPhaseSession as RepairPhaseSessionModel,
)
from bike_doc_api.schemas.common import RepairSessionPhase

logger = logging.getLogger(__name__)


class DiagnosticADKSessionClientProtocol(Protocol):
    """Opaque ADK session operations used by the app-owned phase mapping."""

    async def create_session(
        self,
        *,
        repair_session_id: str,
        phase: RepairSessionPhase,
    ) -> str:
        """Create a raw ADK session and return only its opaque internal ID."""

    async def close_session(self, *, adk_session_id: str) -> None:
        """Best-effort cleanup for an ADK session that lost a creation race."""


class RepairPhaseSessionRepositoryProtocol(Protocol):
    """Phase-session persistence operations required by ADK session wiring."""

    async def add(
        self,
        phase_session: RepairPhaseSessionModel,
    ) -> RepairPhaseSessionModel:
        """Add a phase-session row to the current transaction."""

    async def get_for_session_phase(
        self,
        *,
        repair_session_id: str,
        phase: str,
    ) -> RepairPhaseSessionModel | None:
        """Return one app-owned phase-session mapping."""


class LocalDiagnosticADKSessionClient:
    """Local opaque ADK session placeholder used until a live ADK adapter exists."""

    async def create_session(
        self,
        *,
        repair_session_id: str,
        phase: RepairSessionPhase,
    ) -> str:
        """Create an opaque local session ID without exposing it publicly."""

        _ = repair_session_id
        return f"adk_{phase.value}_{generate_prefixed_ulid('sess_')}"

    async def close_session(self, *, adk_session_id: str) -> None:
        """Discard a local session ID."""

        _ = adk_session_id


@dataclass(frozen=True, slots=True)
class DiagnosticPhaseSessionManager:
    """Create or resume one app-owned phase session per repair-session phase."""

    phase_sessions: RepairPhaseSessionRepositoryProtocol
    adk_sessions: DiagnosticADKSessionClientProtocol
    commit: Callable[[], Awaitable[None]] | None = None
    rollback: Callable[[], Awaitable[None]] | None = None

    async def ensure_diagnostic_session(
        self,
        *,
        repair_session_id: str,
    ) -> RepairPhaseSessionModel:
        """Ensure the diagnostic phase has one app-owned ADK session mapping."""

        return await self.ensure_phase_session(
            repair_session_id=repair_session_id,
            phase=RepairSessionPhase.DIAGNOSTIC,
        )

    async def ensure_phase_session(
        self,
        *,
        repair_session_id: str,
        phase: RepairSessionPhase,
    ) -> RepairPhaseSessionModel:
        """Create or resume the phase-scoped ADK session mapping."""

        existing = await self.phase_sessions.get_for_session_phase(
            repair_session_id=repair_session_id,
            phase=phase.value,
        )
        if existing is not None:
            return existing

        adk_session_id = await self.adk_sessions.create_session(
            repair_session_id=repair_session_id,
            phase=phase,
        )
        phase_session = RepairPhaseSessionModel(
            repair_session_id=repair_session_id,
            phase=phase.value,
            adk_session_id=adk_session_id,
            status="active",
        )

        try:
            created = await self.phase_sessions.add(phase_session)
            if self.commit is not None:
                await self.commit()
            return created
        except IntegrityError:
            await self._rollback_if_configured()
            await self._discard_orphaned_adk_session(adk_session_id)
            raced = await self.phase_sessions.get_for_session_phase(
                repair_session_id=repair_session_id,
                phase=phase.value,
            )
            if raced is None:
                raise
            return raced

    async def _discard_orphaned_adk_session(self, adk_session_id: str) -> None:
        """Close or discard an ADK session that lost a database insert race."""

        try:
            await self.adk_sessions.close_session(adk_session_id=adk_session_id)
        except Exception:
            logger.warning(
                "diagnostic_adk_session_cleanup_failed",
                extra={"adk_session_id": adk_session_id},
            )

    async def _rollback_if_configured(self) -> None:
        """Rollback the current unit of work when one is configured."""

        if self.rollback is not None:
            await self.rollback()
