"""ADK session integration boundary."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

import structlog
from google.adk.errors.already_exists_error import AlreadyExistsError
from google.adk.sessions import BaseSessionService
from sqlalchemy.exc import IntegrityError

from bike_doc_api.core.config import DiagnosticReportVersion
from bike_doc_api.models._ids import generate_prefixed_ulid
from bike_doc_api.models.repair_session import (
    RepairPhaseSession as RepairPhaseSessionModel,
)
from bike_doc_api.schemas.common import RepairSessionPhase

logger = structlog.get_logger(__name__)

DIAGNOSTIC_ADK_APP_NAME = "bike_doc_diagnostic"
DIAGNOSTIC_ADK_USER_ID = "backend_system"


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


class StaleInMemoryADKSessionError(Exception):
    """Recoverable confirmed missing bound ADK session state."""


class PhaseSessionBindingRepositoryProtocol(Protocol):
    """Null-only binding in the executor-owned product transaction."""

    async def bind_adk_session_if_null(
        self, *, phase_session_id: str, adk_session_id: str
    ) -> str:
        """Bind an uninitialized row or return the concurrent winner."""


class DiagnosticADKSessionClient:
    """ADK session client backed by the shared process-lifetime service."""

    def __init__(self, session_service: BaseSessionService) -> None:
        self._session_service = session_service

    @property
    def session_service(self) -> BaseSessionService:
        """Return the shared ADK session service for dependency lifecycle tests."""

        return self._session_service

    async def create_session(
        self,
        *,
        repair_session_id: str,
        phase: RepairSessionPhase,
    ) -> str:
        """Create a raw ADK session and return only its opaque internal ID."""

        adk_session_id = f"adk_{phase.value}_{generate_prefixed_ulid('sess_')}"
        await self._session_service.create_session(
            app_name=DIAGNOSTIC_ADK_APP_NAME,
            user_id=DIAGNOSTIC_ADK_USER_ID,
            session_id=adk_session_id,
            state={
                "repair_session_id": repair_session_id,
                "phase": phase.value,
            },
        )
        return adk_session_id

    async def ensure_unbound_session(
        self, *, phase_session_id: str, repair_session_id: str
    ) -> str:
        """Resume deterministic initialization after loss before app binding."""
        identity = f"adk_diagnostic_{phase_session_id}"
        existing = await self._session_service.get_session(
            app_name=DIAGNOSTIC_ADK_APP_NAME,
            user_id=DIAGNOSTIC_ADK_USER_ID,
            session_id=identity,
        )
        if existing is None:
            try:
                await self._session_service.create_session(
                    app_name=DIAGNOSTIC_ADK_APP_NAME,
                    user_id=DIAGNOSTIC_ADK_USER_ID,
                    session_id=identity,
                    state={
                        "repair_session_id": repair_session_id,
                        "phase": "diagnostic",
                    },
                )
            except (AlreadyExistsError, IntegrityError):
                # ADK owns and rolls back its transaction. Only a confirmed
                # durable winner makes a concurrent creation failure safe.
                await ensure_adk_session_available(
                    self._session_service, adk_session_id=identity
                )
        return identity

    async def ensure_or_resume(
        self,
        *,
        phase_session: RepairPhaseSessionModel,
        phase_sessions: PhaseSessionBindingRepositoryProtocol,
    ) -> str:
        """Initialize before effects in the executor's caller-owned transaction.

        ADK creation commits independently. A lost app commit can safely repeat
        deterministic ensure; a non-null binding is always resumed exactly.
        """
        if phase_session.adk_session_id is not None:
            identity = phase_session.adk_session_id
        else:
            proposed = await self.ensure_unbound_session(
                phase_session_id=phase_session.id,
                repair_session_id=phase_session.repair_session_id,
            )
            identity = await phase_sessions.bind_adk_session_if_null(
                phase_session_id=phase_session.id, adk_session_id=proposed
            )
        await ensure_adk_session_available(
            self._session_service, adk_session_id=identity
        )
        return identity

    async def close_session(self, *, adk_session_id: str) -> None:
        """Best-effort cleanup for an ADK session that lost a creation race."""

        delete_session = getattr(self._session_service, "delete_session", None)
        if delete_session is None:
            return

        try:
            await delete_session(
                app_name=DIAGNOSTIC_ADK_APP_NAME,
                user_id=DIAGNOSTIC_ADK_USER_ID,
                session_id=adk_session_id,
            )
        except Exception:
            logger.warning("diagnostic_adk_session_cleanup_failed")


async def ensure_adk_session_available(
    session_service: BaseSessionService,
    *,
    adk_session_id: str,
) -> None:
    """Raise a recoverable error if an already-bound ADK session is missing."""

    session = await session_service.get_session(
        app_name=DIAGNOSTIC_ADK_APP_NAME,
        user_id=DIAGNOSTIC_ADK_USER_ID,
        session_id=adk_session_id,
    )
    if session is None:
        raise StaleInMemoryADKSessionError()


@dataclass(frozen=True, slots=True)
class DiagnosticPhaseSessionManager:
    """Create or resume one app-owned phase session per repair-session phase."""

    phase_sessions: RepairPhaseSessionRepositoryProtocol
    diagnostic_report_version: DiagnosticReportVersion = "diagnostic_report.v2"

    async def ensure_diagnostic_session(
        self,
        *,
        repair_session_id: str,
    ) -> RepairPhaseSessionModel:
        """Ensure the diagnostic phase has one app-owned reference."""

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
        """Create or reuse the app reference within the acceptance transaction."""

        existing = await self.phase_sessions.get_for_session_phase(
            repair_session_id=repair_session_id,
            phase=phase.value,
        )
        if existing is not None:
            return existing

        phase_session = RepairPhaseSessionModel(
            repair_session_id=repair_session_id,
            phase=phase.value,
            adk_session_id=None,
            diagnostic_report_schema_version=(
                self.diagnostic_report_version
                if phase is RepairSessionPhase.DIAGNOSTIC
                else None
            ),
            status="active",
        )

        # Acceptance holds the repair-session lock and owns commit/rollback.
        return await self.phase_sessions.add(phase_session)
