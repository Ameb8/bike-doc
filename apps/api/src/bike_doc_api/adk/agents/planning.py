"""Planning phase agent construction boundary."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from google.adk.agents import Agent
from google.adk.tools.function_tool import FunctionTool
from google.adk.tools.tool_context import ToolContext
from pydantic import ValidationError

from bike_doc_api.adk.tools.common import PlanningToolContext, tool_error
from bike_doc_api.adk.tools.price_lookup import (
    CostEstimateServiceProtocol,
    LookupPlanPricesTool,
)
from bike_doc_api.core.config import Settings, get_settings

PLANNING_AGENT_NAME = "planning_agent"
V1_PLANNING_TOOL_NAMES = ("lookup_plan_prices",)
_PROMPT_PATH = Path(__file__).resolve().parents[1] / "prompts" / "planning.md"
__all__ = [
    "PLANNING_AGENT_NAME",
    "PLANNING_PROMPT",
    "V1_PLANNING_TOOL_NAMES",
    "PlanningAgentToolDependencies",
    "build_planning_tool_catalog",
    "create_planning_agent",
    "load_planning_prompt",
]


@dataclass(frozen=True, slots=True)
class PlanningAgentToolDependencies:
    """Backend service dependencies required by planning ADK tools."""

    cost_estimate_service: CostEstimateServiceProtocol


def load_planning_prompt() -> str:
    """Load the versioned planning prompt text."""

    return _PROMPT_PATH.read_text(encoding="utf-8").strip()


PLANNING_PROMPT = load_planning_prompt()


def create_planning_agent(
    tool_dependencies: PlanningAgentToolDependencies,
    *,
    settings: Settings | None = None,
) -> Agent:
    """Create the real Google ADK planning agent with V1 tools."""

    resolved_settings = settings or get_settings()
    return Agent(
        name=PLANNING_AGENT_NAME,
        model=resolved_settings.diagnostic_agent_model,
        instruction=PLANNING_PROMPT,
        tools=list(build_planning_tool_catalog(tool_dependencies)),
    )


def build_planning_tool_catalog(
    dependencies: PlanningAgentToolDependencies,
) -> tuple[FunctionTool, ...]:
    """Build the V1 planning ADK FunctionTool catalog."""

    price_lookup_tool = LookupPlanPricesTool(dependencies.cost_estimate_service)

    async def lookup_plan_prices(
        requirements: list[dict[str, Any]],
        tool_context: ToolContext | None = None,
    ) -> dict[str, Any]:
        """Return live price evidence and cost rollups for plan requirements."""

        context = _context_from_tool_context(tool_context)
        if isinstance(context, dict):
            return context
        return await price_lookup_tool.run(
            {
                "repair_session_id": context.repair_session_id,
                "requirements": requirements,
            },
            context,
        )

    return (FunctionTool(lookup_plan_prices),)


def _context_from_tool_context(
    tool_context: ToolContext | None,
) -> PlanningToolContext | dict[str, Any]:
    """Extract and validate app-owned planning context from ADK tool state."""

    try:
        state = getattr(tool_context, "state", None)
        state_get = getattr(state, "get", None)
        if not callable(state_get):
            return _context_error()
        app_context = state_get("app_context")
        if app_context is None:
            return _context_error()
        return PlanningToolContext.model_validate(app_context)
    except (AttributeError, TypeError, ValueError, ValidationError):
        return _context_error()


def _context_error() -> dict[str, Any]:
    """Return the normalized error for absent or malformed ADK app context."""

    return tool_error(
        "validation_error",
        "Tool app context is missing or invalid.",
    )
