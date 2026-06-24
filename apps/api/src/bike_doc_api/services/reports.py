"""Report service boundary."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any, Protocol

from pydantic import ValidationError as PydanticValidationError
from sqlalchemy.exc import IntegrityError

from bike_doc_api.core.errors import NotFoundError, ServerError, ValidationAppError
from bike_doc_api.models.artifact import ArtifactRef as ArtifactRefModel
from bike_doc_api.models.event import RepairSessionEvent as RepairSessionEventModel
from bike_doc_api.models.phase_report import PhaseReport as PhaseReportModel
from bike_doc_api.models.repair_session import (
    RepairPhaseSession as RepairPhaseSessionModel,
)
from bike_doc_api.models.repair_session import (
    RepairSession as RepairSessionModel,
)
from bike_doc_api.models.user import User
from bike_doc_api.schemas.common import (
    ArtifactPurpose,
    ArtifactStatus,
    PhaseReportType,
    RepairSessionPhase,
    RepairSessionStatus,
    SafetySeverity,
    SafetyState,
)
from bike_doc_api.schemas.event import (
    RepairSessionEventType,
    validate_repair_session_event_data,
)
from bike_doc_api.schemas.report import (
    DiagnosticReportV1,
    PhaseReportEnvelope,
    PhaseReportList,
    SafetyFlag,
    phase_report_envelope_from_model,
)

DEFAULT_REPORT_LIMIT = 50
MAX_REPORT_LIMIT = 100
DIAGNOSTIC_SCHEMA_VERSION = "diagnostic_report.v1"


class RepairSessionRepositoryProtocol(Protocol):
    """Repair-session operations required by report persistence."""

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
    """Phase-session operations required by report persistence."""

    async def get(
        self,
        phase_session_id: str,
    ) -> RepairPhaseSessionModel | None:
        """Return a phase session by ID."""


class PhaseReportRepositoryProtocol(Protocol):
    """Phase-report operations required by the service."""

    async def add(self, report: PhaseReportModel) -> PhaseReportModel:
        """Add a phase report to the current transaction."""

    async def get_for_session(
        self,
        *,
        repair_session_id: str,
        report_id: str,
    ) -> PhaseReportModel | None:
        """Return a report owned by a repair session."""

    async def list_for_session(
        self,
        repair_session_id: str,
        *,
        report_type: str | None = None,
        limit: int = DEFAULT_REPORT_LIMIT,
        cursor_report: PhaseReportModel | None = None,
    ) -> list[PhaseReportModel]:
        """Return reports for a repair session."""


class RepairSessionEventRepositoryProtocol(Protocol):
    """Event operations required by report persistence."""

    async def add(
        self,
        event: RepairSessionEventModel,
    ) -> RepairSessionEventModel:
        """Add an event with an already allocated sequence."""


class ArtifactRepositoryProtocol(Protocol):
    """Artifact lookups required by report persistence."""

    async def get_owned(
        self,
        *,
        artifact_id: str,
        user_id: str,
    ) -> ArtifactRefModel | None:
        """Return an artifact owned by a user."""


class ReportService:
    """Application-owned report persistence and read behavior."""

    def __init__(
        self,
        repair_sessions: RepairSessionRepositoryProtocol,
        phase_sessions: RepairPhaseSessionRepositoryProtocol,
        reports: PhaseReportRepositoryProtocol,
        events: RepairSessionEventRepositoryProtocol,
        artifacts: ArtifactRepositoryProtocol,
        *,
        commit: Callable[[], Awaitable[None]] | None = None,
        rollback: Callable[[], Awaitable[None]] | None = None,
    ) -> None:
        self._repair_sessions = repair_sessions
        self._phase_sessions = phase_sessions
        self._reports = reports
        self._events = events
        self._artifacts = artifacts
        self._commit = commit
        self._rollback = rollback

    async def persist_diagnostic_report(
        self,
        *,
        current_user: User,
        repair_session_id: str,
        summary: str,
        payload: DiagnosticReportV1 | dict[str, Any],
        safety_flags: list[SafetyFlag | dict[str, Any]],
        source_artifact_ids: list[str],
        turn_id: str | None = None,
    ) -> PhaseReportEnvelope:
        """Persist a schema-valid diagnostic report without invoking ADK."""

        try:
            validated = DiagnosticReportV1.model_validate(payload)
            envelope = PhaseReportEnvelope(
                id="rpt_validation_placeholder",
                repair_session_id=repair_session_id,
                type=PhaseReportType.DIAGNOSTIC,
                schema_version=DIAGNOSTIC_SCHEMA_VERSION,
                phase=RepairSessionPhase.DIAGNOSTIC,
                summary=summary,
                safety_flags=[SafetyFlag.model_validate(flag) for flag in safety_flags],
                source_artifact_ids=source_artifact_ids,
                created_at=datetime.now(UTC),
                payload=validated,
            )
            _validate_diagnostic_envelope(envelope)

            repair_session = await self._repair_sessions.get_owned_for_update(
                repair_session_id=repair_session_id,
                user_id=current_user.id,
            )
            if repair_session is None:
                raise NotFoundError()

            await self._validate_artifacts(
                current_user=current_user,
                repair_session=repair_session,
                artifact_ids=[
                    *envelope.source_artifact_ids,
                    *validated.key_artifact_ids,
                ],
            )
            phase_session = await self._validate_diagnostic_session(
                repair_session_id=repair_session.id,
                diagnostic_session_id=validated.diagnostic_session_id,
            )

            report = await self._reports.add(
                PhaseReportModel(
                    repair_session_id=repair_session.id,
                    repair_phase_session_id=phase_session.id,
                    type=PhaseReportType.DIAGNOSTIC.value,
                    schema_version=DIAGNOSTIC_SCHEMA_VERSION,
                    phase=RepairSessionPhase.DIAGNOSTIC.value,
                    summary=envelope.summary,
                    safety_flags=[
                        flag.model_dump(mode="json") for flag in envelope.safety_flags
                    ],
                    source_artifact_ids=list(envelope.source_artifact_ids),
                    payload=validated.model_dump(mode="json"),
                ),
            )
            await self._apply_report_session_updates(
                repair_session=repair_session,
                report=report,
                safety_flags=envelope.safety_flags,
                turn_id=turn_id,
            )
            if self._commit is not None:
                await self._commit()
        except (NotFoundError, ValidationAppError):
            await self._rollback_if_configured()
            raise
        except (PydanticValidationError, ValueError) as exc:
            await self._rollback_if_configured()
            raise ValidationAppError() from exc
        except IntegrityError as exc:
            await self._rollback_if_configured()
            raise ServerError() from exc
        except Exception as exc:
            await self._rollback_if_configured()
            raise ServerError() from exc

        return _public_envelope_or_server_error(report)

    async def list_reports(
        self,
        *,
        current_user: User,
        repair_session_id: str,
        limit: int = DEFAULT_REPORT_LIMIT,
        cursor: str | None = None,
    ) -> PhaseReportList:
        """Return public report envelopes for an owned repair session."""

        if limit < 1 or limit > MAX_REPORT_LIMIT:
            raise ValidationAppError()
        if cursor is not None and not cursor.strip():
            raise ValidationAppError()

        repair_session = await self._repair_sessions.get_owned(
            repair_session_id=repair_session_id,
            user_id=current_user.id,
        )
        if repair_session is None:
            raise NotFoundError()

        cursor_report = None
        if cursor is not None:
            cursor_report = await self._reports.get_for_session(
                repair_session_id=repair_session.id,
                report_id=cursor,
            )
            if cursor_report is None:
                raise ValidationAppError()

        reports = await self._reports.list_for_session(
            repair_session.id,
            report_type=PhaseReportType.DIAGNOSTIC.value,
            limit=limit + 1,
            cursor_report=cursor_report,
        )
        page = reports[:limit]
        return PhaseReportList(
            items=[_public_envelope_or_server_error(report) for report in page],
            next_cursor=reports[limit].id if len(reports) > limit else None,
        )

    async def get_report(
        self,
        *,
        current_user: User,
        repair_session_id: str,
        report_id: str,
    ) -> PhaseReportEnvelope:
        """Return one public report envelope for an owned repair session."""

        repair_session = await self._repair_sessions.get_owned(
            repair_session_id=repair_session_id,
            user_id=current_user.id,
        )
        if repair_session is None:
            raise NotFoundError()

        report = await self._reports.get_for_session(
            repair_session_id=repair_session.id,
            report_id=report_id,
        )
        if report is None:
            raise NotFoundError()
        return _public_envelope_or_server_error(report)

    async def _validate_artifacts(
        self,
        *,
        current_user: User,
        repair_session: RepairSessionModel,
        artifact_ids: list[str],
    ) -> None:
        """Validate report artifacts are owned diagnostic evidence for this session."""

        for artifact_id in dict.fromkeys(artifact_ids):
            artifact = await self._artifacts.get_owned(
                artifact_id=artifact_id,
                user_id=current_user.id,
            )
            if artifact is None or artifact.repair_session_id != repair_session.id:
                raise NotFoundError()
            if (
                artifact.purpose != ArtifactPurpose.DIAGNOSTIC_PHOTO.value
                or artifact.status != ArtifactStatus.READY.value
            ):
                raise ValidationAppError()

    async def _validate_diagnostic_session(
        self,
        *,
        repair_session_id: str,
        diagnostic_session_id: str,
    ) -> RepairPhaseSessionModel:
        """Validate the public diagnostic session ID is app-owned and scoped."""

        phase_session = await self._phase_sessions.get(diagnostic_session_id)
        if (
            phase_session is None
            or phase_session.repair_session_id != repair_session_id
            or phase_session.phase != RepairSessionPhase.DIAGNOSTIC.value
        ):
            raise ValidationAppError()
        return phase_session

    async def _apply_report_session_updates(
        self,
        *,
        repair_session: RepairSessionModel,
        report: PhaseReportModel,
        safety_flags: list[SafetyFlag],
        turn_id: str | None,
    ) -> None:
        """Update session state and append report-related events in order."""

        old_safety_state = repair_session.safety_state
        serialized_flags = [flag.model_dump(mode="json") for flag in safety_flags]
        new_safety_state = _derive_safety_state(safety_flags)

        repair_session.diagnostic_report_id = report.id
        repair_session.active_safety_flags = serialized_flags
        repair_session.safety_state = new_safety_state
        repair_session.status = (
            RepairSessionStatus.BLOCKED_SAFETY.value
            if new_safety_state == SafetyState.BLOCKED.value
            else RepairSessionStatus.AWAITING_DECISION.value
        )
        repair_session.updated_at = datetime.now(UTC)

        sequence = repair_session.latest_event_sequence
        if old_safety_state != new_safety_state:
            sequence += 1
            await self._events.add(
                RepairSessionEventModel(
                    repair_session_id=repair_session.id,
                    turn_id=turn_id,
                    sequence=sequence,
                    type=RepairSessionEventType.SAFETY_ESCALATED.value,
                    data=validate_repair_session_event_data(
                        RepairSessionEventType.SAFETY_ESCALATED,
                        {
                            "safety_state": new_safety_state,
                            "safety_flags": serialized_flags,
                            "user_message": _safety_event_message(serialized_flags),
                            "blocks_repair_instructions": any(
                                flag.blocks_repair_instructions for flag in safety_flags
                            ),
                        },
                    ),
                ),
            )

        sequence += 1
        await self._events.add(
            RepairSessionEventModel(
                repair_session_id=repair_session.id,
                turn_id=turn_id,
                sequence=sequence,
                type=RepairSessionEventType.PHASE_REPORT_CREATED.value,
                data=validate_repair_session_event_data(
                    RepairSessionEventType.PHASE_REPORT_CREATED,
                    {
                        "report_id": report.id,
                        "report_type": PhaseReportType.DIAGNOSTIC.value,
                        "schema_version": DIAGNOSTIC_SCHEMA_VERSION,
                        "phase": RepairSessionPhase.DIAGNOSTIC.value,
                        "summary": report.summary,
                    },
                ),
            ),
        )
        repair_session.latest_event_sequence = sequence

    async def _rollback_if_configured(self) -> None:
        """Rollback the current unit of work when one is configured."""

        if self._rollback is not None:
            await self._rollback()


def _validate_diagnostic_envelope(envelope: PhaseReportEnvelope) -> None:
    """Apply Stage 11 diagnostic report invariants before persistence."""

    if envelope.type is not PhaseReportType.DIAGNOSTIC:
        raise ValidationAppError()
    if envelope.schema_version != DIAGNOSTIC_SCHEMA_VERSION:
        raise ValidationAppError()
    if envelope.phase is not RepairSessionPhase.DIAGNOSTIC:
        raise ValidationAppError()
    if not envelope.summary.strip():
        raise ValidationAppError()
    if not isinstance(envelope.payload, DiagnosticReportV1):
        raise ValidationAppError()
    if envelope.payload.schema_version != DIAGNOSTIC_SCHEMA_VERSION:
        raise ValidationAppError()
    if _dump_flags(envelope.payload.safety_flags) != _dump_flags(envelope.safety_flags):
        raise ValidationAppError()
    for flag in envelope.safety_flags:
        if flag.phase is not RepairSessionPhase.DIAGNOSTIC:
            raise ValidationAppError()
        if (
            flag.severity is SafetySeverity.BLOCKING
            and not flag.blocks_repair_instructions
        ):
            raise ValidationAppError()


def _derive_safety_state(safety_flags: list[SafetyFlag]) -> str:
    """Derive repair_sessions.safety_state from active report flags."""

    if any(flag.severity is SafetySeverity.BLOCKING for flag in safety_flags):
        return SafetyState.BLOCKED.value
    if any(flag.severity is SafetySeverity.WARNING for flag in safety_flags):
        return SafetyState.SHOP_RECOMMENDED.value
    if any(flag.severity is SafetySeverity.CAUTION for flag in safety_flags):
        return SafetyState.CAUTION.value
    return SafetyState.OK.value


def _dump_flags(flags: list[SafetyFlag]) -> list[dict[str, Any]]:
    """Return normalized public safety flag JSON."""

    return [flag.model_dump(mode="json") for flag in flags]


def _safety_event_message(serialized_flags: list[dict[str, Any]]) -> str:
    """Return a public safety event message without exposing internals."""

    if serialized_flags:
        return str(serialized_flags[0]["message"])
    return "Safety state updated."


def _public_envelope_or_server_error(
    report: PhaseReportModel,
) -> PhaseReportEnvelope:
    """Validate stored report data before public exposure."""

    try:
        public = phase_report_envelope_from_model(report)
        _validate_diagnostic_envelope(public)
    except (PydanticValidationError, ValueError) as exc:
        raise ServerError() from exc
    return public
