"""Gemini grounded-search price provider."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any, Protocol

from google import genai
from google.genai import types
from pydantic import BaseModel, ConfigDict, Field

from bike_doc_api.schemas.common import Confidence
from bike_doc_api.schemas.report import (
    CostEstimate,
    CostItemType,
    PriceEstimateStatus,
    PriceListing,
    PriceLookupRequirement,
    PriceLookupResult,
)

_SYSTEM_INSTRUCTION = """
You are Bike Doc's live listing lookup normalizer.
Use Google Search grounding for one bike repair tool or part requirement.
Return only a JSON object matching the requested contract. Do not wrap the JSON
in Markdown or explanatory text.

Rules:
- Search for current retailer listings or current observed market prices.
- Do not claim cheapest price, in-stock status, checkout totals, or guaranteed fit.
- Do not infer compatibility solely from retailer text.
- If exact_match_required is true, mark exact_match_not_confirmed unless the
  listing title and visible details support the requested brand/model/spec.
- If generic_equivalent_acceptable is true, a representative generic listing is
  acceptable, but set generic_substitute_used when applicable.
- If the requirement is too vague or variants materially differ, prefer
  needs_more_detail or range_estimate_only over false precision.
- Use USD unless the observed source clearly uses another currency.
""".strip()

logger = logging.getLogger(__name__)


class _GeminiGenerateContent(Protocol):
    """Callable subset used by the provider and fakes."""

    def __call__(
        self,
        *,
        model: str,
        contents: str,
        config: types.GenerateContentConfig,
    ) -> Awaitable[Any]:
        """Generate content with the Gemini API."""


class GeminiPriceListing(BaseModel):
    """LLM-normalized listing evidence."""

    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1)
    retailer: str = Field(min_length=1)
    observed_price: float = Field(ge=0)
    currency: str = "USD"
    url: str = Field(min_length=1)
    observed_at: datetime | None = None
    match_confidence: Confidence
    match_rationale: str = Field(min_length=1)


class GeminiPriceLookupResponse(BaseModel):
    """Strict JSON shape requested from Gemini before final normalization."""

    model_config = ConfigDict(extra="forbid")

    status: PriceEstimateStatus
    estimate_confidence: Confidence
    estimated_price: CostEstimate | None = None
    primary_listing: GeminiPriceListing | None = None
    alternate_listings: list[GeminiPriceListing] = Field(default_factory=list)
    compatibility_uncertain: bool = False
    search_match_ambiguous: bool = False
    generic_substitute_used: bool = False
    exact_match_not_confirmed: bool = False


class GeminiGroundedPriceProvider:
    """Lookup item prices with Gemini grounded Google Search."""

    def __init__(
        self,
        *,
        model: str,
        temperature: float,
        max_output_tokens: int,
        timeout_seconds: float,
        generate_content: _GeminiGenerateContent,
        client: genai.Client | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._model = model
        self._temperature = temperature
        self._max_output_tokens = max_output_tokens
        self._timeout_seconds = timeout_seconds
        self._generate_content = generate_content
        self._client = client
        self._clock = clock or _utc_now

    @classmethod
    def from_google_ai(
        cls,
        *,
        model: str,
        temperature: float,
        max_output_tokens: int,
        timeout_seconds: float,
    ) -> GeminiGroundedPriceProvider:
        """Build a Google AI Studio backed provider."""

        client = genai.Client()
        return cls(
            model=model,
            temperature=temperature,
            max_output_tokens=max_output_tokens,
            timeout_seconds=timeout_seconds,
            generate_content=client.aio.models.generate_content,
            client=client,
        )

    @classmethod
    def from_vertex_ai(
        cls,
        *,
        model: str,
        temperature: float,
        max_output_tokens: int,
        timeout_seconds: float,
    ) -> GeminiGroundedPriceProvider:
        """Build a Vertex AI backed provider from standard genai environment."""

        client = genai.Client(vertexai=True)
        return cls(
            model=model,
            temperature=temperature,
            max_output_tokens=max_output_tokens,
            timeout_seconds=timeout_seconds,
            generate_content=client.aio.models.generate_content,
            client=client,
        )

    async def lookup_requirement(
        self,
        requirement: PriceLookupRequirement,
    ) -> PriceLookupResult:
        """Return normalized price evidence for one planning requirement."""

        looked_up_at = self._clock()
        logger.info(
            "price_lookup_search_started",
            extra={
                "item_type": requirement.item_type.value,
                "requirement_name": requirement.display_name,
                "quantity": requirement.quantity,
                "search_query": requirement.search_query,
                "exact_match_required": requirement.exact_match_required,
                "generic_equivalent_acceptable": (
                    requirement.generic_equivalent_acceptable
                ),
                "provider_model": self._model,
            },
        )
        response = await asyncio.wait_for(
            self._generate_content(
                model=self._model,
                contents=_build_lookup_prompt(requirement, looked_up_at),
                config=_generate_config(
                    temperature=self._temperature,
                    max_output_tokens=self._max_output_tokens,
                ),
            ),
            timeout=self._timeout_seconds,
        )
        parsed = _parse_response(response)
        result = _result_from_response(
            parsed,
            requirement=requirement,
            looked_up_at=looked_up_at,
        )
        logger.info(
            "price_lookup_search_completed",
            extra=_result_log_fields(result),
        )
        return result


def _generate_config(
    *,
    temperature: float,
    max_output_tokens: int,
) -> types.GenerateContentConfig:
    """Build the grounded JSON generation config."""

    return types.GenerateContentConfig(
        system_instruction=_SYSTEM_INSTRUCTION,
        temperature=temperature,
        max_output_tokens=max_output_tokens,
        tools=[types.Tool(google_search=types.GoogleSearch())],
    )


def _build_lookup_prompt(
    requirement: PriceLookupRequirement,
    looked_up_at: datetime,
) -> str:
    """Build a narrow one-item lookup prompt."""

    return json.dumps(
        {
            "task": "Look up current listing or price evidence for this one item.",
            "looked_up_at": looked_up_at.isoformat(),
            "requirement": requirement.model_dump(mode="json"),
            "output_contract": {
                "statuses": [status.value for status in PriceEstimateStatus],
                "alternate_listings_max": 2,
                "required_fields": ["status", "estimate_confidence"],
                "listing_fields": [
                    "title",
                    "retailer",
                    "observed_price",
                    "currency",
                    "url",
                    "observed_at",
                    "match_confidence",
                    "match_rationale",
                ],
                "confidence_values": [confidence.value for confidence in Confidence],
                "fallback_policy": (
                    "Use range_estimate_only, needs_more_detail, or "
                    "price_unavailable when listing evidence is weak."
                ),
            },
        },
        sort_keys=True,
    )


def _parse_response(response: Any) -> GeminiPriceLookupResponse:
    """Parse Gemini JSON output from typed or text response surfaces."""

    parsed = getattr(response, "parsed", None)
    if isinstance(parsed, GeminiPriceLookupResponse):
        return parsed
    if isinstance(parsed, dict):
        return GeminiPriceLookupResponse.model_validate(parsed)

    text = getattr(response, "text", None)
    if not isinstance(text, str) or not text.strip():
        raise ValueError("Gemini price lookup returned no JSON payload.")
    json_text = _extract_json_object(text)
    try:
        data = json.loads(json_text)
    except json.JSONDecodeError as exc:
        raise ValueError("Gemini price lookup returned invalid JSON.") from exc
    return GeminiPriceLookupResponse.model_validate(data)


def _extract_json_object(text: str) -> str:
    """Extract the first JSON object from a grounded text response."""

    stripped = text.strip()
    if stripped.startswith("{"):
        return stripped

    decoder = json.JSONDecoder()
    for index, character in enumerate(stripped):
        if character != "{":
            continue
        try:
            _, end = decoder.raw_decode(stripped[index:])
        except json.JSONDecodeError:
            continue
        return stripped[index : index + end]
    raise ValueError("Gemini price lookup returned no JSON payload.")


def _result_from_response(
    response: GeminiPriceLookupResponse,
    *,
    requirement: PriceLookupRequirement,
    looked_up_at: datetime,
) -> PriceLookupResult:
    """Convert provider JSON into the stable Bike Doc result model."""

    alternate_listings = [
        _listing_from_response(listing, observed_at=looked_up_at)
        for listing in response.alternate_listings[:2]
    ]
    primary_listing = (
        _listing_from_response(response.primary_listing, observed_at=looked_up_at)
        if response.primary_listing is not None
        else None
    )
    status = _coerce_status(response, primary_listing=primary_listing)
    estimated_price = _coerce_estimated_price(response, primary_listing=primary_listing)
    return PriceLookupResult(
        item_type=requirement.item_type,
        requirement_name=requirement.display_name,
        quantity=requirement.quantity,
        status=status,
        estimate_confidence=response.estimate_confidence,
        looked_up_at=looked_up_at,
        estimated_price=estimated_price,
        primary_listing=primary_listing,
        alternate_listings=alternate_listings,
        compatibility_uncertain=(
            response.compatibility_uncertain
            or (
                requirement.item_type is CostItemType.PART
                and response.estimate_confidence in {Confidence.UNKNOWN, Confidence.LOW}
            )
        ),
        search_match_ambiguous=response.search_match_ambiguous,
        generic_substitute_used=response.generic_substitute_used,
        exact_match_not_confirmed=(
            response.exact_match_not_confirmed
            or (
                requirement.exact_match_required
                and response.estimate_confidence in {Confidence.UNKNOWN, Confidence.LOW}
            )
        ),
    )


def _listing_from_response(
    listing: GeminiPriceListing,
    *,
    observed_at: datetime,
) -> PriceListing:
    """Normalize one LLM listing into public listing evidence."""

    return PriceListing(
        title=listing.title,
        retailer=listing.retailer,
        observed_price=listing.observed_price,
        currency=listing.currency,
        url=listing.url,
        observed_at=listing.observed_at or observed_at,
        match_confidence=listing.match_confidence,
        match_rationale=listing.match_rationale,
    )


def _coerce_status(
    response: GeminiPriceLookupResponse,
    *,
    primary_listing: PriceListing | None,
) -> PriceEstimateStatus:
    """Keep status compatible with available evidence."""

    if (
        response.status is PriceEstimateStatus.PRICED_LISTING_FOUND
        and primary_listing is None
    ):
        if response.estimated_price is not None:
            return PriceEstimateStatus.RANGE_ESTIMATE_ONLY
        return PriceEstimateStatus.PRICE_UNAVAILABLE
    if (
        response.status is PriceEstimateStatus.RANGE_ESTIMATE_ONLY
        and response.estimated_price is None
    ):
        return PriceEstimateStatus.PRICE_UNAVAILABLE
    return response.status


def _coerce_estimated_price(
    response: GeminiPriceLookupResponse,
    *,
    primary_listing: PriceListing | None,
) -> CostEstimate | None:
    """Keep estimate payload compatible with the normalized status."""

    if response.status is PriceEstimateStatus.RANGE_ESTIMATE_ONLY:
        return response.estimated_price
    if primary_listing is None and response.estimated_price is not None:
        return response.estimated_price
    return response.estimated_price


def _utc_now() -> datetime:
    """Return the current UTC timestamp."""

    return datetime.now(UTC)


def _result_log_fields(result: PriceLookupResult) -> dict[str, object]:
    """Return a compact result summary for INFO logging."""

    primary_listing = result.primary_listing
    estimated_price = result.estimated_price
    return {
        "item_type": result.item_type.value,
        "requirement_name": result.requirement_name,
        "status": result.status.value,
        "estimate_confidence": result.estimate_confidence.value,
        "search_match_ambiguous": result.search_match_ambiguous,
        "compatibility_uncertain": result.compatibility_uncertain,
        "generic_substitute_used": result.generic_substitute_used,
        "exact_match_not_confirmed": result.exact_match_not_confirmed,
        "primary_listing_title": primary_listing.title if primary_listing else None,
        "primary_listing_retailer": (
            primary_listing.retailer if primary_listing else None
        ),
        "primary_listing_price": (
            primary_listing.observed_price if primary_listing else None
        ),
        "alternate_listing_count": len(result.alternate_listings),
        "estimated_min_amount": estimated_price.min_amount if estimated_price else None,
        "estimated_max_amount": estimated_price.max_amount if estimated_price else None,
    }
