"""Cost estimate service tests."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from bike_doc_api.schemas.common import Confidence
from bike_doc_api.schemas.report import (
    CostEstimate,
    CostEstimateSource,
    CostItemType,
    PriceEstimateStatus,
    PriceListing,
    PriceLookupRequirement,
    PriceLookupResult,
)
from bike_doc_api.services.cost_estimates import CostEstimateService


class _PriceProvider:
    """Fake provider for cost estimate tests."""

    def __init__(self, results: dict[str, PriceLookupResult | Exception]) -> None:
        self.results = results
        self.calls: list[PriceLookupRequirement] = []

    async def lookup_requirement(
        self,
        requirement: PriceLookupRequirement,
    ) -> PriceLookupResult:
        self.calls.append(requirement)
        result = self.results[requirement.display_name]
        if isinstance(result, Exception):
            raise result
        return result


async def test_estimate_plan_cost_aggregates_listings_by_item_type() -> None:
    provider = _PriceProvider(
        {
            "Shimano HG54 10-speed chain": _listing_result(
                "part",
                "Shimano HG54 10-speed chain",
                27.99,
                Confidence.HIGH,
            ),
            "Chain checker": _listing_result(
                "tool",
                "Chain checker",
                17.95,
                Confidence.MEDIUM,
                generic_substitute_used=True,
            ),
        },
    )

    result = await CostEstimateService(provider).estimate_plan_cost(
        [
            _requirement("part", "Shimano HG54 10-speed chain"),
            _requirement(
                "tool",
                "Chain checker",
                generic_equivalent_acceptable=True,
            ),
        ],
    )

    assert result.parts_total.min_amount == 27.99
    assert result.tools_total.max_amount == 17.95
    assert result.diy_total.min_amount == 45.94
    assert result.diy_total.confidence == Confidence.MEDIUM
    assert result.items[1].generic_substitute_used is True
    assert [call.display_name for call in provider.calls] == [
        "Shimano HG54 10-speed chain",
        "Chain checker",
    ]


async def test_estimate_plan_cost_uses_ranges_and_marks_uncertain_total() -> None:
    provider = _PriceProvider(
        {
            "Brake pads": PriceLookupResult(
                item_type=CostItemType.PART,
                requirement_name="Brake pads",
                quantity=2,
                status=PriceEstimateStatus.RANGE_ESTIMATE_ONLY,
                estimate_confidence=Confidence.LOW,
                looked_up_at=datetime(2026, 7, 4, tzinfo=UTC),
                estimated_price=CostEstimate(
                    min_amount=12.0,
                    max_amount=20.0,
                    confidence=Confidence.LOW,
                    source=CostEstimateSource.SEARCH_PROVIDER,
                ),
                compatibility_uncertain=True,
            ),
        },
    )

    result = await CostEstimateService(provider).estimate_plan_cost(
        [_requirement("part", "Brake pads", quantity=2)],
    )

    assert result.parts_total.min_amount == 24.0
    assert result.parts_total.max_amount == 40.0
    assert result.parts_total.confidence == Confidence.LOW
    assert result.parts_total.notes is not None
    assert result.tools_total.min_amount == 0.0
    assert result.diy_total.max_amount == 40.0


async def test_estimate_plan_cost_degrades_provider_failure_per_item() -> None:
    provider = _PriceProvider(
        {
            "Cassette lockring tool": RuntimeError("provider unavailable"),
        },
    )

    result = await CostEstimateService(provider).estimate_plan_cost(
        [_requirement("tool", "Cassette lockring tool")],
    )

    item = result.items[0]
    assert item.status == PriceEstimateStatus.PRICE_UNAVAILABLE
    assert item.requirement_name == "Cassette lockring tool"
    assert result.tools_total.source == CostEstimateSource.UNAVAILABLE
    assert result.diy_total.confidence == Confidence.LOW


async def test_provider_result_identity_is_aligned_to_requirement() -> None:
    provider = _PriceProvider(
        {
            "700x38c tube, presta valve": _listing_result(
                "tool",
                "Wrong item",
                8.0,
                Confidence.LOW,
            ),
        },
    )

    result = await CostEstimateService(provider).estimate_plan_cost(
        [
            _requirement(
                "part",
                "700x38c tube, presta valve",
                exact_match_required=True,
            ),
        ],
    )

    item = result.items[0]
    assert item.item_type == CostItemType.PART
    assert item.requirement_name == "700x38c tube, presta valve"
    assert item.exact_match_not_confirmed is True
    assert item.compatibility_uncertain is True


def _requirement(item_type: str, name: str, **overrides: Any) -> PriceLookupRequirement:
    data = {
        "item_type": item_type,
        "display_name": name,
        "quantity": 1,
        "search_query": name,
    }
    data.update(overrides)
    return PriceLookupRequirement.model_validate(data)


def _listing_result(
    item_type: str,
    name: str,
    amount: float,
    confidence: Confidence,
    **overrides: Any,
) -> PriceLookupResult:
    data = {
        "item_type": item_type,
        "requirement_name": name,
        "quantity": 1,
        "status": "priced_listing_found",
        "estimate_confidence": confidence,
        "looked_up_at": datetime(2026, 7, 4, tzinfo=UTC),
        "primary_listing": PriceListing(
            title=name,
            retailer="Example Retailer",
            observed_price=amount,
            currency="USD",
            url="https://example.com/listing",
            observed_at=datetime(2026, 7, 4, tzinfo=UTC),
            match_confidence=confidence,
            match_rationale="Listing title matches the requirement.",
        ),
    }
    data.update(overrides)
    return PriceLookupResult.model_validate(data)
