"""Diagnostic phase agent construction boundary."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from bike_doc_api.adk.report_schemas.diagnostic import DiagnosticReportToolPayload
from bike_doc_api.adk.tools.artifacts import (
    ArtifactServiceProtocol,
    ListDiagnosticArtifactsTool,
)
from bike_doc_api.adk.tools.bike_profile import (
    BikeProfileServiceProtocol,
    GetBikeProfileTool,
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
from bike_doc_api.core.config import Settings, get_settings

DIAGNOSTIC_AGENT_NAME = "diagnostic_agent"
DIAGNOSTIC_COMPLETION_TOOL_NAME = "save_diagnostic_report"
V1_DIAGNOSTIC_TOOL_NAMES = (
    "get_bike_profile",
    "lookup_repair_history",
    "list_diagnostic_artifacts",
    "request_diagnostic_input",
    "raise_safety_flag",
    DIAGNOSTIC_COMPLETION_TOOL_NAME,
)
_PROMPT_PATH = Path(__file__).resolve().parents[1] / "prompts" / "diagnostic.md"


@dataclass(frozen=True, slots=True)
class DiagnosticAgentToolDependencies:
    """Backend service dependencies required by diagnostic ADK tools."""

    bike_profile_service: BikeProfileServiceProtocol
    repair_history_service: RepairHistoryServiceProtocol
    artifact_service: ArtifactServiceProtocol
    input_request_service: DiagnosticInputRequestServiceProtocol
    safety_service: SafetyServiceProtocol
    report_service: DiagnosticReportServiceProtocol


@dataclass(frozen=True, slots=True)
class DiagnosticAgentToolSpec:
    """Registered diagnostic tool metadata for the ADK boundary."""

    name: str
    tool: Any


@dataclass(frozen=True, slots=True)
class DiagnosticCompletionCondition:
    """Stage 14 agent-side completion contract."""

    tool_name: str = DIAGNOSTIC_COMPLETION_TOOL_NAME
    backend_validates_and_persists_report: bool = True
    backend_emits_report_and_phase_transition_events: bool = True
    agent_side_only_for_stage_14: bool = True


@dataclass(frozen=True, slots=True)
class DiagnosticAgentDefinition:
    """Structural diagnostic agent definition consumed by Stage 15 orchestration."""

    name: str
    model: str
    instruction: str
    tools: tuple[DiagnosticAgentToolSpec, ...]
    output_schema: type[DiagnosticReportToolPayload]
    completion_condition: DiagnosticCompletionCondition

    @property
    def tool_names(self) -> tuple[str, ...]:
        """Return registered tool names in ADK registration order."""

        return tuple(tool.name for tool in self.tools)

    def as_adk_agent_kwargs(self) -> dict[str, Any]:
        """Return constructor kwargs for the eventual Google ADK agent adapter."""

        return {
            "name": self.name,
            "model": self.model,
            "instruction": self.instruction,
            "tools": [tool.tool for tool in self.tools],
        }


def load_diagnostic_prompt() -> str:
    """Load the versioned diagnostic prompt text."""

    return _PROMPT_PATH.read_text(encoding="utf-8").strip()


DIAGNOSTIC_PROMPT = load_diagnostic_prompt()


def create_diagnostic_agent(
    tool_dependencies: DiagnosticAgentToolDependencies,
    *,
    settings: Settings | None = None,
) -> DiagnosticAgentDefinition:
    """Create the internal diagnostic agent definition with V1 tools only."""

    resolved_settings = settings or get_settings()
    tools = (
        DiagnosticAgentToolSpec(
            name="get_bike_profile",
            tool=GetBikeProfileTool(tool_dependencies.bike_profile_service),
        ),
        DiagnosticAgentToolSpec(
            name="lookup_repair_history",
            tool=LookupRepairHistoryTool(tool_dependencies.repair_history_service),
        ),
        DiagnosticAgentToolSpec(
            name="list_diagnostic_artifacts",
            tool=ListDiagnosticArtifactsTool(tool_dependencies.artifact_service),
        ),
        DiagnosticAgentToolSpec(
            name="request_diagnostic_input",
            tool=RequestDiagnosticInputTool(tool_dependencies.input_request_service),
        ),
        DiagnosticAgentToolSpec(
            name="raise_safety_flag",
            tool=RaiseSafetyFlagTool(tool_dependencies.safety_service),
        ),
        DiagnosticAgentToolSpec(
            name=DIAGNOSTIC_COMPLETION_TOOL_NAME,
            tool=SaveDiagnosticReportTool(tool_dependencies.report_service),
        ),
    )
    return DiagnosticAgentDefinition(
        name=DIAGNOSTIC_AGENT_NAME,
        model=resolved_settings.diagnostic_agent_model,
        instruction=DIAGNOSTIC_PROMPT,
        tools=tools,
        output_schema=DiagnosticReportToolPayload,
        completion_condition=DiagnosticCompletionCondition(),
    )
