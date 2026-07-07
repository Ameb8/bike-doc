"""Planning cost-estimate behavior."""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

from bike_doc_api.core.errors import ValidationAppError
from bike_doc_api.providers.price.base import PriceLookupProvider
from bike_doc_api.schemas.common import Confidence
from bike_doc_api.schemas.report import (
    CostEstimate,
    CostEstimateSource,
    CostItemType,
    PlanCostEstimate,
    PriceEstimateStatus,
    PriceLookupRequirement,
    PriceLookupResult,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class _RollupTotals:
    """Accumulated total range for one item type."""

    min_amount: float | None
    max_amount: float | None
    confidence: Confidence
    source: CostEstimateSource
    uncertain: bool


class CostEstimateService:
    """Build item-level lookup results and plan-level cost rollups."""

    def __init__(self, provider: PriceLookupProvider) -> None:
        self._provider = provider

    async def estimate_plan_cost(
        self,
        requirements: Sequence[PriceLookupRequirement | dict[str, object]],
    ) -> PlanCostEstimate:
        """Price each requirement independently and aggregate the plan total."""

        normalized = [
            _normalize_requirement(requirement) for requirement in requirements
        ]
        if not normalized:
            raise ValidationAppError()

        logger.info(
            "plan_cost_estimate_started",
            extra={
                "requirement_count": len(normalized),
                "requirement_names": [item.display_name for item in normalized],
            },
        )
        results: list[PriceLookupResult] = []
        for requirement in normalized:
            results.append(await self._lookup_with_degradation(requirement))

        parts_total = _roll_up(results, CostItemType.PART)
        tools_total = _roll_up(results, CostItemType.TOOL)
        estimate = PlanCostEstimate(
            parts_total=_cost_estimate_from_rollup(parts_total, "parts"),
            tools_total=_cost_estimate_from_rollup(tools_total, "tools"),
            diy_total=_merge_rollups(parts_total, tools_total),
            items=results,
        )
        logger.info(
            "plan_cost_estimate_completed",
            extra={
                "item_count": len(results),
                "priced_item_count": sum(
                    1
                    for item in results
                    if item.status is PriceEstimateStatus.PRICED_LISTING_FOUND
                ),
                "range_item_count": sum(
                    1
                    for item in results
                    if item.status is PriceEstimateStatus.RANGE_ESTIMATE_ONLY
                ),
                "unavailable_item_count": sum(
                    1
                    for item in results
                    if item.status is PriceEstimateStatus.PRICE_UNAVAILABLE
                ),
                "needs_more_detail_count": sum(
                    1
                    for item in results
                    if item.status is PriceEstimateStatus.NEEDS_MORE_DETAIL
                ),
                "parts_total_min_amount": estimate.parts_total.min_amount,
                "parts_total_max_amount": estimate.parts_total.max_amount,
                "tools_total_min_amount": estimate.tools_total.min_amount,
                "tools_total_max_amount": estimate.tools_total.max_amount,
                "diy_total_min_amount": estimate.diy_total.min_amount,
                "diy_total_max_amount": estimate.diy_total.max_amount,
                "diy_total_confidence": estimate.diy_total.confidence.value,
            },
        )
        return estimate

    async def _lookup_with_degradation(
        self,
        requirement: PriceLookupRequirement,
    ) -> PriceLookupResult:
        """Lookup one item and turn provider failures into explicit uncertainty."""

        try:
            logger.info(
                "plan_cost_item_lookup_started",
                extra={
                    "item_type": requirement.item_type.value,
                    "requirement_name": requirement.display_name,
                    "quantity": requirement.quantity,
                    "search_query": requirement.search_query,
                },
            )
            result = await self._provider.lookup_requirement(requirement)
            aligned_result = _align_result_with_requirement(result, requirement)
            logger.info(
                "plan_cost_item_lookup_completed",
                extra={
                    "item_type": aligned_result.item_type.value,
                    "requirement_name": aligned_result.requirement_name,
                    "status": aligned_result.status.value,
                    "estimate_confidence": aligned_result.estimate_confidence.value,
                    "primary_listing_price": (
                        aligned_result.primary_listing.observed_price
                        if aligned_result.primary_listing is not None
                        else None
                    ),
                    "estimated_min_amount": (
                        aligned_result.estimated_price.min_amount
                        if aligned_result.estimated_price is not None
                        else None
                    ),
                    "estimated_max_amount": (
                        aligned_result.estimated_price.max_amount
                        if aligned_result.estimated_price is not None
                        else None
                    ),
                },
            )
            return aligned_result
        except ValidationAppError:
            raise
        except Exception:
            logger.info(
                "plan_cost_item_lookup_degraded",
                extra={
                    "item_type": requirement.item_type.value,
                    "requirement_name": requirement.display_name,
                    "search_query": requirement.search_query,
                },
                exc_info=True,
            )
            return _unavailable_result(requirement)


def _normalize_requirement(
    requirement: PriceLookupRequirement | dict[str, object],
) -> PriceLookupRequirement:
    """Validate a raw planning requirement."""

    try:
        if isinstance(requirement, PriceLookupRequirement):
            return requirement
        return PriceLookupRequirement.model_validate(requirement)
    except ValueError as exc:
        raise ValidationAppError() from exc


def _align_result_with_requirement(
    result: PriceLookupResult,
    requirement: PriceLookupRequirement,
) -> PriceLookupResult:
    """Prevent provider output from changing the requirement identity."""

    result_data = result.model_dump(mode="python")
    result_data["item_type"] = requirement.item_type
    result_data["requirement_name"] = requirement.display_name
    result_data["quantity"] = requirement.quantity
    if requirement.exact_match_required and result.primary_listing is not None:
        result_data["exact_match_not_confirmed"] = (
            result.exact_match_not_confirmed
            or result.estimate_confidence in {Confidence.UNKNOWN, Confidence.LOW}
        )
        result_data["compatibility_uncertain"] = (
            result.compatibility_uncertain or result_data["exact_match_not_confirmed"]
        )
    return PriceLookupResult.model_validate(result_data)


def _unavailable_result(requirement: PriceLookupRequirement) -> PriceLookupResult:
    """Return an explicit unavailable result for failed provider lookups."""

    return PriceLookupResult(
        item_type=requirement.item_type,
        requirement_name=requirement.display_name,
        quantity=requirement.quantity,
        status=PriceEstimateStatus.PRICE_UNAVAILABLE,
        estimate_confidence=Confidence.LOW,
        looked_up_at=datetime.now(UTC),
        compatibility_uncertain=requirement.item_type is CostItemType.PART,
        exact_match_not_confirmed=requirement.exact_match_required,
    )


def _roll_up(
    results: Sequence[PriceLookupResult],
    item_type: CostItemType,
) -> _RollupTotals:
    """Aggregate the best available estimate for all items of one type."""

    min_total = 0.0
    max_total = 0.0
    found_priced_item = False
    saw_item = False
    uncertain = False
    source = CostEstimateSource.SEARCH_PROVIDER
    confidence = Confidence.HIGH

    for result in results:
        if result.item_type is not item_type:
            continue
        saw_item = True
        amount = _result_amount_range(result)
        if amount is None:
            uncertain = True
            confidence = _lower_confidence(confidence, Confidence.LOW)
            continue

        found_priced_item = True
        min_total += amount[0] * result.quantity
        max_total += amount[1] * result.quantity
        confidence = _lower_confidence(confidence, result.estimate_confidence)
        if result.status is PriceEstimateStatus.CACHED_ESTIMATE_USED:
            source = CostEstimateSource.CACHED_LOOKUP
        elif result.status is PriceEstimateStatus.RANGE_ESTIMATE_ONLY:
            source = result.estimated_price.source if result.estimated_price else source
        if _is_uncertain(result):
            uncertain = True

    if not saw_item:
        return _RollupTotals(
            min_amount=0.0,
            max_amount=0.0,
            confidence=Confidence.HIGH,
            source=CostEstimateSource.SEARCH_PROVIDER,
            uncertain=False,
        )
    if not found_priced_item:
        return _RollupTotals(
            min_amount=None,
            max_amount=None,
            confidence=Confidence.LOW if uncertain else Confidence.UNKNOWN,
            source=CostEstimateSource.UNAVAILABLE,
            uncertain=uncertain,
        )
    return _RollupTotals(
        min_amount=round(min_total, 2),
        max_amount=round(max_total, 2),
        confidence=Confidence.LOW if uncertain else confidence,
        source=source,
        uncertain=uncertain,
    )


def _result_amount_range(result: PriceLookupResult) -> tuple[float, float] | None:
    """Return the amount range to use for one lookup result."""

    if result.primary_listing is not None:
        amount = result.primary_listing.observed_price
        return (amount, amount)
    if result.estimated_price is None:
        return None
    estimate = result.estimated_price
    if estimate.min_amount is None and estimate.max_amount is None:
        return None
    low = (
        estimate.min_amount if estimate.min_amount is not None else estimate.max_amount
    )
    high = (
        estimate.max_amount if estimate.max_amount is not None else estimate.min_amount
    )
    assert low is not None
    assert high is not None
    return (low, high)


def _is_uncertain(result: PriceLookupResult) -> bool:
    """Return whether result-level flags should make totals approximate."""

    return (
        result.status
        in {
            PriceEstimateStatus.RANGE_ESTIMATE_ONLY,
            PriceEstimateStatus.CACHED_ESTIMATE_USED,
            PriceEstimateStatus.NEEDS_MORE_DETAIL,
            PriceEstimateStatus.PRICE_UNAVAILABLE,
        }
        or result.compatibility_uncertain
        or result.search_match_ambiguous
        or result.exact_match_not_confirmed
        or result.estimate_confidence in {Confidence.UNKNOWN, Confidence.LOW}
    )


def _cost_estimate_from_rollup(rollup: _RollupTotals, label: str) -> CostEstimate:
    """Build a public cost estimate from accumulated totals."""

    notes = None
    if rollup.uncertain:
        notes = f"{label} total is approximate; one or more items are uncertain."
    if rollup.min_amount is None and rollup.max_amount is None:
        notes = f"{label} total unavailable."
    return CostEstimate(
        currency="USD",
        min_amount=rollup.min_amount,
        max_amount=rollup.max_amount,
        confidence=rollup.confidence,
        source=rollup.source,
        notes=notes,
    )


def _merge_rollups(
    parts_total: _RollupTotals,
    tools_total: _RollupTotals,
) -> CostEstimate:
    """Merge part and tool rollups into DIY out-of-pocket cost."""

    min_amount = _sum_optional(parts_total.min_amount, tools_total.min_amount)
    max_amount = _sum_optional(parts_total.max_amount, tools_total.max_amount)
    uncertain = parts_total.uncertain or tools_total.uncertain
    source = (
        CostEstimateSource.UNAVAILABLE
        if min_amount is None and max_amount is None
        else CostEstimateSource.SEARCH_PROVIDER
    )
    return CostEstimate(
        currency="USD",
        min_amount=round(min_amount, 2) if min_amount is not None else None,
        max_amount=round(max_amount, 2) if max_amount is not None else None,
        confidence=Confidence.LOW
        if uncertain
        else _lower_confidence(parts_total.confidence, tools_total.confidence),
        source=source,
        notes="DIY total is approximate; one or more items are uncertain."
        if uncertain
        else None,
    )


def _sum_optional(first: float | None, second: float | None) -> float | None:
    """Sum optional totals while preserving complete unavailability."""

    if first is None and second is None:
        return None
    return (first or 0.0) + (second or 0.0)


def _lower_confidence(first: Confidence, second: Confidence) -> Confidence:
    """Return the lower confidence value."""

    order = {
        Confidence.UNKNOWN: 0,
        Confidence.LOW: 1,
        Confidence.MEDIUM: 2,
        Confidence.HIGH: 3,
    }
    return first if order[first] <= order[second] else second
