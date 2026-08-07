"""Report service boundary."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal, Protocol

from pydantic import ValidationError as PydanticValidationError
from sqlalchemy.exc import IntegrityError

from bike_doc_api.core.errors import (
    AppError,
    NotFoundError,
    ServerError,
    SessionStateConflictError,
    ValidationAppError,
)
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
    Confidence,
    PhaseReportType,
    RepairSessionPhase,
    RepairSessionStatus,
)
from bike_doc_api.schemas.event import (
    RepairSessionEventType,
    validate_repair_session_event_data,
)
from bike_doc_api.schemas.report import (
    CostItemType,
    DiagnosticReportV1,
    DiagnosticReportV2,
    PhaseReportEnvelope,
    PhaseReportList,
    PlanCostEstimate,
    PlanReportV1,
    PriceLookupRequirement,
    SafetyFlag,
    phase_report_envelope_from_model,
)
from bike_doc_api.services.safety import SafetyService

DEFAULT_REPORT_LIMIT = 50
MAX_REPORT_LIMIT = 100
DIAGNOSTIC_SCHEMA_VERSION = "diagnostic_report.v2"
logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ReportPersistenceEvents:
    """Event metadata emitted while persisting a phase report."""

    safety_escalated: RepairSessionEventModel | None
    phase_report_created: RepairSessionEventModel
    phase_transitioned: RepairSessionEventModel | None = None


@dataclass(frozen=True, slots=True)
class DiagnosticReportPersistenceResult:
    """Tool-facing diagnostic report persistence result."""

    report: PhaseReportEnvelope
    events: ReportPersistenceEvents
    safety_state: str
    active_safety_flags: list[SafetyFlag]


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


class CostEstimateServiceProtocol(Protocol):
    """Cost-estimate operations required for diagnostic report enrichment."""

    async def estimate_plan_cost(
        self,
        requirements: list[PriceLookupRequirement],
    ) -> PlanCostEstimate:
        """Return item-level pricing and rollups for report requirements."""


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
        safety: SafetyService | None = None,
        cost_estimate_service: CostEstimateServiceProtocol | None = None,
        commit: Callable[[], Awaitable[None]] | None = None,
        rollback: Callable[[], Awaitable[None]] | None = None,
    ) -> None:
        self._repair_sessions = repair_sessions
        self._phase_sessions = phase_sessions
        self._reports = reports
        self._events = events
        self._artifacts = artifacts
        self._safety = safety or SafetyService()
        self._cost_estimate_service = cost_estimate_service
        self._commit = commit
        self._rollback = rollback

    async def persist_diagnostic_report(
        self,
        *,
        current_user: User,
        repair_session_id: str,
        summary: str,
        payload: DiagnosticReportV1 | DiagnosticReportV2 | dict[str, Any],
        safety_flags: list[SafetyFlag | dict[str, Any]],
        source_artifact_ids: list[str],
        turn_id: str | None = None,
    ) -> PhaseReportEnvelope:
        """Persist a schema-valid diagnostic report without invoking ADK."""

        result = await self._persist_diagnostic_report(
            current_user=current_user,
            repair_session_id=repair_session_id,
            summary=summary,
            payload=payload,
            safety_flags=safety_flags,
            source_artifact_ids=source_artifact_ids,
            turn_id=turn_id,
        )
        return result.report

    async def persist_diagnostic_report_from_tool(
        self,
        *,
        current_user: User,
        repair_session_id: str,
        diagnostic_session_id: str,
        summary: str | None,
        payload: DiagnosticReportV1 | DiagnosticReportV2 | dict[str, Any],
        report_schema_version: Literal["diagnostic_report.v1", "diagnostic_report.v2"],
        completion_reason: str | None = None,
        turn_id: str | None = None,
    ) -> DiagnosticReportPersistenceResult:
        """Persist a diagnostic report produced through an internal ADK tool."""

        payload_data = _payload_data(payload)
        if payload_data.get("schema_version") != report_schema_version:
            raise ValidationAppError()
        if report_schema_version == "diagnostic_report.v2":
            if completion_reason not in {
                "diagnosis_supported",
                "user_declined_more_input",
                "requested_input_unavailable",
                "in_person_assessment_required",
            }:
                raise ValidationAppError()
            if (
                "diagnostic_outcome" in payload_data
                or "diagnostic_session_id" in payload_data
                or summary is not None
            ):
                raise ValidationAppError()
            payload_data["diagnostic_outcome"] = completion_reason
            payload_data["diagnostic_session_id"] = diagnostic_session_id
            validated = _validate_diagnostic_payload(payload_data)
            if not isinstance(validated, DiagnosticReportV2):
                raise ValidationAppError()
            summary = validated.evidence_summary
            source_artifact_ids = _report_artifact_ids(validated)
        else:
            if summary is None or not summary.strip() or completion_reason is not None:
                raise ValidationAppError()
            if "diagnostic_session_id" in payload_data:
                raise ValidationAppError()
            payload_data["diagnostic_session_id"] = diagnostic_session_id
            key_artifact_ids = payload_data.get("key_artifact_ids")
            if not isinstance(key_artifact_ids, list) or not all(
                isinstance(artifact_id, str) for artifact_id in key_artifact_ids
            ):
                raise ValidationAppError()
            source_artifact_ids = key_artifact_ids
        safety_flags = _payload_safety_flags(payload_data)
        return await self._persist_diagnostic_report(
            current_user=current_user,
            repair_session_id=repair_session_id,
            summary=summary,
            payload=payload_data,
            safety_flags=list(safety_flags),
            source_artifact_ids=source_artifact_ids,
            turn_id=turn_id,
        )

    async def _persist_diagnostic_report(
        self,
        *,
        current_user: User,
        repair_session_id: str,
        summary: str,
        payload: DiagnosticReportV1 | DiagnosticReportV2 | dict[str, Any],
        safety_flags: list[SafetyFlag | dict[str, Any]],
        source_artifact_ids: list[str],
        turn_id: str | None = None,
    ) -> DiagnosticReportPersistenceResult:
        """Persist a schema-valid diagnostic report and return event metadata."""

        try:
            payload_data = _payload_data(payload)
            raw_payload_flags = _payload_safety_flags(payload_data)
            report_safety = self._safety.validate_report_safety_flags(
                payload_flags=raw_payload_flags,
                envelope_flags=safety_flags,
            )
            payload_data["safety_flags"] = [
                flag.model_dump(mode="json") for flag in report_safety.payload_flags
            ]
            validated = _validate_diagnostic_payload(payload_data)
            if isinstance(validated, DiagnosticReportV2):
                # V2 image findings are retained evidence even when not designated
                # as key artifacts; keep the envelope's invalidation set complete.
                source_artifact_ids = _report_artifact_ids(validated)
            schema_version = validated.schema_version
            envelope = PhaseReportEnvelope(
                id="rpt_validation_placeholder",
                repair_session_id=repair_session_id,
                type=PhaseReportType.DIAGNOSTIC,
                schema_version=schema_version,
                phase=RepairSessionPhase.DIAGNOSTIC,
                summary=summary,
                safety_flags=report_safety.envelope_flags,
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
            if repair_session.phase != RepairSessionPhase.DIAGNOSTIC.value:
                raise SessionStateConflictError()

            await self._validate_artifacts(
                current_user=current_user,
                repair_session=repair_session,
                artifact_ids=[
                    *envelope.source_artifact_ids,
                    *_report_artifact_ids(validated),
                ],
            )
            phase_session = await self._validate_diagnostic_session(
                repair_session_id=repair_session.id,
                diagnostic_session_id=validated.diagnostic_session_id,
            )
            if (
                isinstance(validated, DiagnosticReportV1)
                and validated.cost_estimate is None
            ):
                validated = await self._with_diagnostic_cost_estimate(validated)
                envelope = PhaseReportEnvelope(
                    id=envelope.id,
                    repair_session_id=envelope.repair_session_id,
                    type=envelope.type,
                    schema_version=envelope.schema_version,
                    phase=envelope.phase,
                    summary=envelope.summary,
                    safety_flags=envelope.safety_flags,
                    source_artifact_ids=envelope.source_artifact_ids,
                    created_at=envelope.created_at,
                    payload=validated,
                )
                _validate_diagnostic_envelope(envelope)

            report = await self._reports.add(
                PhaseReportModel(
                    repair_session_id=repair_session.id,
                    repair_phase_session_id=phase_session.id,
                    type=PhaseReportType.DIAGNOSTIC.value,
                    schema_version=schema_version,
                    phase=RepairSessionPhase.DIAGNOSTIC.value,
                    summary=envelope.summary,
                    safety_flags=[
                        flag.model_dump(mode="json") for flag in envelope.safety_flags
                    ],
                    source_artifact_ids=list(envelope.source_artifact_ids),
                    payload=validated.model_dump(mode="json"),
                ),
            )
            events = await self._apply_report_session_updates(
                repair_session=repair_session,
                report=report,
                safety_flags=envelope.safety_flags,
                turn_id=turn_id,
            )
            if self._commit is not None:
                await self._commit()
        except AppError:
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

        return DiagnosticReportPersistenceResult(
            report=_public_envelope_or_server_error(report),
            events=events,
            safety_state=repair_session.safety_state,
            active_safety_flags=[
                SafetyFlag.model_validate(flag)
                for flag in repair_session.active_safety_flags
            ],
        )

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
        public = _public_envelope_or_server_error(report)
        if (
            public.type is PhaseReportType.DIAGNOSTIC
            and isinstance(public.payload, DiagnosticReportV1)
            and public.payload.cost_estimate is None
        ):
            enriched_payload = await self._with_diagnostic_cost_estimate(
                public.payload,
            )
            if enriched_payload.cost_estimate is not None:
                public_data = public.model_dump(mode="python")
                public_data["payload"] = enriched_payload
                return PhaseReportEnvelope.model_validate(public_data)
        return public

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

    async def _with_diagnostic_cost_estimate(
        self,
        report: DiagnosticReportV1,
    ) -> DiagnosticReportV1:
        """Attach live pricing evidence to diagnostic reports when available."""

        if self._cost_estimate_service is None:
            return report

        requirements = _price_requirements_from_diagnostic_report(report)
        if not requirements:
            return report

        try:
            estimate = await self._cost_estimate_service.estimate_plan_cost(
                requirements,
            )
        except Exception:
            logger.info(
                "diagnostic_report_cost_estimate_degraded",
                extra={
                    "diagnostic_session_id": report.diagnostic_session_id,
                    "requirement_count": len(requirements),
                },
                exc_info=True,
            )
            return report
        report_data = report.model_dump(mode="python")
        report_data["cost_estimate"] = estimate
        return DiagnosticReportV1.model_validate(report_data)

    async def _apply_report_session_updates(
        self,
        *,
        repair_session: RepairSessionModel,
        report: PhaseReportModel,
        safety_flags: list[SafetyFlag],
        turn_id: str | None,
    ) -> ReportPersistenceEvents:
        """Update session state and append report-related events in order."""

        safety_update = self._safety.apply_report_safety_flags(
            repair_session=repair_session,
            report_flags=safety_flags,
        )

        repair_session.diagnostic_report_id = report.id
        repair_session.status = (
            RepairSessionStatus.BLOCKED_SAFETY.value
            if safety_update.safety_state == "blocked"
            else RepairSessionStatus.AWAITING_DECISION.value
        )
        repair_session.updated_at = datetime.now(UTC)

        sequence = repair_session.latest_event_sequence
        safety_event: RepairSessionEventModel | None = None
        if safety_update.emit_safety_escalated and safety_update.event_data is not None:
            sequence += 1
            safety_event = await self._events.add(
                RepairSessionEventModel(
                    repair_session_id=repair_session.id,
                    turn_id=turn_id,
                    sequence=sequence,
                    type=RepairSessionEventType.SAFETY_ESCALATED.value,
                    data=validate_repair_session_event_data(
                        RepairSessionEventType.SAFETY_ESCALATED,
                        safety_update.event_data,
                    ),
                ),
            )

        sequence += 1
        phase_report_event = await self._events.add(
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
                        "schema_version": report.schema_version,
                        "phase": RepairSessionPhase.DIAGNOSTIC.value,
                        "summary": report.summary,
                    },
                ),
            ),
        )
        repair_session.latest_event_sequence = sequence
        return ReportPersistenceEvents(
            safety_escalated=safety_event,
            phase_report_created=phase_report_event,
        )

    async def _rollback_if_configured(self) -> None:
        """Rollback the current unit of work when one is configured."""

        if self._rollback is not None:
            await self._rollback()


def _validate_diagnostic_envelope(envelope: PhaseReportEnvelope) -> None:
    """Apply Stage 11 diagnostic report invariants before persistence."""

    if envelope.type is not PhaseReportType.DIAGNOSTIC:
        raise ValidationAppError()
    if envelope.schema_version not in {"diagnostic_report.v1", "diagnostic_report.v2"}:
        raise ValidationAppError()
    if envelope.phase is not RepairSessionPhase.DIAGNOSTIC:
        raise ValidationAppError()
    if not envelope.summary.strip():
        raise ValidationAppError()
    if not isinstance(envelope.payload, (DiagnosticReportV1, DiagnosticReportV2)):
        raise ValidationAppError()
    if envelope.payload.schema_version != envelope.schema_version:
        raise ValidationAppError()
    if [flag.model_dump(mode="json") for flag in envelope.payload.safety_flags] != [
        flag.model_dump(mode="json") for flag in envelope.safety_flags
    ]:
        raise ValidationAppError()


def _validate_plan_envelope(envelope: PhaseReportEnvelope) -> None:
    """Apply plan report invariants before public exposure."""

    if envelope.type is not PhaseReportType.PLAN:
        raise ValidationAppError()
    if envelope.schema_version != "plan_report.v1":
        raise ValidationAppError()
    if envelope.phase is not RepairSessionPhase.PLANNING:
        raise ValidationAppError()
    if not envelope.summary.strip():
        raise ValidationAppError()
    if not isinstance(envelope.payload, PlanReportV1):
        raise ValidationAppError()
    if envelope.payload.schema_version != "plan_report.v1":
        raise ValidationAppError()
    if [flag.model_dump(mode="json") for flag in envelope.payload.safety_concerns] != [
        flag.model_dump(mode="json") for flag in envelope.safety_flags
    ]:
        raise ValidationAppError()


def _payload_data(
    payload: DiagnosticReportV1 | DiagnosticReportV2 | dict[str, Any],
) -> dict[str, Any]:
    """Return mutable diagnostic report payload data."""

    if isinstance(payload, (DiagnosticReportV1, DiagnosticReportV2)):
        return payload.model_dump(mode="json")
    if isinstance(payload, dict):
        return dict(payload)
    raise ValidationAppError()


def _payload_safety_flags(payload: dict[str, Any]) -> list[SafetyFlag | dict[str, Any]]:
    """Return raw diagnostic payload safety flags before Pydantic validation."""

    safety_flags = payload.get("safety_flags")
    if not isinstance(safety_flags, list):
        raise ValidationAppError()
    return safety_flags


def _validate_diagnostic_payload(
    payload: dict[str, Any],
) -> DiagnosticReportV1 | DiagnosticReportV2:
    """Parse a diagnostic payload using its declared immutable schema version."""

    schema_version = payload.get("schema_version")
    if schema_version == "diagnostic_report.v1":
        return DiagnosticReportV1.model_validate(payload)
    if schema_version == "diagnostic_report.v2":
        return DiagnosticReportV2.model_validate(payload)
    raise ValueError("unsupported diagnostic report schema version")


def _report_artifact_ids(report: DiagnosticReportV1 | DiagnosticReportV2) -> list[str]:
    """Return all artifact references that require owner/session validation."""

    if isinstance(report, DiagnosticReportV1):
        return report.key_artifact_ids
    return [
        *report.key_artifact_ids,
        *(
            artifact_id
            for finding in report.observed_findings
            for artifact_id in finding.artifact_ids
        ),
    ]


def _price_requirements_from_diagnostic_report(
    report: DiagnosticReportV1,
) -> list[PriceLookupRequirement]:
    """Build simple price lookup requirements from diagnostic required items."""

    requirements: list[PriceLookupRequirement] = []
    for item in _unique_non_blank(report.repair_estimate.parts_required):
        requirements.append(
            _diagnostic_price_requirement(CostItemType.PART, item),
        )
    for item in _unique_non_blank(report.repair_estimate.tools_required):
        requirements.append(
            _diagnostic_price_requirement(CostItemType.TOOL, item),
        )
    return requirements


def _diagnostic_price_requirement(
    item_type: CostItemType,
    item: str,
) -> PriceLookupRequirement:
    """Create a conservative lookup request from a freeform required item."""

    return PriceLookupRequirement(
        item_type=item_type,
        display_name=item,
        quantity=1,
        generic_equivalent_acceptable=item_type is CostItemType.TOOL,
        exact_match_required=item_type is CostItemType.PART,
        planning_confidence=Confidence.MEDIUM,
        search_query=item,
    )


def _unique_non_blank(items: list[str]) -> list[str]:
    """Deduplicate freeform report items while preserving order."""

    unique: dict[str, str] = {}
    for item in items:
        normalized = " ".join(item.split())
        if normalized:
            unique.setdefault(normalized.casefold(), normalized)
    return list(unique.values())


def _public_envelope_or_server_error(
    report: PhaseReportModel,
) -> PhaseReportEnvelope:
    """Validate stored report data before public exposure."""

    try:
        public = phase_report_envelope_from_model(report)
        if public.type is PhaseReportType.DIAGNOSTIC:
            _validate_diagnostic_envelope(public)
            SafetyService().validate_report_safety_flags(
                payload_flags=public.payload.safety_flags
                if isinstance(public.payload, (DiagnosticReportV1, DiagnosticReportV2))
                else [],
                envelope_flags=public.safety_flags,
            )
        elif public.type is PhaseReportType.PLAN:
            _validate_plan_envelope(public)
        else:
            raise ValidationAppError()
    except (PydanticValidationError, ValueError, AppError) as exc:
        raise ServerError() from exc
    return public
