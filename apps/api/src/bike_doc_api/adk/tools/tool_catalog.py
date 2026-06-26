"""Diagnostic ADK FunctionTool catalog."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from google.adk.tools.function_tool import FunctionTool
from google.adk.tools.tool_context import ToolContext
from pydantic import ValidationError

from bike_doc_api.adk.tools.artifacts import (
    ArtifactServiceProtocol,
    ListDiagnosticArtifactsTool,
)
from bike_doc_api.adk.tools.bike_profile import (
    BikeProfileServiceProtocol,
    GetBikeProfileTool,
)
from bike_doc_api.adk.tools.common import (
    DiagnosticToolContext,
    tool_error,
)
from bike_doc_api.adk.tools.input_requests import (
    DiagnosticInputRequestServiceProtocol,
    RequestDiagnosticInputTool,
)
from bike_doc_api.adk.tools.repair_history import (
    LookupRepairHistoryTool,
    RepairHistoryServiceProtocol,
)
from bike_doc_api.adk.tools.reports import (
    DiagnosticReportServiceProtocol,
    SaveDiagnosticReportTool,
)
from bike_doc_api.adk.tools.safety import RaiseSafetyFlagTool, SafetyServiceProtocol
from bike_doc_api.schemas.common import ArtifactPurpose

V1_DIAGNOSTIC_TOOL_NAMES = (
    "get_bike_profile",
    "lookup_repair_history",
    "list_diagnostic_artifacts",
    "request_diagnostic_input",
    "raise_safety_flag",
    "save_diagnostic_report",
)


@dataclass(frozen=True, slots=True)
class DiagnosticAgentToolDependencies:
    """Backend service dependencies required by diagnostic ADK tools."""

    bike_profile_service: BikeProfileServiceProtocol
    repair_history_service: RepairHistoryServiceProtocol
    artifact_service: ArtifactServiceProtocol
    input_request_service: DiagnosticInputRequestServiceProtocol
    safety_service: SafetyServiceProtocol
    report_service: DiagnosticReportServiceProtocol


def build_tool_catalog(
    dependencies: DiagnosticAgentToolDependencies,
) -> tuple[FunctionTool, ...]:
    """Build the V1 diagnostic ADK FunctionTool catalog."""

    bike_profile_tool = GetBikeProfileTool(dependencies.bike_profile_service)
    repair_history_tool = LookupRepairHistoryTool(
        dependencies.repair_history_service,
    )
    artifacts_tool = ListDiagnosticArtifactsTool(dependencies.artifact_service)
    input_request_tool = RequestDiagnosticInputTool(
        dependencies.input_request_service,
    )
    safety_tool = RaiseSafetyFlagTool(dependencies.safety_service)
    report_tool = SaveDiagnosticReportTool(dependencies.report_service)

    async def get_bike_profile(tool_context: ToolContext) -> dict[str, Any]:
        """Return bike profile context for the active repair session."""

        context = _context_from_tool_context(tool_context)
        if isinstance(context, dict):
            return context
        return await bike_profile_tool.run(
            {"repair_session_id": context.repair_session_id},
            context,
        )

    async def lookup_repair_history(
        component_terms: list[str] | None = None,
        limit: int = 5,
        tool_context: ToolContext | None = None,
    ) -> dict[str, Any]:
        """Return relevant prior repair records for the active bike."""

        context = _context_from_tool_context(tool_context)
        if isinstance(context, dict):
            return context
        return await repair_history_tool.run(
            {
                "repair_session_id": context.repair_session_id,
                "component_terms": component_terms or [],
                "limit": limit,
            },
            context,
        )

    async def list_diagnostic_artifacts(
        purpose: str = ArtifactPurpose.DIAGNOSTIC_PHOTO.value,
        tool_context: ToolContext | None = None,
    ) -> dict[str, Any]:
        """Return diagnostic artifact metadata for the active repair session."""

        context = _context_from_tool_context(tool_context)
        if isinstance(context, dict):
            return context
        return await artifacts_tool.run(
            {
                "repair_session_id": context.repair_session_id,
                "purpose": purpose,
            },
            context,
        )

    async def request_diagnostic_input(
        type: str,
        prompt: str = "",
        required: bool = True,
        accepted_media_types: list[str] | None = None,
        choices: list[dict[str, Any]] | None = None,
        min_artifacts: int | None = None,
        max_artifacts: int | None = None,
        tool_context: ToolContext | None = None,
    ) -> dict[str, Any]:
        """Persist a structured request for more diagnostic input."""

        context = _context_from_tool_context(tool_context)
        if isinstance(context, dict):
            return context
        return await input_request_tool.run(
            {
                "repair_session_id": context.repair_session_id,
                "type": type,
                "prompt": prompt,
                "required": required,
                "accepted_media_types": accepted_media_types or [],
                "choices": choices or [],
                "min_artifacts": min_artifacts,
                "max_artifacts": max_artifacts,
            },
            context,
        )

    async def raise_safety_flag(
        safety_flag: dict[str, Any],
        tool_context: ToolContext | None = None,
    ) -> dict[str, Any]:
        """Persist a diagnostic safety flag through backend safety rules."""

        context = _context_from_tool_context(tool_context)
        if isinstance(context, dict):
            return context
        return await safety_tool.run(
            {
                "repair_session_id": context.repair_session_id,
                "safety_flag": safety_flag,
            },
            context,
        )

    async def save_diagnostic_report(
        report: dict[str, Any],
        summary: str,
        tool_context: ToolContext | None = None,
    ) -> dict[str, Any]:
        """Persist the completed diagnostic report for the active phase session."""

        context = _context_from_tool_context(tool_context)
        if isinstance(context, dict):
            return context
        return await report_tool.run(
            {
                "repair_session_id": context.repair_session_id,
                "report": report,
                "summary": summary,
            },
            context,
        )

    return (
        FunctionTool(get_bike_profile),
        FunctionTool(lookup_repair_history),
        FunctionTool(list_diagnostic_artifacts),
        FunctionTool(request_diagnostic_input),
        FunctionTool(raise_safety_flag),
        FunctionTool(save_diagnostic_report),
    )


def _context_from_tool_context(
    tool_context: ToolContext | None,
) -> DiagnosticToolContext | dict[str, Any]:
    """Extract and validate app-owned context from ADK tool state."""

    try:
        state = getattr(tool_context, "state", None)
        state_get = getattr(state, "get", None)
        if not callable(state_get):
            return _context_error()
        app_context = state_get("app_context")
        if app_context is None:
            return _context_error()
        return DiagnosticToolContext.model_validate(app_context)
    except (AttributeError, TypeError, ValueError, ValidationError):
        return _context_error()


def _context_error() -> dict[str, Any]:
    """Return the normalized error for absent or malformed ADK app context."""

    return tool_error(
        "validation_error",
        "Tool app context is missing or invalid.",
    )
