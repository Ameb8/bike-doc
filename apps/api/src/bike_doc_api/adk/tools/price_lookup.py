"""Planning price lookup ADK tool."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from bike_doc_api.adk.tools.common import (
    PlanningToolContext,
    normalize_tool_errors,
    parse_tool_input,
    tool_error,
    tool_success,
    validate_planning_tool_context,
    validation_error_details,
)
from bike_doc_api.core.errors import ValidationAppError
from bike_doc_api.schemas.report import PlanCostEstimate, PriceLookupRequirement


class LookupPlanPricesInput(BaseModel):
    """Internal top-level input schema for planning price lookup."""

    model_config = ConfigDict(extra="forbid")

    repair_session_id: str = Field(min_length=1)
    requirements: list[PriceLookupRequirement] = Field(min_length=1)


class CostEstimateServiceProtocol(Protocol):
    """Service boundary used by lookup_plan_prices."""

    async def estimate_plan_cost(
        self,
        requirements: list[PriceLookupRequirement],
    ) -> PlanCostEstimate:
        """Return item-level pricing and rollups for a plan."""


class LookupPlanPricesTool:
    """Thin ADK wrapper for planning cost estimation."""

    def __init__(self, service: CostEstimateServiceProtocol) -> None:
        self._service = service

    async def run(
        self,
        tool_input: LookupPlanPricesInput | Mapping[str, Any],
        context: PlanningToolContext,
    ) -> dict[str, Any]:
        """Run lookup_plan_prices and return the common tool envelope."""

        try:
            parsed: LookupPlanPricesInput = parse_tool_input(
                LookupPlanPricesInput,
                tool_input,
            )
            validate_planning_tool_context(
                repair_session_id=parsed.repair_session_id,
                context=context,
            )
        except ValidationError as exc:
            return tool_error(
                "validation_error",
                "Tool input validation failed.",
                validation_error_details(exc),
            )
        except ValidationAppError:
            return tool_error("validation_error", "Tool input validation failed.")

        async def call() -> dict[str, Any]:
            estimate = await self._service.estimate_plan_cost(parsed.requirements)
            return tool_success({"cost_estimate": estimate.model_dump(mode="json")})

        return await normalize_tool_errors(call)


async def lookup_plan_prices(
    tool_input: LookupPlanPricesInput | Mapping[str, Any],
    *,
    context: PlanningToolContext,
    service: CostEstimateServiceProtocol,
) -> dict[str, Any]:
    """Function-style entrypoint for lookup_plan_prices."""

    return await LookupPlanPricesTool(service).run(tool_input, context)
