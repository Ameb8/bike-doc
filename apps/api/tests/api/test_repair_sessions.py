"""Diagnostic repair-session API tests."""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from conftest import (
    ApiTestUser,
    assert_error_response,
    assert_no_private_fields,
    assert_repair_session_shape,
)

OWNED_BIKE_ID = "bike_owned_contract"
NOT_OWNED_BIKE_ID = "bike_other_user"
OWNED_SESSION_ID = "rs_owned_contract"
NOT_OWNED_SESSION_ID = "rs_other_user"

pytestmark = pytest.mark.xfail(
    reason="Stage 5 diagnostic API tests are red until route behavior is implemented.",
)


async def _create_session(
    api_client: httpx.AsyncClient,
    auth_headers: dict[str, str],
    payload: dict[str, Any] | None,
) -> httpx.Response:
    return await api_client.post(
        "/v1/repair-sessions",
        headers=auth_headers,
        json=payload,
    )


def _valid_create_payload(**overrides: Any) -> dict[str, Any]:
    payload = {
        "bike_id": OWNED_BIKE_ID,
        "client_session_id": "client-session-001",
    }
    payload.update(overrides)
    return payload


async def test_create_repair_session_for_owned_bike_returns_created(
    api_client: httpx.AsyncClient,
    auth_headers: dict[str, str],
    test_user: ApiTestUser,
) -> None:
    response = await _create_session(api_client, auth_headers, _valid_create_payload())

    assert response.status_code == 201
    session = response.json()
    assert_repair_session_shape(session, user_id=test_user.id, bike_id=OWNED_BIKE_ID)
    assert session["status"] == "created"
    assert session["safety_state"] == "ok"
    assert session["latest_event_id"] == "0"
    assert session["latest_reports"]["diagnostic_report_id"] is None


async def test_repeating_client_session_id_returns_original_session(
    api_client: httpx.AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    payload = _valid_create_payload(client_session_id="client-session-repeat")

    first = await _create_session(api_client, auth_headers, payload)
    retry = await _create_session(api_client, auth_headers, payload)

    assert first.status_code == 201
    assert retry.status_code == 201
    assert retry.json() == first.json()


async def test_reusing_client_session_id_with_different_payload_returns_409(
    api_client: httpx.AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    client_session_id = "client-session-conflict"
    first = _valid_create_payload(client_session_id=client_session_id)
    conflicting = _valid_create_payload(
        bike_id="bike_owned_second",
        client_session_id=client_session_id,
    )

    await _create_session(api_client, auth_headers, first)
    response = await _create_session(api_client, auth_headers, conflicting)

    assert_error_response(
        response,
        status_code=409,
        error_code="idempotency_conflict",
    )


async def test_create_with_unknown_or_not_owned_bike_returns_404(
    api_client: httpx.AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    for bike_id in ["bike_missing", NOT_OWNED_BIKE_ID]:
        response = await _create_session(
            api_client,
            auth_headers,
            _valid_create_payload(bike_id=bike_id),
        )
        assert_error_response(response, status_code=404, error_code="not_found")


async def test_create_with_missing_or_malformed_bike_id_returns_422(
    api_client: httpx.AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    for payload in [
        {"client_session_id": "client-session-missing-bike"},
        {"bike_id": "", "client_session_id": "client-session-empty-bike"},
    ]:
        response = await _create_session(api_client, auth_headers, payload)
        assert_error_response(
            response,
            status_code=422,
            error_code="validation_error",
        )


async def test_create_with_missing_or_invalid_auth_returns_401(
    api_client: httpx.AsyncClient,
) -> None:
    for headers in [{}, {"Authorization": "Bearer invalid-token"}]:
        response = await _create_session(api_client, headers, _valid_create_payload())
        assert_error_response(response, status_code=401, error_code="unauthorized")


async def test_get_owned_repair_session_returns_public_session(
    api_client: httpx.AsyncClient,
    auth_headers: dict[str, str],
    test_user: ApiTestUser,
) -> None:
    response = await api_client.get(
        f"/v1/repair-sessions/{OWNED_SESSION_ID}",
        headers=auth_headers,
    )

    assert response.status_code == 200
    session = response.json()
    assert_repair_session_shape(session, user_id=test_user.id, bike_id=OWNED_BIKE_ID)
    assert_no_private_fields(session)


async def test_get_unknown_or_not_owned_repair_session_returns_404(
    api_client: httpx.AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    for session_id in ["rs_missing", NOT_OWNED_SESSION_ID]:
        response = await api_client.get(
            f"/v1/repair-sessions/{session_id}",
            headers=auth_headers,
        )
        assert_error_response(response, status_code=404, error_code="not_found")
