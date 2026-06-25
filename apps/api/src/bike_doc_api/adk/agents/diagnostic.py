"""Diagnostic phase agent construction boundary."""

from __future__ import annotations

from pathlib import Path

from google.adk.agents import Agent

from bike_doc_api.adk.tools.tool_catalog import (
    V1_DIAGNOSTIC_TOOL_NAMES,
    DiagnosticAgentToolDependencies,
    build_tool_catalog,
)
from bike_doc_api.core.config import Settings, get_settings

DIAGNOSTIC_AGENT_NAME = "diagnostic_agent"
DIAGNOSTIC_COMPLETION_TOOL_NAME = "save_diagnostic_report"
_PROMPT_PATH = Path(__file__).resolve().parents[1] / "prompts" / "diagnostic.md"
__all__ = [
    "DIAGNOSTIC_AGENT_NAME",
    "DIAGNOSTIC_COMPLETION_TOOL_NAME",
    "DIAGNOSTIC_PROMPT",
    "V1_DIAGNOSTIC_TOOL_NAMES",
    "DiagnosticAgentToolDependencies",
    "create_diagnostic_agent",
    "load_diagnostic_prompt",
]


def load_diagnostic_prompt() -> str:
    """Load the versioned diagnostic prompt text."""

    return _PROMPT_PATH.read_text(encoding="utf-8").strip()


DIAGNOSTIC_PROMPT = load_diagnostic_prompt()


def create_diagnostic_agent(
    tool_dependencies: DiagnosticAgentToolDependencies,
    *,
    settings: Settings | None = None,
) -> Agent:
    """Create the real Google ADK diagnostic agent with V1 tools only."""

    resolved_settings = settings or get_settings()
    return Agent(
        name=DIAGNOSTIC_AGENT_NAME,
        model=resolved_settings.diagnostic_agent_model,
        instruction=DIAGNOSTIC_PROMPT,
        tools=list(build_tool_catalog(tool_dependencies)),
    )
