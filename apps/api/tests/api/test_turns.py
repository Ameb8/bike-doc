"""Diagnostic turn API tests."""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from conftest import ApiTestUser, assert_error_response, assert_repair_session_shape

OWNED_SESSION_ID = "rs_owned_contract"
NOT_OWNED_SESSION_ID = "rs_other_user"
OWNED_BIKE_ID = "bike_owned_contract"
OWNED_ARTIFACT_ID = "art_owned_contract"

pytestmark = pytest.mark.xfail(
    reason="Stage 5 diagnostic API tests are red until route behavior is implemented.",
)


def _valid_turn_payload(**overrides: Any) -> dict[str, Any]:
    payload = {
        "schema_version": "ai_turn.v1",
        "client_turn_id": "client-turn-001",
        "message": {
            "text": "The chain skips under load.",
            "artifact_ids": [],
        },
    }
    payload.update(overrides)
    return payload


async def _post_turn(
    api_client: httpx.AsyncClient,
    auth_headers: dict[str, str],
    session_id: str,
    payload: dict[str, Any],
) -> httpx.Response:
    return await api_client.post(
        f"/v1/repair-sessions/{session_id}/turns",
        headers=auth_headers,
        json=payload,
    )


async def test_submit_valid_diagnostic_turn_returns_accepted(
    api_client: httpx.AsyncClient,
    auth_headers: dict[str, str],
    test_user: ApiTestUser,
) -> None:
    response = await _post_turn(
        api_client,
        auth_headers,
        OWNED_SESSION_ID,
        _valid_turn_payload(),
    )

    assert response.status_code == 202
    body = response.json()
    assert body["turn_id"].startswith("turn_") or body["turn_id"]
    assert body["repair_session_id"] == OWNED_SESSION_ID
    assert body["start_event_id"]
    assert body["event_stream_url"] == (
        f"/v1/repair-sessions/{OWNED_SESSION_ID}/events?after={body['start_event_id']}"
    )
    assert_repair_session_shape(
        body["session"],
        user_id=test_user.id,
        bike_id=OWNED_BIKE_ID,
    )


async def test_repeating_client_turn_id_returns_original_acceptance(
    api_client: httpx.AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    payload = _valid_turn_payload(client_turn_id="client-turn-repeat")

    first = await _post_turn(api_client, auth_headers, OWNED_SESSION_ID, payload)
    retry = await _post_turn(api_client, auth_headers, OWNED_SESSION_ID, payload)

    assert first.status_code == 202
    assert retry.status_code == 202
    assert retry.json() == first.json()


async def test_reusing_client_turn_id_with_different_payload_returns_409(
    api_client: httpx.AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    client_turn_id = "client-turn-conflict"
    first = _valid_turn_payload(client_turn_id=client_turn_id)
    conflicting = _valid_turn_payload(
        client_turn_id=client_turn_id,
        message={"text": "Now it also clicks.", "artifact_ids": []},
    )

    await _post_turn(api_client, auth_headers, OWNED_SESSION_ID, first)
    response = await _post_turn(
        api_client,
        auth_headers,
        OWNED_SESSION_ID,
        conflicting,
    )

    assert_error_response(
        response,
        status_code=409,
        error_code="idempotency_conflict",
    )


async def test_post_turn_to_unknown_or_not_owned_session_returns_404(
    api_client: httpx.AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    for session_id in ["rs_missing", NOT_OWNED_SESSION_ID]:
        response = await _post_turn(
            api_client,
            auth_headers,
            session_id,
            _valid_turn_payload(),
        )
        assert_error_response(response, status_code=404, error_code="not_found")


async def test_referencing_invalid_artifact_returns_404(
    api_client: httpx.AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    for artifact_id in ["art_missing", "art_other_user", "art_wrong_session"]:
        response = await _post_turn(
            api_client,
            auth_headers,
            OWNED_SESSION_ID,
            _valid_turn_payload(
                message={"text": "See attached.", "artifact_ids": [artifact_id]},
            ),
        )
        assert_error_response(response, status_code=404, error_code="not_found")


async def test_referencing_unknown_input_request_returns_404(
    api_client: httpx.AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    response = await _post_turn(
        api_client,
        auth_headers,
        OWNED_SESSION_ID,
        _valid_turn_payload(responds_to_input_request_id="req_missing"),
    )

    assert_error_response(response, status_code=404, error_code="not_found")


async def test_post_turn_when_session_not_accepting_turns_returns_409(
    api_client: httpx.AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    response = await _post_turn(
        api_client,
        auth_headers,
        "rs_completed_contract",
        _valid_turn_payload(),
    )

    assert_error_response(
        response,
        status_code=409,
        error_code="session_state_conflict",
    )


async def test_invalid_turn_payload_returns_422(
    api_client: httpx.AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    invalid_payloads = [
        _valid_turn_payload(schema_version="ai_turn.v2"),
        {
            "schema_version": "ai_turn.v1",
            "message": {"text": "Missing client turn ID.", "artifact_ids": []},
        },
        _valid_turn_payload(message={"text": "", "artifact_ids": []}),
        _valid_turn_payload(message={"text": None, "artifact_ids": []}),
    ]

    for payload in invalid_payloads:
        response = await _post_turn(
            api_client,
            auth_headers,
            OWNED_SESSION_ID,
            payload,
        )
        assert_error_response(
            response,
            status_code=422,
            error_code="validation_error",
        )
