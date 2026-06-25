"""ADK dependency lifecycle wiring tests."""

from __future__ import annotations

from bike_doc_api.api.deps import (
    get_adk_session_service,
    get_diagnostic_adk_session_client,
    get_diagnostic_runner,
)
from bike_doc_api.core.config import Settings


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
