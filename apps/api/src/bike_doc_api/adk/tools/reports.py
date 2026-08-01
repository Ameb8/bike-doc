"""Report ADK tool boundary."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal, Protocol, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

from bike_doc_api.adk.report_schemas.diagnostic import (
    DiagnosticReportToolPayload,
    DiagnosticReportV2ToolPayload,
)
from bike_doc_api.adk.tools.common import (
    DiagnosticToolContext,
    ReportValidationToolError,
    current_tool_user,
    normalize_tool_errors,
    parse_tool_input,
    tool_error,
    tool_success,
    validate_tool_context,
    validation_error_details,
)
from bike_doc_api.core.errors import SessionStateConflictError, ValidationAppError
from bike_doc_api.schemas.report import (
    DiagnosticReportV1,
    DiagnosticReportV2,
    PhaseReportEnvelope,
    SafetyFlag,
)

CompletionReason = Literal[
    "diagnosis_supported",
    "user_declined_more_input",
    "requested_input_unavailable",
    "in_person_assessment_required",
]


class CompletionBasis(BaseModel):
    """Internal, concise attestation that a diagnostic report is ready to save."""

    model_config = ConfigDict(extra="forbid")

    completion_reason: CompletionReason
    material_hypotheses_considered: list[str]
    readily_obtainable_material_evidence_missing: bool
    why_ready: str = Field(min_length=1)

    @field_validator("material_hypotheses_considered")
    @classmethod
    def require_non_blank_hypothesis_labels(cls, labels: list[str]) -> list[str]:
        """Reject blank labels without trying to judge their diagnostic truth."""

        if any(not label.strip() for label in labels):
            msg = "material_hypotheses_considered entries must be non-blank"
            raise ValueError(msg)
        return labels

    @field_validator("why_ready")
    @classmethod
    def require_non_blank_readiness_rationale(cls, rationale: str) -> str:
        """Require a concise readiness rationale suitable for ordinary traces."""

        if not rationale.strip():
            msg = "why_ready must be non-blank"
            raise ValueError(msg)
        return rationale

    @model_validator(mode="after")
    def validate_supported_completion(self) -> Self:
        """Apply completion-reason invariants available before report V2 exists."""

        if self.completion_reason != "diagnosis_supported":
            return self
        if not self.material_hypotheses_considered:
            msg = (
                "diagnosis_supported requires material_hypotheses_considered "
                "to be non-empty"
            )
            raise ValueError(msg)
        if self.readily_obtainable_material_evidence_missing:
            msg = (
                "diagnosis_supported cannot retain readily obtainable material "
                "evidence gaps"
            )
            raise ValueError(msg)
        return self


class SaveDiagnosticReportInput(BaseModel):
    """Internal top-level input schema for save_diagnostic_report."""

    model_config = ConfigDict(extra="forbid")

    repair_session_id: str = Field(min_length=1)
    report: dict[str, Any]
    summary: str | None = None
    completion_basis: CompletionBasis


class ReportEventProtocol(Protocol):
    """Event metadata shape returned by report persistence."""

    id: str
    sequence: int


class ReportPersistenceEventsProtocol(Protocol):
    """Report persistence event shape required by this tool."""

    phase_report_created: ReportEventProtocol
    phase_transitioned: ReportEventProtocol | None


class DiagnosticReportPersistenceResultProtocol(Protocol):
    """Service result shape required by this tool."""

    report: PhaseReportEnvelope
    events: ReportPersistenceEventsProtocol
    safety_state: str
    active_safety_flags: list[SafetyFlag]


class DiagnosticReportServiceProtocol(Protocol):
    """Service boundary used by save_diagnostic_report."""

    async def persist_diagnostic_report_from_tool(
        self,
        *,
        current_user: Any,
        repair_session_id: str,
        diagnostic_session_id: str,
        summary: str | None,
        payload: dict[str, Any],
        report_schema_version: Literal["diagnostic_report.v1", "diagnostic_report.v2"],
        completion_reason: str | None = None,
        turn_id: str | None = None,
    ) -> DiagnosticReportPersistenceResultProtocol:
        """Persist a diagnostic report with server-owned context injected."""


class SaveDiagnosticReportTool:
    """Thin ADK wrapper for diagnostic report persistence."""

    def __init__(self, service: DiagnosticReportServiceProtocol) -> None:
        self._service = service

    async def run(
        self,
        tool_input: SaveDiagnosticReportInput | Mapping[str, Any],
        context: DiagnosticToolContext,
    ) -> dict[str, Any]:
        """Run save_diagnostic_report and return the common tool envelope."""

        try:
            parsed: SaveDiagnosticReportInput = parse_tool_input(
                SaveDiagnosticReportInput,
                tool_input,
            )
            validate_tool_context(
                repair_session_id=parsed.repair_session_id,
                context=context,
            )
        except ValidationError as exc:
            details = validation_error_details(exc)
            if any(
                field["path"].startswith("completion_basis")
                for field in details["fields"]
            ):
                return tool_error(
                    "report_validation_failed",
                    "Diagnostic report validation failed.",
                    details,
                )
            return tool_error("validation_error", "Tool input validation failed.")
        except ValidationAppError:
            return tool_error("validation_error", "Tool input validation failed.")
        except SessionStateConflictError:
            return tool_error("invalid_phase", "Diagnostic phase is not active.")

        async def call() -> dict[str, Any]:
            expected_version = context.diagnostic_report_schema_version
            if parsed.report.get("schema_version") != expected_version:
                raise ReportValidationToolError()
            try:
                if expected_version == "diagnostic_report.v1":
                    if parsed.summary is None or not parsed.summary.strip():
                        raise ReportValidationToolError()
                    report_payload: (
                        DiagnosticReportToolPayload | DiagnosticReportV2ToolPayload
                    ) = DiagnosticReportToolPayload.model_validate(
                        parsed.report,
                    )
                else:
                    if parsed.summary is not None:
                        raise ReportValidationToolError()
                    report_payload = DiagnosticReportV2ToolPayload.model_validate(
                        parsed.report,
                    )
            except ValidationError as exc:
                raise ReportValidationToolError(
                    validation_error_details(exc, prefix="report"),
                ) from exc

            payload = report_payload.model_dump(mode="json")
            # The completion basis is intentionally never persisted or returned.
            result = await self._service.persist_diagnostic_report_from_tool(
                current_user=current_tool_user(context),
                repair_session_id=parsed.repair_session_id,
                diagnostic_session_id=context.diagnostic_session_id,
                summary=parsed.summary,
                payload=payload,
                report_schema_version=expected_version,
                completion_reason=(
                    parsed.completion_basis.completion_reason
                    if expected_version == "diagnostic_report.v2"
                    else None
                ),
                turn_id=context.turn_id,
            )
            report = result.report
            if isinstance(report.payload, (DiagnosticReportV1, DiagnosticReportV2)):
                diagnostic_session_id = report.payload.diagnostic_session_id
            elif isinstance(report.payload, dict):
                diagnostic_session_id = str(report.payload["diagnostic_session_id"])
            else:
                raise ReportValidationToolError()
            data: dict[str, Any] = {
                "report_id": report.id,
                "schema_version": report.schema_version,
                "diagnostic_session_id": diagnostic_session_id,
                "safety_state": result.safety_state,
                "safety_flags": [
                    flag.model_dump(mode="json") for flag in result.active_safety_flags
                ],
                "phase_report_created_event_id": (
                    result.events.phase_report_created.id
                ),
                "phase_report_created_event_sequence": (
                    result.events.phase_report_created.sequence
                ),
            }
            if result.events.phase_transitioned is not None:
                data["phase_transitioned_event_id"] = (
                    result.events.phase_transitioned.id
                )
                data["phase_transitioned_event_sequence"] = (
                    result.events.phase_transitioned.sequence
                )
            return tool_success(data)

        return await normalize_tool_errors(
            call,
            validation_error_code="report_validation_failed",
        )


async def save_diagnostic_report(
    tool_input: SaveDiagnosticReportInput | Mapping[str, Any],
    *,
    context: DiagnosticToolContext,
    service: DiagnosticReportServiceProtocol,
) -> dict[str, Any]:
    """Function-style entrypoint for save_diagnostic_report."""

    return await SaveDiagnosticReportTool(service).run(tool_input, context)
