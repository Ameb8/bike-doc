"""Stub price provider boundary."""

from __future__ import annotations

from datetime import UTC, datetime

from bike_doc_api.schemas.common import Confidence
from bike_doc_api.schemas.report import (
    CostEstimateSource,
    PriceEstimateStatus,
    PriceLookupRequirement,
    PriceLookupResult,
)


class UnavailablePriceProvider:
    """Local/test provider that reports transparent price unavailability."""

    async def lookup_requirement(
        self,
        requirement: PriceLookupRequirement,
    ) -> PriceLookupResult:
        """Return a non-fabricated unavailable result for one requirement."""

        return PriceLookupResult(
            item_type=requirement.item_type,
            requirement_name=requirement.display_name,
            quantity=requirement.quantity,
            status=PriceEstimateStatus.PRICE_UNAVAILABLE,
            estimate_confidence=Confidence.LOW,
            looked_up_at=datetime.now(UTC),
            estimated_price=None,
            compatibility_uncertain=requirement.item_type == "part",
            search_match_ambiguous=False,
            generic_substitute_used=False,
            exact_match_not_confirmed=requirement.exact_match_required,
        )


def unavailable_cost_source() -> CostEstimateSource:
    """Return the source value used when no provider evidence is available."""

    return CostEstimateSource.UNAVAILABLE
