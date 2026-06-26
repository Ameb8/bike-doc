"""Diagnostic turn acceptance service."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Awaitable, Callable
from copy import copy
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol

from pydantic import ValidationError as PydanticValidationError
from sqlalchemy.exc import IntegrityError

from bike_doc_api.adk.sessions import (
    DiagnosticPhaseSessionManager,
    LocalDiagnosticADKSessionClient,
)
from bike_doc_api.core.errors import (
    IdempotencyConflictError,
    NotFoundError,
    ServerError,
    SessionStateConflictError,
    StaleSessionError,
    ValidationAppError,
)
from bike_doc_api.models._ids import generate_prefixed_ulid
from bike_doc_api.models.artifact import ArtifactRef as ArtifactRefModel
from bike_doc_api.models.event import RepairSessionEvent as RepairSessionEventModel
from bike_doc_api.models.repair_session import (
    RepairPhaseSession as RepairPhaseSessionModel,
)
from bike_doc_api.models.repair_session import (
    RepairSession as RepairSessionModel,
)
from bike_doc_api.models.repair_session import RepairTurn as RepairTurnModel
from bike_doc_api.models.user import User
from bike_doc_api.schemas.common import RepairSessionPhase, RepairSessionStatus
from bike_doc_api.schemas.event import (
    RepairSessionEventType,
    validate_repair_session_event_data,
)
from bike_doc_api.schemas.repair_session import (
    InputChoice,
    InputRequest,
    InputRequestType,
)
from bike_doc_api.schemas.turn import (
    TurnAccepted,
    TurnCreate,
    turn_accepted_from_model,
)

ACCEPTING_DIAGNOSTIC_STATUSES = frozenset(
    {
        RepairSessionStatus.CREATED.value,
        RepairSessionStatus.AWAITING_USER.value,
    },
)


@dataclass(frozen=True, slots=True)
class DiagnosticInputRequestResult:
    """Persisted diagnostic input-request event metadata."""

    input_request: InputRequest
    event_id: str
    event_sequence: int


class RepairSessionRepositoryProtocol(Protocol):
    """Repair-session persistence operations required by turn acceptance."""

    async def get_owned(
        self,
        *,
        repair_session_id: str,
        user_id: str,
    ) -> RepairSessionModel | None:
        """Return a repair session owned by a user."""

    async def get_owned_for_update(
        self,
        *,
        repair_session_id: str,
        user_id: str,
    ) -> RepairSessionModel | None:
        """Return and lock an owned repair session row."""


class RepairPhaseSessionRepositoryProtocol(Protocol):
    """Phase-session persistence operations required by turn acceptance."""

    async def add(
        self,
        phase_session: RepairPhaseSessionModel,
    ) -> RepairPhaseSessionModel:
        """Add a phase session to the current transaction."""

    async def get_for_session_phase(
        self,
        *,
        repair_session_id: str,
        phase: str,
    ) -> RepairPhaseSessionModel | None:
        """Return a phase session for one repair session phase."""


class RepairTurnRepositoryProtocol(Protocol):
    """Repair-turn persistence operations required by turn acceptance."""

    async def add(self, turn: RepairTurnModel) -> RepairTurnModel:
        """Add a repair turn to the current transaction."""

    async def get_by_client_turn_id(
        self,
        *,
        repair_session_id: str,
        client_turn_id: str,
    ) -> RepairTurnModel | None:
        """Return a repair turn by session-scoped idempotency key."""


class RepairSessionEventRepositoryProtocol(Protocol):
    """Event persistence operations required by turn acceptance."""

    async def add(
        self,
        event: RepairSessionEventModel,
    ) -> RepairSessionEventModel:
        """Add an event with an already allocated sequence."""


class ArtifactRepositoryProtocol(Protocol):
    """Artifact lookups required by turn acceptance."""

    async def get_owned(
        self,
        *,
        artifact_id: str,
        user_id: str,
    ) -> ArtifactRefModel | None:
        """Return an artifact owned by a user."""


class DiagnosticTurnOrchestratorProtocol(Protocol):
    """Accepted-turn orchestration boundary."""

    async def process_turn(
        self,
        *,
        current_user: User,
        turn: RepairTurnModel,
    ) -> None:
        """Process an accepted diagnostic turn."""


class TurnService:
    """Application-owned user-turn acceptance behavior."""

    def __init__(
        self,
        repair_sessions: RepairSessionRepositoryProtocol,
        phase_sessions: RepairPhaseSessionRepositoryProtocol,
        turns: RepairTurnRepositoryProtocol,
        events: RepairSessionEventRepositoryProtocol,
        artifacts: ArtifactRepositoryProtocol,
        *,
        commit: Callable[[], Awaitable[None]] | None = None,
        rollback: Callable[[], Awaitable[None]] | None = None,
        phase_session_manager: DiagnosticPhaseSessionManager | None = None,
        orchestrator: DiagnosticTurnOrchestratorProtocol | None = None,
    ) -> None:
        self._repair_sessions = repair_sessions
        self._phase_sessions = phase_sessions
        self._turns = turns
        self._events = events
        self._artifacts = artifacts
        self._commit = commit
        self._rollback = rollback
        self._phase_session_manager = phase_session_manager or (
            DiagnosticPhaseSessionManager(
                phase_sessions=phase_sessions,
                adk_sessions=LocalDiagnosticADKSessionClient(),
                commit=commit,
                rollback=rollback,
            )
        )
        self._orchestrator = orchestrator
        self._last_acceptance_was_idempotent_replay = False

    @property
    def last_acceptance_was_idempotent_replay(self) -> bool:
        """Return whether the latest acceptance returned an existing turn."""

        return self._last_acceptance_was_idempotent_replay

    async def accept_turn(
        self,
        *,
        current_user: User,
        repair_session_id: str,
        request: TurnCreate,
    ) -> TurnAccepted:
        """Accept a diagnostic user turn and persist durable replay events."""

        self._last_acceptance_was_idempotent_replay = False
        request_hash = _canonical_turn_request_hash(request)
        preflight_session = await self._repair_sessions.get_owned(
            repair_session_id=repair_session_id,
            user_id=current_user.id,
        )
        if preflight_session is None:
            raise NotFoundError()
        existing = await self._turns.get_by_client_turn_id(
            repair_session_id=preflight_session.id,
            client_turn_id=request.client_turn_id,
        )
        if existing is not None:
            if existing.request_hash != request_hash:
                raise IdempotencyConflictError()
            self._last_acceptance_was_idempotent_replay = True
            return turn_accepted_from_model(
                existing,
                _turn_acceptance_session_snapshot(preflight_session, existing),
            )

        _validate_accepting_diagnostic_turns(preflight_session)
        _validate_input_request(preflight_session, request)

        phase_session = await self._ensure_diagnostic_phase_session(
            repair_session_id=repair_session_id,
        )

        try:
            repair_session = await self._repair_sessions.get_owned_for_update(
                repair_session_id=repair_session_id,
                user_id=current_user.id,
            )
            if repair_session is None:
                raise NotFoundError()

            existing = await self._turns.get_by_client_turn_id(
                repair_session_id=repair_session.id,
                client_turn_id=request.client_turn_id,
            )
            if existing is not None:
                if existing.request_hash != request_hash:
                    raise IdempotencyConflictError()
                self._last_acceptance_was_idempotent_replay = True
                return turn_accepted_from_model(
                    existing,
                    _turn_acceptance_session_snapshot(repair_session, existing),
                )

            _validate_accepting_diagnostic_turns(repair_session)
            _validate_input_request(repair_session, request)
            await self._validate_artifacts(
                current_user=current_user,
                repair_session=repair_session,
                artifact_ids=request.message.artifact_ids,
            )

            turn = await self._create_turn_started(
                repair_session=repair_session,
                phase_session=phase_session,
                request=request,
                request_hash=request_hash,
            )
            if self._commit is not None:
                await self._commit()
        except (IdempotencyConflictError, NotFoundError, SessionStateConflictError):
            await self._rollback_if_configured()
            raise
        except (PydanticValidationError, ValueError) as exc:
            await self._rollback_if_configured()
            raise ValidationAppError() from exc
        except IntegrityError as exc:
            await self._rollback_if_configured()
            return await self._handle_turn_integrity_race(
                current_user=current_user,
                repair_session_id=repair_session_id,
                client_turn_id=request.client_turn_id,
                request_hash=request_hash,
                error=exc,
            )
        except Exception as exc:
            await self._rollback_if_configured()
            raise ServerError() from exc

        if self._orchestrator is not None:
            try:
                await self._orchestrator.process_turn(
                    current_user=current_user,
                    turn=turn,
                )
            except Exception as exc:
                await self._rollback_if_configured()
                raise ServerError() from exc

        return turn_accepted_from_model(turn, repair_session)

    async def request_diagnostic_input(
        self,
        *,
        current_user: User,
        repair_session_id: str,
        diagnostic_session_id: str,
        request_type: InputRequestType,
        prompt: str,
        required: bool,
        accepted_media_types: list[str],
        choices: list[InputChoice],
        min_artifacts: int | None,
        max_artifacts: int | None,
    ) -> DiagnosticInputRequestResult:
        """Persist an app-owned diagnostic input request and event."""

        try:
            repair_session = await self._repair_sessions.get_owned_for_update(
                repair_session_id=repair_session_id,
                user_id=current_user.id,
            )
            if repair_session is None:
                raise NotFoundError()
            _validate_diagnostic_tool_session(repair_session)
            phase_session = await self._phase_sessions.get_for_session_phase(
                repair_session_id=repair_session.id,
                phase=RepairSessionPhase.DIAGNOSTIC.value,
            )
            if phase_session is None or phase_session.id != diagnostic_session_id:
                raise StaleSessionError()

            input_request = InputRequest(
                id=generate_prefixed_ulid("req_"),
                type=request_type,
                prompt=prompt,
                required=required,
                accepted_media_types=accepted_media_types,
                choices=choices,
                min_artifacts=min_artifacts,
                max_artifacts=max_artifacts,
                created_at=datetime.now(UTC),
            )
            sequence = repair_session.latest_event_sequence + 1
            event_data = validate_repair_session_event_data(
                RepairSessionEventType.INPUT_REQUESTED,
                {"input_request": input_request.model_dump(mode="json")},
            )
            event = await self._events.add(
                RepairSessionEventModel(
                    repair_session_id=repair_session.id,
                    sequence=sequence,
                    type=RepairSessionEventType.INPUT_REQUESTED.value,
                    data=event_data,
                ),
            )
            repair_session.current_input_request = input_request.model_dump(
                mode="json",
            )
            repair_session.status = RepairSessionStatus.AWAITING_USER.value
            repair_session.latest_event_sequence = sequence
            repair_session.updated_at = datetime.now(UTC)
            if self._commit is not None:
                await self._commit()
        except (NotFoundError, SessionStateConflictError, StaleSessionError):
            await self._rollback_if_configured()
            raise
        except (PydanticValidationError, ValueError) as exc:
            await self._rollback_if_configured()
            raise ValidationAppError() from exc
        except Exception as exc:
            await self._rollback_if_configured()
            raise ServerError() from exc

        return DiagnosticInputRequestResult(
            input_request=input_request,
            event_id=event.id,
            event_sequence=event.sequence,
        )

    async def _ensure_diagnostic_phase_session(
        self,
        *,
        repair_session_id: str,
    ) -> RepairPhaseSessionModel:
        """Ensure the required phase-session row exists through ADK boundary."""

        return await self._phase_session_manager.ensure_diagnostic_session(
            repair_session_id=repair_session_id,
        )

    async def _create_turn_started(
        self,
        *,
        repair_session: RepairSessionModel,
        phase_session: RepairPhaseSessionModel,
        request: TurnCreate,
        request_hash: str,
    ) -> RepairTurnModel:
        """Create the repair_turns row and its turn.started event together."""

        start_sequence = repair_session.latest_event_sequence + 1
        turn = await self._turns.add(
            RepairTurnModel(
                repair_session_id=repair_session.id,
                repair_phase_session_id=phase_session.id,
                client_turn_id=request.client_turn_id,
                request_hash=request_hash,
                schema_version=request.schema_version,
                phase=RepairSessionPhase.DIAGNOSTIC.value,
                message=request.message.model_dump(mode="json"),
                responds_to_input_request_id=request.responds_to_input_request_id,
                start_event_sequence=start_sequence,
            ),
        )
        started_data = validate_repair_session_event_data(
            RepairSessionEventType.TURN_STARTED,
            {"turn_id": turn.id, "phase": RepairSessionPhase.DIAGNOSTIC.value},
        )
        await self._events.add(
            RepairSessionEventModel(
                repair_session_id=repair_session.id,
                turn_id=turn.id,
                sequence=start_sequence,
                type=RepairSessionEventType.TURN_STARTED.value,
                data=started_data,
            ),
        )
        repair_session.status = RepairSessionStatus.RUNNING.value
        repair_session.current_input_request = None
        repair_session.latest_event_sequence = start_sequence
        repair_session.updated_at = datetime.now(UTC)
        return turn

    async def _validate_artifacts(
        self,
        *,
        current_user: User,
        repair_session: RepairSessionModel,
        artifact_ids: list[str],
    ) -> None:
        """Validate every referenced artifact is owned and session-attached."""

        for artifact_id in artifact_ids:
            artifact = await self._artifacts.get_owned(
                artifact_id=artifact_id,
                user_id=current_user.id,
            )
            if artifact is None or artifact.repair_session_id != repair_session.id:
                raise NotFoundError()

    async def _handle_turn_integrity_race(
        self,
        *,
        current_user: User,
        repair_session_id: str,
        client_turn_id: str,
        request_hash: str,
        error: IntegrityError,
    ) -> TurnAccepted:
        """Map defensive uniqueness races to idempotent retry semantics."""

        repair_session = await self._repair_sessions.get_owned_for_update(
            repair_session_id=repair_session_id,
            user_id=current_user.id,
        )
        if repair_session is None:
            raise NotFoundError() from error
        existing = await self._turns.get_by_client_turn_id(
            repair_session_id=repair_session_id,
            client_turn_id=client_turn_id,
        )
        if existing is None:
            raise ServerError() from error
        if existing.request_hash != request_hash:
            raise IdempotencyConflictError() from error
        self._last_acceptance_was_idempotent_replay = True
        return turn_accepted_from_model(existing, repair_session)

    async def _rollback_if_configured(self) -> None:
        """Rollback the current unit of work when one is configured."""

        if self._rollback is not None:
            await self._rollback()


def _validate_accepting_diagnostic_turns(
    repair_session: RepairSessionModel,
) -> None:
    """Validate the session phase and status accept Stage 10 turns."""

    if (
        repair_session.phase != RepairSessionPhase.DIAGNOSTIC.value
        or repair_session.status not in ACCEPTING_DIAGNOSTIC_STATUSES
    ):
        raise SessionStateConflictError()


def _validate_diagnostic_tool_session(
    repair_session: RepairSessionModel,
) -> None:
    """Validate the session phase for internal diagnostic tool writes."""

    if repair_session.phase != RepairSessionPhase.DIAGNOSTIC.value:
        raise SessionStateConflictError()


def _validate_input_request(
    repair_session: RepairSessionModel,
    request: TurnCreate,
) -> None:
    """Validate optional responses to the session's pending input request."""

    input_request_id = request.responds_to_input_request_id
    if input_request_id is None:
        return

    current_input_request = repair_session.current_input_request
    if (
        current_input_request is None
        or current_input_request.get("id") != input_request_id
    ):
        raise NotFoundError()


def _canonical_turn_request_hash(request: TurnCreate) -> str:
    """Return the canonical SHA-256 request hash for turn idempotency."""

    canonical = json.dumps(
        _canonical_turn_request_payload(request),
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _canonical_turn_request_payload(request: TurnCreate) -> dict[str, Any]:
    """Return the canonical public request body used for idempotency."""

    return {
        "client_turn_id": request.client_turn_id,
        "message": request.message.model_dump(mode="json"),
        "responds_to_input_request_id": request.responds_to_input_request_id,
        "schema_version": request.schema_version,
    }


def _turn_acceptance_session_snapshot(
    repair_session: RepairSessionModel,
    turn: RepairTurnModel,
) -> RepairSessionModel:
    """Return the original app-owned session snapshot for an accepted turn."""

    snapshot = copy(repair_session)
    snapshot.status = RepairSessionStatus.RUNNING.value
    snapshot.current_input_request = None
    snapshot.latest_event_sequence = turn.start_event_sequence
    return snapshot
