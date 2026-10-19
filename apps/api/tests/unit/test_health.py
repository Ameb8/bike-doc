"""Dependency health is independent, bounded, and contains no private failure text."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from nats.errors import AuthorizationError
from nats.errors import Error as NatsError

from bike_doc_api.core.config import Settings
from bike_doc_api.main import create_app
from bike_doc_api.operations.health import dependencies


def test_worker_role_does_not_require_http_auth_configuration() -> None:
    settings = Settings(environment="production", process_role="profile")
    with pytest.raises(ValueError, match="API process role"):
        create_app(settings)
    with pytest.raises(ValueError, match="firebase"):
        Settings(environment="production")


@pytest.mark.parametrize(
    "database_ok,broker_ok,consumer_ok",
    [
        (True, True, True),
        (False, True, True),
        (True, False, False),
        (True, True, False),
    ],
)
async def test_independent_health(
    database_ok: bool, broker_ok: bool, consumer_ok: bool
) -> None:
    sessions = MagicMock()
    session = sessions.return_value.__aenter__.return_value
    session.execute = AsyncMock(
        side_effect=None if database_ok else RuntimeError("private")
    )
    client = MagicMock()
    client.is_connected = broker_ok
    client.last_error = None
    client.flush = AsyncMock()
    js = MagicMock()
    js.consumer_info = AsyncMock(
        side_effect=None if consumer_ok else RuntimeError("private")
    )
    worker = MagicMock()
    worker.healthy.return_value = True
    with patch("bike_doc_api.operations.health.jetstream", return_value=js):
        checks = await dependencies(
            sessions,
            Settings(),
            client=client,
            workload="profile_inference",
            worker=worker,
        )
    assert checks == {
        "process": True,
        "database": database_ok,
        "broker": broker_ok,
        "consumer": consumer_ok,
        "worker": True,
    }
    assert "private" not in str(checks)
    if broker_ok:
        js.consumer_info.assert_awaited_once_with(
            "BIKEDOC_WORK_V1", "bikedoc_profile_v1"
        )


@pytest.mark.parametrize(
    "failure",
    [
        NatsError("nats: permissions violation for private subject"),
        AuthorizationError(),
    ],
)
async def test_broker_permission_error_is_consumer_failure(failure: Exception) -> None:
    sessions = MagicMock()
    sessions.return_value.__aenter__.return_value.execute = AsyncMock()
    client = MagicMock()
    client.is_connected = True
    client.last_error = failure
    client.flush = AsyncMock()
    checks = await dependencies(sessions, Settings(), client=client)
    assert checks["database"] and checks["broker"] and not checks["consumer"]
    assert "private" not in str(checks)


async def test_denied_pull_is_not_reported_as_successful_empty_fetch() -> None:
    from nats.errors import TimeoutError as NatsTimeoutError

    from bike_doc_api.workers.adapters import NatsPullTransport

    subscription = MagicMock()
    subscription.fetch = AsyncMock(side_effect=NatsTimeoutError)
    client = MagicMock()
    transport = NatsPullTransport(subscription, client=client)
    client.is_connected = True
    client.last_error = None
    assert await transport.fetch(1, 0.1) == []
    client.last_error = NatsError("nats: permissions violation for private subject")
    with pytest.raises(ConnectionError, match=r"^NATS pull unavailable$"):
        await transport.fetch(1, 0.1)


@pytest.mark.parametrize(
    "historical_error",
    [ConnectionRefusedError(), TimeoutError(), NatsError("nats: timeout")],
)
async def test_reconnected_worker_recovers_health_and_empty_fetch(
    historical_error: Exception,
) -> None:
    from nats.errors import TimeoutError as NatsTimeoutError

    from bike_doc_api.workers.adapters import NatsPullTransport

    sessions = MagicMock()
    sessions.return_value.__aenter__.return_value.execute = AsyncMock()
    client = MagicMock()
    client.is_connected = True
    client.last_error = historical_error
    client.flush = AsyncMock()
    js = MagicMock()
    js.consumer_info = AsyncMock()
    with patch("bike_doc_api.operations.health.jetstream", return_value=js):
        checks = await dependencies(
            sessions, Settings(), client=client, workload="profile_inference"
        )
    assert all(checks.values())
    subscription = MagicMock()
    subscription.fetch = AsyncMock(side_effect=NatsTimeoutError)
    transport = NatsPullTransport(subscription, client=client)
    assert await transport.fetch(1, 0.1) == []
    client.last_error = None
    client.is_connected = False
    with pytest.raises(ConnectionError, match=r"^NATS pull unavailable$"):
        await transport.fetch(1, 0.1)
