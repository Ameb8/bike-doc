"""Price provider interface boundary."""

from __future__ import annotations

from typing import Protocol

from bike_doc_api.schemas.report import PriceLookupRequirement, PriceLookupResult


class PriceLookupProvider(Protocol):
    """Provider-neutral item lookup boundary for planning cost estimates."""

    async def lookup_requirement(
        self,
        requirement: PriceLookupRequirement,
    ) -> PriceLookupResult:
        """Return normalized price evidence for one requirement."""
