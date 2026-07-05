"""Gemini grounded price provider tests."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Any
from unittest.mock import patch

from google.genai import types

from bike_doc_api.providers.price.gemini import (
    GeminiGroundedPriceProvider,
    GeminiPriceLookupResponse,
)
from bike_doc_api.schemas.common import Confidence
from bike_doc_api.schemas.report import (
    CostEstimate,
    CostEstimateSource,
    PriceEstimateStatus,
    PriceLookupRequirement,
)

LOGGER_NAME = "bike_doc_api.providers.price.gemini"


class _Response:
    """Fake genai response."""

    def __init__(
        self,
        *,
        parsed: object | None = None,
        text: str | None = None,
    ) -> None:
        self.parsed = parsed
        self.text = text


class _GenerateContent:
    """Capture fake generate_content calls."""

    def __init__(self, response: _Response) -> None:
        self.response = response
        self.calls: list[dict[str, Any]] = []

    async def __call__(
        self,
        *,
        model: str,
        contents: str,
        config: types.GenerateContentConfig,
    ) -> _Response:
        self.calls.append({"model": model, "contents": contents, "config": config})
        return self.response


class _FakeClient:
    """Minimal fake genai client with the async models surface."""

    def __init__(self) -> None:
        self.aio = _FakeAio()


class _FakeAio:
    """Minimal fake genai aio namespace."""

    def __init__(self) -> None:
        self.models = _FakeModels()


class _FakeModels:
    """Minimal fake genai models namespace."""

    async def generate_content(
        self,
        *,
        model: str,
        contents: str,
        config: types.GenerateContentConfig,
    ) -> _Response:
        return _Response()


def test_factory_keeps_genai_client_alive() -> None:
    client = _FakeClient()

    with patch("bike_doc_api.providers.price.gemini.genai.Client", return_value=client):
        provider = GeminiGroundedPriceProvider.from_google_ai(
            model="gemini-test",
            temperature=0.1,
            max_output_tokens=512,
            timeout_seconds=5,
        )

    assert provider._client is client


async def test_gemini_provider_normalizes_parsed_listing_response() -> None:
    looked_up_at = datetime(2026, 7, 4, 12, 0, tzinfo=UTC)
    generate_content = _GenerateContent(
        _Response(
            parsed=GeminiPriceLookupResponse.model_validate(
                {
                    "status": "priced_listing_found",
                    "estimate_confidence": "high",
                    "primary_listing": {
                        "title": "Shimano HG54 10-Speed Chain",
                        "retailer": "Example Retailer",
                        "observed_price": 27.99,
                        "currency": "USD",
                        "url": "https://example.com/hg54",
                        "match_confidence": "high",
                        "match_rationale": "Title matches model and speed.",
                    },
                },
            ),
        ),
    )

    result = await GeminiGroundedPriceProvider(
        model="gemini-test",
        temperature=0.1,
        max_output_tokens=512,
        timeout_seconds=5,
        generate_content=generate_content,
        clock=lambda: looked_up_at,
    ).lookup_requirement(
        PriceLookupRequirement.model_validate(
            {
                "item_type": "part",
                "display_name": "Shimano HG54 10-speed chain",
                "quantity": 1,
                "exact_match_required": True,
                "search_query": "Shimano HG54 10-speed chain",
            },
        ),
    )

    assert result.status == PriceEstimateStatus.PRICED_LISTING_FOUND
    assert result.requirement_name == "Shimano HG54 10-speed chain"
    assert result.primary_listing is not None
    assert result.primary_listing.observed_price == 27.99
    assert result.primary_listing.observed_at == looked_up_at
    assert result.exact_match_not_confirmed is False
    assert generate_content.calls[0]["model"] == "gemini-test"
    assert "Shimano HG54 10-speed chain" in generate_content.calls[0]["contents"]
    assert generate_content.calls[0]["config"].tools
    assert generate_content.calls[0]["config"].response_mime_type is None
    assert generate_content.calls[0]["config"].response_schema is None


async def test_gemini_provider_parses_text_range_response() -> None:
    looked_up_at = datetime(2026, 7, 4, 12, 0, tzinfo=UTC)
    generate_content = _GenerateContent(
        _Response(
            text=(
                "```json\n"
                + json.dumps(
                    {
                        "status": "range_estimate_only",
                        "estimate_confidence": "low",
                        "estimated_price": {
                            "currency": "USD",
                            "min_amount": 12,
                            "max_amount": 24,
                            "confidence": "low",
                            "source": "search_provider",
                            "notes": "Current listings vary by compound and fit.",
                        },
                        "compatibility_uncertain": True,
                        "search_match_ambiguous": True,
                        "exact_match_not_confirmed": True,
                    },
                )
                + "\n```"
            ),
        ),
    )

    result = await GeminiGroundedPriceProvider(
        model="gemini-test",
        temperature=0.1,
        max_output_tokens=512,
        timeout_seconds=5,
        generate_content=generate_content,
        clock=lambda: looked_up_at,
    ).lookup_requirement(
        PriceLookupRequirement.model_validate(
            {
                "item_type": "part",
                "display_name": "Brake pads",
                "quantity": 2,
                "exact_match_required": True,
                "search_query": "bike disc brake pads",
            },
        ),
    )

    assert result.status == PriceEstimateStatus.RANGE_ESTIMATE_ONLY
    assert result.estimated_price == CostEstimate(
        currency="USD",
        min_amount=12,
        max_amount=24,
        confidence=Confidence.LOW,
        source=CostEstimateSource.SEARCH_PROVIDER,
        notes="Current listings vary by compound and fit.",
    )
    assert result.compatibility_uncertain is True
    assert result.search_match_ambiguous is True
    assert result.exact_match_not_confirmed is True


async def test_gemini_provider_logs_search_lifecycle(
    caplog: Any,
) -> None:
    looked_up_at = datetime(2026, 7, 4, 12, 0, tzinfo=UTC)
    generate_content = _GenerateContent(
        _Response(
            parsed=GeminiPriceLookupResponse.model_validate(
                {
                    "status": "priced_listing_found",
                    "estimate_confidence": "medium",
                    "primary_listing": {
                        "title": "Chain Wear Indicator Tool",
                        "retailer": "Example Retailer",
                        "observed_price": 17.95,
                        "currency": "USD",
                        "url": "https://example.com/chain-checker",
                        "match_confidence": "medium",
                        "match_rationale": "Representative generic listing.",
                    },
                    "generic_substitute_used": True,
                },
            ),
        ),
    )
    caplog.set_level(logging.INFO, logger=LOGGER_NAME)

    await GeminiGroundedPriceProvider(
        model="gemini-test",
        temperature=0.1,
        max_output_tokens=512,
        timeout_seconds=5,
        generate_content=generate_content,
        clock=lambda: looked_up_at,
    ).lookup_requirement(
        PriceLookupRequirement.model_validate(
            {
                "item_type": "tool",
                "display_name": "Chain checker",
                "quantity": 1,
                "generic_equivalent_acceptable": True,
                "search_query": "bike chain checker tool",
            },
        ),
    )

    events = [record.msg for record in caplog.records]
    assert "price_lookup_search_started" in events
    assert "price_lookup_search_completed" in events

    completed = next(
        record
        for record in caplog.records
        if record.msg == "price_lookup_search_completed"
    )
    assert completed.requirement_name == "Chain checker"
    assert completed.status == "priced_listing_found"
    assert completed.primary_listing_price == 17.95
