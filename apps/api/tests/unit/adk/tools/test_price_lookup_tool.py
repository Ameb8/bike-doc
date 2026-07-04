"""lookup_plan_prices ADK tool tests."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from bike_doc_api.adk.tools.common import PlanningToolContext
from bike_doc_api.adk.tools.price_lookup import LookupPlanPricesTool
from bike_doc_api.core.errors import SessionStateConflictError
from bike_doc_api.schemas.common import Confidence, RepairSessionPhase
from bike_doc_api.schemas.report import (
    CostEstimate,
    CostEstimateSource,
    CostItemType,
    PlanCostEstimate,
    PriceEstimateStatus,
    PriceLookupResult,
)


class _CostEstimateService:
    """Fake service for price lookup tool tests."""

    def __init__(self, *, error: Exception | None = None) -> None:
        self.error = error
        self.calls: list[list[Any]] = []

    async def estimate_plan_cost(self, requirements: list[Any]) -> PlanCostEstimate:
        self.calls.append(requirements)
        if self.error is not None:
            raise self.error
        return PlanCostEstimate(
            parts_total=CostEstimate(
                min_amount=27.99,
                max_amount=27.99,
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
                min_amount=27.99,
                max_amount=27.99,
                confidence=Confidence.HIGH,
                source=CostEstimateSource.SEARCH_PROVIDER,
            ),
            items=[
                PriceLookupResult(
                    item_type=CostItemType.PART,
                    requirement_name="Shimano HG54 10-speed chain",
                    quantity=1,
                    status=PriceEstimateStatus.PRICE_UNAVAILABLE,
                    estimate_confidence=Confidence.LOW,
                    looked_up_at=datetime(2026, 7, 4, tzinfo=UTC),
                ),
            ],
        )


def _context(**overrides: Any) -> PlanningToolContext:
    data = {
        "user_id": "usr_tool",
        "user_skill_level": "beginner",
        "repair_session_id": "rs_tool",
        "planning_session_id": "phs_plan",
        "diagnostic_report_id": "rpt_diag",
    }
    data.update(overrides)
    return PlanningToolContext.model_validate(data)


def _input(**overrides: Any) -> dict[str, Any]:
    data = {
        "repair_session_id": "rs_tool",
        "requirements": [
            {
                "item_type": "part",
                "display_name": "Shimano HG54 10-speed chain",
                "quantity": 1,
                "exact_match_required": True,
                "planning_confidence": "high",
                "search_query": "Shimano HG54 10-speed chain",
            },
        ],
    }
    data.update(overrides)
    return data


async def test_lookup_plan_prices_returns_normalized_cost_estimate() -> None:
    service = _CostEstimateService()

    result = await LookupPlanPricesTool(service).run(_input(), _context())

    assert result["ok"] is True
    assert result["data"]["cost_estimate"]["diy_total"]["min_amount"] == 27.99
    assert result["data"]["cost_estimate"]["items"][0]["requirement_name"] == (
        "Shimano HG54 10-speed chain"
    )
    assert service.calls[0][0].search_query == "Shimano HG54 10-speed chain"


async def test_lookup_plan_prices_rejects_context_mismatch() -> None:
    service = _CostEstimateService()

    result = await LookupPlanPricesTool(service).run(
        _input(repair_session_id="rs_other"),
        _context(),
    )

    assert result["ok"] is False
    assert result["error"]["code"] == "validation_error"
    assert service.calls == []


async def test_lookup_plan_prices_maps_invalid_phase() -> None:
    result = await LookupPlanPricesTool(
        _CostEstimateService(error=SessionStateConflictError()),
    ).run(_input(), _context(active_phase=RepairSessionPhase.PLANNING))

    assert result["ok"] is False
    assert result["error"]["code"] == "invalid_phase"
