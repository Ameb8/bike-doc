"""Diagnostic repair-session event stream API tests."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from conftest import assert_error_response

OWNED_SESSION_ID = "rs_owned_contract"
NOT_OWNED_SESSION_ID = "rs_other_user"

pytestmark = pytest.mark.xfail(
    reason="Stage 5 diagnostic API tests are red until route behavior is implemented.",
)


def _parse_sse_frames(text: str) -> list[dict[str, Any]]:
    frames: list[dict[str, Any]] = []
    for raw_frame in text.strip().split("\n\n"):
        if not raw_frame:
            continue
        frame: dict[str, Any] = {}
        for line in raw_frame.splitlines():
            if line.startswith(":"):
                continue
            field, separator, value = line.partition(":")
            assert separator
            frame[field] = value.lstrip()
        assert frame["id"]
        assert frame["event"]
        data = json.loads(frame["data"])
        assert data["id"] == frame["id"]
        assert data["type"] == frame["event"]
        assert data["session_id"]
        assert isinstance(data["sequence"], int)
        assert data["created_at"]
        assert "data" in data
        frame["data"] = data
        frames.append(frame)
    return frames


async def _get_events(
    api_client: httpx.AsyncClient,
    auth_headers: dict[str, str],
    session_id: str = OWNED_SESSION_ID,
    params: dict[str, Any] | None = None,
    extra_headers: dict[str, str] | None = None,
) -> httpx.Response:
    headers = {**auth_headers, "Accept": "text/event-stream"}
    if extra_headers:
        headers.update(extra_headers)
    return await api_client.get(
        f"/v1/repair-sessions/{session_id}/events",
        headers=headers,
        params=params,
    )


async def test_events_after_zero_returns_event_stream_content_type(
    api_client: httpx.AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    response = await _get_events(
        api_client,
        auth_headers,
        params={"after": "0", "timeout_seconds": 5},
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    _parse_sse_frames(response.text)


async def test_after_zero_replays_all_retained_events_in_sequence_order(
    api_client: httpx.AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    response = await _get_events(
        api_client,
        auth_headers,
        params={"after": "0", "timeout_seconds": 5},
    )

    assert response.status_code == 200
    frames = _parse_sse_frames(response.text)
    sequences = [frame["data"]["sequence"] for frame in frames]
    assert sequences
    assert sequences == sorted(sequences)
    assert sequences[0] == 1


async def test_known_cursor_replays_only_newer_events(
    api_client: httpx.AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    response = await _get_events(
        api_client,
        auth_headers,
        params={"after": "1", "timeout_seconds": 5},
    )

    assert response.status_code == 200
    frames = _parse_sse_frames(response.text)
    assert frames
    assert all(frame["data"]["sequence"] > 1 for frame in frames)


async def test_omitted_after_starts_after_current_latest_event(
    api_client: httpx.AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    response = await _get_events(
        api_client,
        auth_headers,
        params={"timeout_seconds": 5},
    )

    assert response.status_code == 200
    frames = _parse_sse_frames(response.text)
    assert all(frame["event"] == "heartbeat" for frame in frames)


async def test_after_takes_precedence_over_last_event_id(
    api_client: httpx.AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    response = await _get_events(
        api_client,
        auth_headers,
        params={"after": "0", "timeout_seconds": 5},
        extra_headers={"Last-Event-ID": "2"},
    )

    assert response.status_code == 200
    frames = _parse_sse_frames(response.text)
    assert frames
    assert frames[0]["id"] == "1"


async def test_invalid_event_cursors_return_422(
    api_client: httpx.AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    for after in ["-1", "abc", "evt_123", "999999"]:
        response = await _get_events(
            api_client,
            auth_headers,
            params={"after": after, "timeout_seconds": 5},
        )
        assert_error_response(
            response,
            status_code=422,
            error_code="validation_error",
        )


async def test_invalid_timeout_seconds_returns_422(
    api_client: httpx.AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    for timeout_seconds in [4, 121]:
        response = await _get_events(
            api_client,
            auth_headers,
            params={"after": "0", "timeout_seconds": timeout_seconds},
        )
        assert_error_response(
            response,
            status_code=422,
            error_code="validation_error",
        )


async def test_events_for_unknown_or_not_owned_session_returns_404(
    api_client: httpx.AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    for session_id in ["rs_missing", NOT_OWNED_SESSION_ID]:
        response = await _get_events(
            api_client,
            auth_headers,
            session_id=session_id,
            params={"after": "0", "timeout_seconds": 5},
        )
        assert_error_response(response, status_code=404, error_code="not_found")


async def test_events_with_missing_or_invalid_auth_returns_401(
    api_client: httpx.AsyncClient,
) -> None:
    for headers in [{}, {"Authorization": "Bearer invalid-token"}]:
        response = await _get_events(
            api_client,
            headers,
            params={"after": "0", "timeout_seconds": 5},
        )
        assert_error_response(response, status_code=401, error_code="unauthorized")
