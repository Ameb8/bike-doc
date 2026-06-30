"""Repair session service."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Protocol

from sqlalchemy.exc import IntegrityError

from bike_doc_api.core.errors import (
    IdempotencyConflictError,
    NotFoundError,
    SessionStateConflictError,
    StaleSessionError,
)
from bike_doc_api.models.bike import BikeProfile
from bike_doc_api.models.repair_session import (
    RepairPhaseSession as RepairPhaseSessionModel,
)
from bike_doc_api.models.repair_session import RepairSession as RepairSessionModel
from bike_doc_api.models.user import User
from bike_doc_api.schemas.bike import BikeProfile as BikeProfileSchema
from bike_doc_api.schemas.bike import bike_profile_from_model
from bike_doc_api.schemas.common import (
    RepairSessionPhase,
    RepairSessionStatus,
    SafetyState,
)
from bike_doc_api.schemas.repair_session import (
    RepairSession,
    RepairSessionCreate,
    RepairSessionList,
    repair_session_from_model,
)

DEFAULT_REPAIR_SESSION_LIMIT = 20
MAX_REPAIR_SESSION_LIMIT = 100


class BikeRepositoryProtocol(Protocol):
    """Bike persistence operations required by repair-session workflows."""

    async def get_owned_active(
        self,
        *,
        bike_id: str,
        user_id: str,
    ) -> BikeProfile | None:
        """Return a non-deleted bike profile owned by a user."""


class RepairSessionRepositoryProtocol(Protocol):
    """Repair-session persistence operations required by the service."""

    async def add(self, repair_session: RepairSessionModel) -> RepairSessionModel:
        """Add a repair session to the current transaction."""

    async def get_owned(
        self,
        *,
        repair_session_id: str,
        user_id: str,
    ) -> RepairSessionModel | None:
        """Return a repair session owned by a user."""

    async def get_by_client_session_id(
        self,
        *,
        user_id: str,
        client_session_id: str,
    ) -> RepairSessionModel | None:
        """Return a repair session by user-scoped idempotency key."""

    async def list_owned(
        self,
        user_id: str,
        *,
        bike_id: str | None = None,
        status: str | None = None,
        limit: int = DEFAULT_REPAIR_SESSION_LIMIT,
    ) -> list[RepairSessionModel]:
        """Return repair sessions owned by a user."""


class RepairPhaseSessionRepositoryProtocol(Protocol):
    """Phase-session lookups required by diagnostic tool context checks."""

    async def get_for_session_phase(
        self,
        *,
        repair_session_id: str,
        phase: str,
    ) -> RepairPhaseSessionModel | None:
        """Return a phase session for one repair session phase."""


@dataclass(frozen=True, slots=True)
class DiagnosticBikeProfile:
    """Bike profile context attached to a diagnostic repair session."""

    bike_profile: BikeProfileSchema
    user_skill_level: str


@dataclass(frozen=True, slots=True)
class RepairHistoryEntry:
    """Prior repair-history entry returned to diagnostic tools."""

    id: str
    bike_id: str
    repair_session_id: str | None
    title: str
    summary: str
    components: list[str]
    parts_used: list[str]
    tools_used: list[str]
    mileage: int | None
    service_date: str | None
    created_at: str


@dataclass(frozen=True, slots=True)
class RepairHistoryLookup:
    """Repair-history lookup result for a diagnostic session bike."""

    entries: list[RepairHistoryEntry]


class RepairSessionService:
    """Application-owned repair-session workflow behavior."""

    def __init__(
        self,
        bikes: BikeRepositoryProtocol,
        repair_sessions: RepairSessionRepositoryProtocol,
        *,
        phase_sessions: RepairPhaseSessionRepositoryProtocol | None = None,
        rollback: Callable[[], Awaitable[None]] | None = None,
    ) -> None:
        self._bikes = bikes
        self._repair_sessions = repair_sessions
        self._phase_sessions = phase_sessions
        self._rollback = rollback

    async def list_sessions(
        self,
        *,
        current_user: User,
        bike_id: str,
        status: RepairSessionStatus | None = None,
        limit: int = DEFAULT_REPAIR_SESSION_LIMIT,
        cursor: str | None = None,
    ) -> RepairSessionList:
        """Return repair sessions for an owned bike, newest first."""

        _ = cursor
        bike = await self._bikes.get_owned_active(
            bike_id=bike_id,
            user_id=current_user.id,
        )
        if bike is None:
            raise NotFoundError()
        repair_sessions = await self._repair_sessions.list_owned(
            current_user.id,
            bike_id=bike.id,
            status=None if status is None else status.value,
            limit=limit,
        )
        return RepairSessionList(
            items=[
                repair_session_from_model(repair_session)
                for repair_session in repair_sessions
            ],
            next_cursor=None,
        )

    async def create_session(
        self,
        *,
        current_user: User,
        request: RepairSessionCreate,
    ) -> RepairSession:
        """Create or return an idempotent diagnostic repair session."""

        user_id = current_user.id
        request_hash = _canonical_create_request_hash(request)
        if request.client_session_id is not None:
            existing = await self._repair_sessions.get_by_client_session_id(
                user_id=user_id,
                client_session_id=request.client_session_id,
            )
            if existing is not None:
                if existing.request_hash != request_hash:
                    raise IdempotencyConflictError()
                return repair_session_from_model(existing)

        bike = await self._bikes.get_owned_active(
            bike_id=request.bike_id,
            user_id=user_id,
        )
        if bike is None:
            raise NotFoundError()

        repair_session = RepairSessionModel(
            user_id=user_id,
            bike_id=bike.id,
            client_session_id=request.client_session_id,
            request_hash=(
                request_hash if request.client_session_id is not None else None
            ),
            phase=RepairSessionPhase.DIAGNOSTIC.value,
            status=RepairSessionStatus.CREATED.value,
            safety_state=SafetyState.OK.value,
            current_input_request=None,
            execution_progress=None,
            active_safety_flags=[],
            latest_event_sequence=0,
        )

        try:
            created = await self._repair_sessions.add(repair_session)
        except IntegrityError as exc:
            if self._rollback is not None:
                await self._rollback()
            if request.client_session_id is None or not _is_unique_violation(exc):
                raise
            raced_existing = await self._repair_sessions.get_by_client_session_id(
                user_id=user_id,
                client_session_id=request.client_session_id,
            )
            if raced_existing is None:
                raise
            if raced_existing.request_hash != request_hash:
                raise IdempotencyConflictError() from exc
            created = raced_existing

        return repair_session_from_model(created)

    async def get_session(
        self,
        *,
        current_user: User,
        repair_session_id: str,
    ) -> RepairSession:
        """Return a public repair session owned by the current user."""

        repair_session = await self._repair_sessions.get_owned(
            repair_session_id=repair_session_id,
            user_id=current_user.id,
        )
        if repair_session is None:
            raise NotFoundError()
        return repair_session_from_model(repair_session)

    async def get_diagnostic_bike_profile(
        self,
        *,
        current_user: User,
        repair_session_id: str,
        diagnostic_session_id: str,
    ) -> DiagnosticBikeProfile:
        """Return the active diagnostic session's attached bike profile."""

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
            bike_profile=bike_profile_from_model(bike),
            user_skill_level=current_user.skill_level,
        )

    async def lookup_repair_history(
        self,
        *,
        current_user: User,
        repair_session_id: str,
        diagnostic_session_id: str,
        component_terms: list[str],
        limit: int,
    ) -> RepairHistoryLookup:
        """Return service-backed repair history for the session bike.

        Repair-history persistence is intentionally outside the diagnostic DB slice,
        so this method performs the ownership/phase checks and returns no entries.
        """

        await self._get_owned_diagnostic_session(
            current_user=current_user,
            repair_session_id=repair_session_id,
            diagnostic_session_id=diagnostic_session_id,
        )
        _ = component_terms
        _ = limit
        return RepairHistoryLookup(entries=[])

    async def verify_diagnostic_context(
        self,
        *,
        current_user: User,
        repair_session_id: str,
        diagnostic_session_id: str,
    ) -> None:
        """Verify ownership, active diagnostic phase, and phase-session identity."""

        await self._get_owned_diagnostic_session(
            current_user=current_user,
            repair_session_id=repair_session_id,
            diagnostic_session_id=diagnostic_session_id,
        )

    async def _get_owned_diagnostic_session(
        self,
        *,
        current_user: User,
        repair_session_id: str,
        diagnostic_session_id: str,
    ) -> RepairSessionModel:
        """Return an owned diagnostic session or raise a domain error."""

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


def _canonical_create_request_hash(request: RepairSessionCreate) -> str:
    """Return the canonical SHA-256 request hash for create idempotency."""

    canonical = json.dumps(
        {"bike_id": request.bike_id},
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _is_unique_violation(exc: IntegrityError) -> bool:
    """Return whether an integrity error came from a unique-constraint violation."""

    original = getattr(exc, "orig", None)
    sqlstate = getattr(original, "sqlstate", None) or getattr(original, "pgcode", None)
    return sqlstate == "23505"
