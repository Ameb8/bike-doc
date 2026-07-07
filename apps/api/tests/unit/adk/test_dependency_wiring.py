"""ADK dependency lifecycle wiring tests."""

from __future__ import annotations

import pytest

import bike_doc_api.api.deps as deps
from bike_doc_api.api.deps import (
    get_adk_session_service,
    get_cost_estimate_service,
    get_diagnostic_adk_session_client,
    get_diagnostic_runner,
    get_price_lookup_provider,
    get_storage_provider,
)
from bike_doc_api.core.config import Settings
from bike_doc_api.providers.price import (
    UnavailablePriceProvider,
)
from bike_doc_api.providers.storage import LocalStorageProvider
from bike_doc_api.services.cost_estimates import CostEstimateService


def test_adk_session_service_provider_is_process_lifetime() -> None:
    get_adk_session_service.cache_clear()

    first = get_adk_session_service()
    second = get_adk_session_service()

    assert first is second


def test_session_client_and_runner_receive_same_adk_session_service() -> None:
    get_adk_session_service.cache_clear()
    service = get_adk_session_service()
    settings = Settings(environment="test")

    client = get_diagnostic_adk_session_client(service, settings)
    runner = get_diagnostic_runner(service, settings)

    assert client.session_service is service
    assert runner.session_service is service


def test_storage_provider_dependency_returns_local_provider() -> None:
    provider = get_storage_provider(
        Settings(
            environment="test",
            artifact_storage_provider="local",
        )
    )

    assert isinstance(provider, LocalStorageProvider)


def test_storage_provider_dependency_returns_gcs_provider() -> None:
    captured: dict[str, str] = {}

    class _FakeGCSStorageProvider:
        def __init__(self, *, bucket_name: str) -> None:
            captured["bucket_name"] = bucket_name

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(deps, "GCSStorageProvider", _FakeGCSStorageProvider)
    try:
        provider = get_storage_provider(
            Settings(
                environment="test",
                artifact_storage_provider="gcs",
                artifact_gcs_bucket="bike-doc-artifacts",
            )
        )
    finally:
        monkeypatch.undo()

    assert isinstance(provider, _FakeGCSStorageProvider)
    assert captured == {"bucket_name": "bike-doc-artifacts"}


def test_price_lookup_provider_dependency_defaults_to_unavailable() -> None:
    provider = get_price_lookup_provider(Settings(environment="test"))

    assert isinstance(provider, UnavailablePriceProvider)


def test_price_lookup_provider_dependency_returns_gemini_provider() -> None:
    captured: dict[str, object] = {}

    class _FakeGeminiGroundedPriceProvider:
        @classmethod
        def from_google_ai(
            cls,
            *,
            model: str,
            temperature: float,
            max_output_tokens: int,
            timeout_seconds: float,
        ) -> _FakeGeminiGroundedPriceProvider:
            captured.update(
                {
                    "model": model,
                    "temperature": temperature,
                    "max_output_tokens": max_output_tokens,
                    "timeout_seconds": timeout_seconds,
                },
            )
            return cls()

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(
        deps,
        "GeminiGroundedPriceProvider",
        _FakeGeminiGroundedPriceProvider,
    )
    try:
        provider = get_price_lookup_provider(
            Settings(
                environment="test",
                price_lookup_provider="gemini_grounded",
                price_lookup_model="gemini-price-test",
                price_lookup_temperature=0.3,
                price_lookup_max_output_tokens=777,
                price_lookup_timeout_seconds=9.5,
            ),
        )
    finally:
        monkeypatch.undo()

    assert isinstance(provider, _FakeGeminiGroundedPriceProvider)
    assert captured == {
        "model": "gemini-price-test",
        "temperature": 0.3,
        "max_output_tokens": 777,
        "timeout_seconds": 9.5,
    }


def test_price_lookup_provider_dependency_can_use_vertex() -> None:
    captured: dict[str, object] = {}

    class _FakeGeminiGroundedPriceProvider:
        @classmethod
        def from_vertex_ai(
            cls,
            *,
            model: str,
            temperature: float,
            max_output_tokens: int,
            timeout_seconds: float,
        ) -> _FakeGeminiGroundedPriceProvider:
            captured.update(
                {
                    "model": model,
                    "temperature": temperature,
                    "max_output_tokens": max_output_tokens,
                    "timeout_seconds": timeout_seconds,
                },
            )
            return cls()

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(
        deps,
        "GeminiGroundedPriceProvider",
        _FakeGeminiGroundedPriceProvider,
    )
    try:
        provider = get_price_lookup_provider(
            Settings(
                environment="test",
                price_lookup_provider="gemini_grounded",
                price_lookup_llm_provider="vertex_ai",
                price_lookup_model="gemini-price-test",
            ),
        )
    finally:
        monkeypatch.undo()

    assert isinstance(provider, _FakeGeminiGroundedPriceProvider)
    assert captured["model"] == "gemini-price-test"


def test_cost_estimate_service_dependency_wraps_price_provider() -> None:
    provider = UnavailablePriceProvider()

    service = get_cost_estimate_service(provider)

    assert isinstance(service, CostEstimateService)
