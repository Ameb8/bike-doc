"""Planning agent structural tests."""

from __future__ import annotations

from google.adk.agents import Agent
from google.adk.tools import FunctionTool

from bike_doc_api.adk.agents.planning import (
    PLANNING_AGENT_NAME,
    PLANNING_PROMPT,
    V1_PLANNING_TOOL_NAMES,
    PlanningAgentToolDependencies,
    create_planning_agent,
    load_planning_prompt,
)
from bike_doc_api.schemas.common import Confidence
from bike_doc_api.schemas.report import (
    CostEstimate,
    CostEstimateSource,
    PlanCostEstimate,
    PriceLookupRequirement,
)


class _FakeCostEstimateService:
    """Fake planning service dependency for structural tests."""

    async def estimate_plan_cost(
        self,
        requirements: list[PriceLookupRequirement],
    ) -> PlanCostEstimate:
        _ = requirements
        return PlanCostEstimate(
            parts_total=CostEstimate(
                min_amount=0,
                max_amount=0,
                confidence=Confidence.HIGH,
                source=CostEstimateSource.SEARCH_PROVIDER,
            ),
            tools_total=CostEstimate(
                min_amount=0,
                max_amount=0,
                confidence=Confidence.HIGH,
                source=CostEstimateSource.SEARCH_PROVIDER,
            ),
            diy_total=CostEstimate(
                min_amount=0,
                max_amount=0,
                confidence=Confidence.HIGH,
                source=CostEstimateSource.SEARCH_PROVIDER,
            ),
            items=[],
        )


def _dependencies() -> PlanningAgentToolDependencies:
    return PlanningAgentToolDependencies(
        cost_estimate_service=_FakeCostEstimateService(),
    )


def _function_tools(agent: Agent) -> list[FunctionTool]:
    return [tool for tool in agent.tools if isinstance(tool, FunctionTool)]


def test_planning_agent_constructs_with_price_lookup_tool() -> None:
    agent = create_planning_agent(_dependencies())

    assert isinstance(agent, Agent)
    assert agent.name == PLANNING_AGENT_NAME
    assert agent.instruction == PLANNING_PROMPT
    assert tuple(tool.name for tool in _function_tools(agent)) == V1_PLANNING_TOOL_NAMES


def test_planning_prompt_file_is_loaded_by_agent_module() -> None:
    prompt = load_planning_prompt()

    assert prompt == PLANNING_PROMPT
    assert "lookup_plan_prices" in prompt
    assert "plan_report.v1" in prompt


def test_planning_prompt_contains_price_lookup_rules() -> None:
    prompt = " ".join(PLANNING_PROMPT.split())

    required_fragments = [
        "Normalize every required tool and part",
        "Call `lookup_plan_prices`",
        "observed market evidence",
        "compatibility proof",
        "exact_match_required: true",
        "generic_equivalent_acceptable: true",
        "search_query",
        "cost_estimate",
    ]
    for fragment in required_fragments:
        assert fragment in prompt
