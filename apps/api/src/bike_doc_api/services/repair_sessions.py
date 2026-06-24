"""Repair session service."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Awaitable, Callable
from typing import Protocol

from sqlalchemy.exc import IntegrityError

from bike_doc_api.core.errors import IdempotencyConflictError, NotFoundError
from bike_doc_api.models.bike import BikeProfile
from bike_doc_api.models.repair_session import RepairSession as RepairSessionModel
from bike_doc_api.models.user import User
from bike_doc_api.schemas.common import (
    RepairSessionPhase,
    RepairSessionStatus,
    SafetyState,
)
from bike_doc_api.schemas.repair_session import (
    RepairSession,
    RepairSessionCreate,
    repair_session_from_model,
)


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


class RepairSessionService:
    """Application-owned repair-session workflow behavior."""

    def __init__(
        self,
        bikes: BikeRepositoryProtocol,
        repair_sessions: RepairSessionRepositoryProtocol,
        *,
        rollback: Callable[[], Awaitable[None]] | None = None,
    ) -> None:
        self._bikes = bikes
        self._repair_sessions = repair_sessions
        self._rollback = rollback

    async def create_session(
        self,
        *,
        current_user: User,
        request: RepairSessionCreate,
    ) -> RepairSession:
        """Create or return an idempotent diagnostic repair session."""

        request_hash = _canonical_create_request_hash(request)
        if request.client_session_id is not None:
            existing = await self._repair_sessions.get_by_client_session_id(
                user_id=current_user.id,
                client_session_id=request.client_session_id,
            )
            if existing is not None:
                if existing.request_hash != request_hash:
                    raise IdempotencyConflictError()
                return repair_session_from_model(existing)

        bike = await self._bikes.get_owned_active(
            bike_id=request.bike_id,
            user_id=current_user.id,
        )
        if bike is None:
            raise NotFoundError()

        repair_session = RepairSessionModel(
            user_id=current_user.id,
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
            if request.client_session_id is None:
                raise
            raced_existing = await self._repair_sessions.get_by_client_session_id(
                user_id=current_user.id,
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


def _canonical_create_request_hash(request: RepairSessionCreate) -> str:
    """Return the canonical SHA-256 request hash for create idempotency."""

    canonical = json.dumps(
        {"bike_id": request.bike_id},
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
