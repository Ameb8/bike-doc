"""ADK dependency lifecycle wiring tests."""

from __future__ import annotations

import pytest

import bike_doc_api.api.deps as deps
from bike_doc_api.api.deps import (
    get_adk_session_service,
    get_diagnostic_adk_session_client,
    get_diagnostic_runner,
    get_storage_provider,
)
from bike_doc_api.core.config import Settings
from bike_doc_api.providers.storage import LocalStorageProvider


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
