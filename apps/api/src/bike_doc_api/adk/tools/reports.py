"""Report ADK tool boundary."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from bike_doc_api.adk.report_schemas.diagnostic import DiagnosticReportToolPayload
from bike_doc_api.adk.tools.common import (
    DiagnosticToolContext,
    ReportValidationToolError,
    current_tool_user,
    normalize_tool_errors,
    parse_tool_input,
    tool_error,
    tool_success,
    validate_tool_context,
)
from bike_doc_api.core.errors import ValidationAppError
from bike_doc_api.schemas.report import PhaseReportEnvelope, SafetyFlag


class SaveDiagnosticReportInput(BaseModel):
    """Internal top-level input schema for save_diagnostic_report."""

    model_config = ConfigDict(extra="forbid")

    repair_session_id: str = Field(min_length=1)
    report: dict[str, Any]
    summary: str = Field(min_length=1)


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
        summary: str,
        payload: dict[str, Any],
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
            parsed = parse_tool_input(SaveDiagnosticReportInput, tool_input)
            validate_tool_context(
                repair_session_id=parsed.repair_session_id,
                context=context,
            )
        except (ValidationError, ValidationAppError):
            return tool_error("validation_error", "Tool input validation failed.")

        async def call() -> dict[str, Any]:
            try:
                report_payload = DiagnosticReportToolPayload.model_validate(
                    parsed.report,
                )
            except ValidationError as exc:
                raise ReportValidationToolError() from exc

            payload = report_payload.model_dump(mode="json")
            payload["diagnostic_session_id"] = context.diagnostic_session_id
            result = await self._service.persist_diagnostic_report_from_tool(
                current_user=current_tool_user(context),
                repair_session_id=parsed.repair_session_id,
                diagnostic_session_id=context.diagnostic_session_id,
                summary=parsed.summary,
                payload=payload,
            )
            report = result.report
            if not isinstance(report.payload, dict):
                diagnostic_session_id = report.payload.diagnostic_session_id
            else:
                diagnostic_session_id = str(report.payload["diagnostic_session_id"])
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
