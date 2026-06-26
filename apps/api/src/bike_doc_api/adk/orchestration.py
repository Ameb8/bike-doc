"""ADK orchestration boundary."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol, cast

from bike_doc_api.adk.runner import (
    DiagnosticRunnerArtifactReferenced,
    DiagnosticRunnerAssistantDelta,
    DiagnosticRunnerAssistantMessageCompleted,
    DiagnosticRunnerInputRequested,
    DiagnosticRunnerProtocol,
    DiagnosticRunnerRecoverableError,
    DiagnosticRunnerReportCompleted,
    DiagnosticRunnerRequest,
    DiagnosticRunnerSafetyEscalated,
)
from bike_doc_api.adk.tools.artifacts import ListDiagnosticArtifactsTool
from bike_doc_api.adk.tools.bike_profile import GetBikeProfileTool
from bike_doc_api.adk.tools.common import DiagnosticToolContext
from bike_doc_api.adk.tools.input_requests import RequestDiagnosticInputTool
from bike_doc_api.adk.tools.repair_history import LookupRepairHistoryTool
from bike_doc_api.adk.tools.reports import SaveDiagnosticReportTool
from bike_doc_api.adk.tools.safety import RaiseSafetyFlagTool
from bike_doc_api.core.errors import NotFoundError, ServerError
from bike_doc_api.models.artifact import ArtifactRef as ArtifactRefModel
from bike_doc_api.models.event import RepairSessionEvent as RepairSessionEventModel
from bike_doc_api.models.repair_session import (
    RepairPhaseSession as RepairPhaseSessionModel,
)
from bike_doc_api.models.repair_session import RepairSession as RepairSessionModel
from bike_doc_api.models.repair_session import RepairTurn as RepairTurnModel
from bike_doc_api.models.user import User
from bike_doc_api.schemas.artifact import artifact_ref_from_model
from bike_doc_api.schemas.common import RepairSessionPhase, RepairSessionStatus
from bike_doc_api.schemas.event import (
    RepairSessionEventType,
    validate_repair_session_event_data,
)
from bike_doc_api.schemas.repair_session import (
    repair_session_from_model,
)


class RepairPhaseSessionRepositoryProtocol(Protocol):
    """Phase-session lookup required by orchestration."""

    async def get(self, phase_session_id: str) -> RepairPhaseSessionModel | None:
        """Return a phase session by app-owned ID."""


class RepairSessionRepositoryProtocol(Protocol):
    """Repair-session persistence required for terminal turn events."""

    async def get_owned_for_update(
        self,
        *,
        repair_session_id: str,
        user_id: str,
    ) -> RepairSessionModel | None:
        """Return and lock an owned repair session row."""


class RepairSessionEventRepositoryProtocol(Protocol):
    """Event persistence required for terminal turn events."""

    async def add(
        self,
        event: RepairSessionEventModel,
    ) -> RepairSessionEventModel:
        """Add an event with an already allocated sequence."""


class ArtifactRepositoryProtocol(Protocol):
    """Artifact lookup required for artifact.referenced events."""

    async def get_owned(
        self,
        *,
        artifact_id: str,
        user_id: str,
    ) -> ArtifactRefModel | None:
        """Return an artifact owned by a user."""


class EventServiceProtocol(Protocol):
    """Public product-event persistence boundary."""

    async def append_event(
        self,
        *,
        repair_session_id: str,
        event_type: RepairSessionEventType | str,
        data: dict[str, Any],
        turn_id: str | None = None,
    ) -> Any:
        """Persist and publish one public event."""


@dataclass(frozen=True, slots=True)
class DiagnosticTurnOrchestrator:
    """Connect accepted diagnostic turns to the internal ADK boundary."""

    phase_sessions: RepairPhaseSessionRepositoryProtocol
    repair_sessions: RepairSessionRepositoryProtocol
    events: RepairSessionEventRepositoryProtocol
    artifacts: ArtifactRepositoryProtocol
    event_service: EventServiceProtocol
    runner: DiagnosticRunnerProtocol
    get_bike_profile: GetBikeProfileTool
    lookup_repair_history: LookupRepairHistoryTool
    list_diagnostic_artifacts: ListDiagnosticArtifactsTool
    request_diagnostic_input: RequestDiagnosticInputTool
    raise_safety_flag: RaiseSafetyFlagTool
    save_diagnostic_report: SaveDiagnosticReportTool
    commit: Callable[[], Awaitable[None]] | None = None
    rollback: Callable[[], Awaitable[None]] | None = None

    async def process_turn(
        self,
        *,
        current_user: User,
        turn: RepairTurnModel,
    ) -> None:
        """Run diagnostic orchestration for an already accepted turn."""

        try:
            phase_session = await self.phase_sessions.get(
                turn.repair_phase_session_id,
            )
            if phase_session is None:
                raise NotFoundError()

            context = DiagnosticToolContext(
                user_id=current_user.id,
                user_skill_level=current_user.skill_level,
                repair_session_id=turn.repair_session_id,
                active_phase=RepairSessionPhase.DIAGNOSTIC,
                diagnostic_session_id=phase_session.id,
                turn_id=turn.id,
            )
            seed = await self._build_seed_context(context=context, turn=turn)
            await self._emit_turn_artifact_references(
                current_user=current_user,
                turn=turn,
            )
            processing_state = _TurnProcessingState()
            request = DiagnosticRunnerRequest(
                user_id=current_user.id,
                user_skill_level=current_user.skill_level,
                repair_session_id=turn.repair_session_id,
                turn_id=turn.id,
                diagnostic_session_id=phase_session.id,
                adk_session_id=phase_session.adk_session_id,
                message_text=_turn_message_text(turn),
                artifact_ids=_turn_artifact_ids(turn),
                bike_profile=seed.bike_profile,
                repair_history=seed.repair_history,
                diagnostic_artifacts=seed.diagnostic_artifacts,
            )
            async for event in self.runner.stream(request):
                await self._process_runner_event(
                    context=context,
                    turn=turn,
                    event=event,
                    processing_state=processing_state,
                )

            await self._append_turn_completed(
                current_user=current_user,
                repair_session_id=turn.repair_session_id,
                turn_id=turn.id,
                status=processing_state.terminal_status,
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            await self._append_recoverable_error(
                repair_session_id=turn.repair_session_id,
                turn_id=turn.id,
                code="diagnostic_processing_error",
                message="Diagnostic processing could not be completed.",
                retryable=True,
            )
            await self._append_turn_completed(
                current_user=current_user,
                repair_session_id=turn.repair_session_id,
                turn_id=turn.id,
                status=RepairSessionStatus.AWAITING_USER,
            )

    async def _process_runner_event(
        self,
        *,
        context: DiagnosticToolContext,
        turn: RepairTurnModel,
        event: Any,
        processing_state: _TurnProcessingState,
    ) -> None:
        """Map one app-owned runner event to public persistence/tool effects."""

        if isinstance(event, DiagnosticRunnerAssistantDelta):
            await self.event_service.append_event(
                repair_session_id=turn.repair_session_id,
                turn_id=turn.id,
                event_type=RepairSessionEventType.ASSISTANT_DELTA,
                data={"text": event.text},
            )
            return

        if isinstance(event, DiagnosticRunnerAssistantMessageCompleted):
            await self.event_service.append_event(
                repair_session_id=turn.repair_session_id,
                turn_id=turn.id,
                event_type=RepairSessionEventType.ASSISTANT_MESSAGE_COMPLETED,
                data={
                    "message_id": event.message_id,
                    "full_text": event.full_text,
                    "artifact_ids": list(event.artifact_ids),
                    "display_safety_level": event.display_safety_level.value,
                },
            )
            return

        if isinstance(event, DiagnosticRunnerInputRequested):
            processing_state.note_input_requested()
            return

        if isinstance(event, DiagnosticRunnerSafetyEscalated):
            processing_state.note_safety_escalated(
                safety_state=event.safety_state,
                safety_flags=event.safety_flags,
                safety_flag=event.safety_flag,
            )
            return

        if isinstance(event, DiagnosticRunnerReportCompleted):
            processing_state.note_report_completed(
                safety_state=event.safety_state,
                safety_flags=event.safety_flags,
            )
            return

        if isinstance(event, DiagnosticRunnerArtifactReferenced):
            await self.event_service.append_event(
                repair_session_id=turn.repair_session_id,
                turn_id=turn.id,
                event_type=RepairSessionEventType.ARTIFACT_REFERENCED,
                data={"artifact": dict(event.artifact)},
            )
            return

        if isinstance(event, DiagnosticRunnerRecoverableError):
            await self._append_recoverable_error(
                repair_session_id=turn.repair_session_id,
                turn_id=turn.id,
                code=event.code,
                message=event.message,
                retryable=event.retryable,
            )
            processing_state.note_error(retryable=event.retryable)

    async def _build_seed_context(
        self,
        *,
        context: DiagnosticToolContext,
        turn: RepairTurnModel,
    ) -> _DiagnosticSeedContext:
        """Seed the diagnostic run with durable product context."""

        bike_profile: Mapping[str, Any] | None = None
        repair_history: tuple[Mapping[str, Any], ...] = ()
        diagnostic_artifacts: tuple[Mapping[str, Any], ...] = ()

        bike_profile_result = await self.get_bike_profile.run(
            {"repair_session_id": turn.repair_session_id},
            context,
        )
        if bike_profile_result.get("ok") is True:
            data = cast(Mapping[str, Any], bike_profile_result.get("data", {}))
            profile = data.get("bike_profile")
            if isinstance(profile, Mapping):
                bike_profile = profile

        history_result = await self.lookup_repair_history.run(
            {
                "repair_session_id": turn.repair_session_id,
                "component_terms": [],
                "limit": 5,
            },
            context,
        )
        if history_result.get("ok") is True:
            data = cast(Mapping[str, Any], history_result.get("data", {}))
            repair_history = _mapping_items(data.get("entries"))

        artifacts_result = await self.list_diagnostic_artifacts.run(
            {"repair_session_id": turn.repair_session_id},
            context,
        )
        if artifacts_result.get("ok") is True:
            data = cast(Mapping[str, Any], artifacts_result.get("data", {}))
            diagnostic_artifacts = _mapping_items(data.get("artifacts"))

        return _DiagnosticSeedContext(
            bike_profile=bike_profile,
            repair_history=repair_history,
            diagnostic_artifacts=diagnostic_artifacts,
        )

    async def _emit_turn_artifact_references(
        self,
        *,
        current_user: User,
        turn: RepairTurnModel,
    ) -> None:
        """Persist public references for artifacts included in the user turn."""

        for artifact_id in _turn_artifact_ids(turn):
            artifact = await self.artifacts.get_owned(
                artifact_id=artifact_id,
                user_id=current_user.id,
            )
            if artifact is None:
                continue
            await self.event_service.append_event(
                repair_session_id=turn.repair_session_id,
                turn_id=turn.id,
                event_type=RepairSessionEventType.ARTIFACT_REFERENCED,
                data={
                    "artifact": artifact_ref_from_model(artifact).model_dump(
                        mode="json",
                    ),
                },
            )

    async def _append_recoverable_error(
        self,
        *,
        repair_session_id: str,
        turn_id: str,
        code: str,
        message: str,
        retryable: bool,
    ) -> None:
        """Persist a public recoverable processing error."""

        await self.event_service.append_event(
            repair_session_id=repair_session_id,
            turn_id=turn_id,
            event_type=RepairSessionEventType.ERROR,
            data={"code": code, "message": message, "retryable": retryable},
        )

    async def _append_turn_completed(
        self,
        *,
        current_user: User,
        repair_session_id: str,
        turn_id: str,
        status: RepairSessionStatus,
    ) -> None:
        """Persist terminal turn completion with the current session snapshot."""

        try:
            repair_session = await self.repair_sessions.get_owned_for_update(
                repair_session_id=repair_session_id,
                user_id=current_user.id,
            )
            if repair_session is None:
                raise NotFoundError()

            repair_session.status = status.value
            sequence = repair_session.latest_event_sequence + 1
            repair_session.latest_event_sequence = sequence
            repair_session.updated_at = datetime.now(UTC)
            completed_data = validate_repair_session_event_data(
                RepairSessionEventType.TURN_COMPLETED,
                {
                    "turn_id": turn_id,
                    "session": repair_session_from_model(repair_session).model_dump(
                        mode="json",
                    ),
                },
            )
            await self.events.add(
                RepairSessionEventModel(
                    repair_session_id=repair_session.id,
                    turn_id=turn_id,
                    sequence=sequence,
                    type=RepairSessionEventType.TURN_COMPLETED.value,
                    data=completed_data,
                ),
            )
            if self.commit is not None:
                await self.commit()
        except Exception as exc:
            await self._rollback_if_configured()
            raise ServerError() from exc

    async def _rollback_if_configured(self) -> None:
        """Rollback the current unit of work when one is configured."""

        if self.rollback is not None:
            await self.rollback()


@dataclass(frozen=True, slots=True)
class _DiagnosticSeedContext:
    """Durable diagnostic evidence passed to the runner boundary."""

    bike_profile: Mapping[str, Any] | None
    repair_history: tuple[Mapping[str, Any], ...]
    diagnostic_artifacts: tuple[Mapping[str, Any], ...]


@dataclass(slots=True)
class _TurnProcessingState:
    """Track the terminal public status implied by streamed runner events."""

    terminal_status: RepairSessionStatus = RepairSessionStatus.AWAITING_USER

    def note_input_requested(self) -> None:
        """Record that direct ADK tool execution requested more user input."""

        self.terminal_status = RepairSessionStatus.AWAITING_USER

    def note_safety_escalated(
        self,
        *,
        safety_state: str | None,
        safety_flags: tuple[Mapping[str, Any], ...],
        safety_flag: Mapping[str, Any],
    ) -> None:
        """Record the status implied by an already-persisted safety update."""

        flags: tuple[Mapping[str, Any], ...]
        flags = safety_flags if safety_flags else (safety_flag,)
        if _blocks_repair_guidance(safety_state=safety_state, safety_flags=flags):
            self.terminal_status = RepairSessionStatus.BLOCKED_SAFETY

    def note_report_completed(
        self,
        *,
        safety_state: str | None,
        safety_flags: tuple[Mapping[str, Any], ...],
    ) -> None:
        """Record the status implied by an already-persisted report."""

        self.terminal_status = (
            RepairSessionStatus.BLOCKED_SAFETY
            if _blocks_repair_guidance(
                safety_state=safety_state,
                safety_flags=safety_flags,
            )
            else RepairSessionStatus.AWAITING_DECISION
        )

    def note_error(self, *, retryable: bool) -> None:
        """Record the status implied by a handled recoverable runner error."""

        self.terminal_status = (
            RepairSessionStatus.AWAITING_USER
            if retryable
            else RepairSessionStatus.FAILED
        )


def _turn_message_text(turn: RepairTurnModel) -> str | None:
    """Return accepted user text from a persisted turn."""

    text = turn.message.get("text")
    return text if isinstance(text, str) and text else None


def _turn_artifact_ids(turn: RepairTurnModel) -> tuple[str, ...]:
    """Return accepted user artifact IDs from a persisted turn."""

    artifact_ids = turn.message.get("artifact_ids")
    if not isinstance(artifact_ids, list):
        return ()
    return tuple(
        artifact_id
        for artifact_id in artifact_ids
        if isinstance(artifact_id, str) and artifact_id
    )


def _mapping_items(value: object) -> tuple[Mapping[str, Any], ...]:
    """Return mapping items from a tool result payload."""

    if not isinstance(value, list):
        return ()
    return tuple(
        cast(Mapping[str, Any], item) for item in value if isinstance(item, Mapping)
    )


def _blocks_repair_guidance(
    *,
    safety_state: str | None,
    safety_flags: tuple[Mapping[str, Any], ...],
) -> bool:
    """Return whether runner metadata says repair guidance is safety-blocked."""

    if safety_state == "blocked":
        return True
    return any(flag.get("blocks_repair_instructions") is True for flag in safety_flags)
