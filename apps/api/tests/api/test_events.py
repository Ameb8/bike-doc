"""Diagnostic repair-session event stream API tests."""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest
from conftest import ApiTestUser, assert_error_response
from fastapi import FastAPI, Header

from bike_doc_api.api.deps import get_current_user
from bike_doc_api.api.v1.events import get_event_service
from bike_doc_api.core.errors import AuthenticationError
from bike_doc_api.models.event import RepairSessionEvent as RepairSessionEventModel
from bike_doc_api.models.repair_session import RepairSession as RepairSessionModel
from bike_doc_api.models.user import User as UserModel
from bike_doc_api.services.events import EventService

OWNED_SESSION_ID = "rs_owned_contract"
NOT_OWNED_SESSION_ID = "rs_other_user"


class _InMemoryEventStore:
    """Minimal repository double for event stream API tests."""

    def __init__(self, user_id: str) -> None:
        self.sessions = {
            OWNED_SESSION_ID: RepairSessionModel(
                id=OWNED_SESSION_ID,
                user_id=user_id,
                bike_id="bike_owned_contract",
                phase="diagnostic",
                status="created",
                safety_state="ok",
                current_input_request=None,
                execution_progress=None,
                active_safety_flags=[],
                latest_event_sequence=2,
            ),
            NOT_OWNED_SESSION_ID: RepairSessionModel(
                id=NOT_OWNED_SESSION_ID,
                user_id="usr_other",
                bike_id="bike_other_contract",
                phase="diagnostic",
                status="created",
                safety_state="ok",
                current_input_request=None,
                execution_progress=None,
                active_safety_flags=[],
                latest_event_sequence=0,
            ),
        }
        self.events = [
            RepairSessionEventModel(
                id="evt_internal_1",
                repair_session_id=OWNED_SESSION_ID,
                turn_id=None,
                sequence=1,
                type="assistant.delta",
                data={"text": "Check chain tension."},
                created_at=datetime(2026, 6, 21, 17, 0, 0, tzinfo=UTC),
            ),
            RepairSessionEventModel(
                id="evt_internal_2",
                repair_session_id=OWNED_SESSION_ID,
                turn_id=None,
                sequence=2,
                type="assistant.delta",
                data={"text": "Inspect the cassette."},
                created_at=datetime(2026, 6, 21, 17, 0, 1, tzinfo=UTC),
            ),
        ]

    async def get_owned(
        self,
        *,
        repair_session_id: str,
        user_id: str,
    ) -> RepairSessionModel | None:
        session = self.sessions.get(repair_session_id)
        if session is None or session.user_id != user_id:
            return None
        return session

    async def append_for_session(
        self,
        *,
        repair_session_id: str,
        event_type: str,
        data: dict[str, Any],
        turn_id: str | None = None,
    ) -> RepairSessionEventModel:
        session = self.sessions[repair_session_id]
        sequence = session.latest_event_sequence + 1
        event = RepairSessionEventModel(
            id=f"evt_internal_{sequence}",
            repair_session_id=repair_session_id,
            turn_id=turn_id,
            sequence=sequence,
            type=event_type,
            data=data,
            created_at=datetime(2026, 6, 21, 17, 0, sequence, tzinfo=UTC),
        )
        self.events.append(event)
        session.latest_event_sequence = sequence
        return event

    async def list_after_sequence(
        self,
        *,
        repair_session_id: str,
        after_sequence: int,
        limit: int = 100,
    ) -> list[RepairSessionEventModel]:
        return [
            event
            for event in self.events
            if event.repair_session_id == repair_session_id
            and event.sequence > after_sequence
        ][:limit]


@pytest.fixture
def event_store(
    app: FastAPI,
    test_user: ApiTestUser,
) -> Iterator[_InMemoryEventStore]:
    """Override event service dependencies with deterministic in-memory storage."""

    store = _InMemoryEventStore(test_user.id)

    def override_event_service() -> EventService:
        return EventService(store, store)

    app.dependency_overrides[get_event_service] = override_event_service
    yield store
    app.dependency_overrides.pop(get_event_service, None)


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
    event_store: _InMemoryEventStore,
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
    event_store: _InMemoryEventStore,
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
    event_store: _InMemoryEventStore,
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
    event_store: _InMemoryEventStore,
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
    event_store: _InMemoryEventStore,
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
    event_store: _InMemoryEventStore,
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


async def test_invalid_last_event_id_returns_422(
    api_client: httpx.AsyncClient,
    auth_headers: dict[str, str],
    event_store: _InMemoryEventStore,
) -> None:
    for last_event_id in ["-1", "abc", "evt_123", "999999"]:
        response = await _get_events(
            api_client,
            auth_headers,
            params={"timeout_seconds": 5},
            extra_headers={"Last-Event-ID": last_event_id},
        )
        assert_error_response(
            response,
            status_code=422,
            error_code="validation_error",
        )


async def test_invalid_timeout_seconds_returns_422(
    api_client: httpx.AsyncClient,
    auth_headers: dict[str, str],
    event_store: _InMemoryEventStore,
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
    event_store: _InMemoryEventStore,
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
    app: FastAPI,
    test_user: ApiTestUser,
    event_store: _InMemoryEventStore,
) -> None:
    async def auth_checked_user(
        authorization: str | None = Header(alias="Authorization", default=None),
    ) -> UserModel:
        if authorization != "Bearer test-token":
            raise AuthenticationError()
        return UserModel(
            id=test_user.id,
            auth_subject=test_user.auth_subject,
            email=test_user.email,
            display_name=test_user.display_name,
            skill_level=test_user.skill_level,
            created_at=datetime(2026, 1, 1, tzinfo=UTC),
        )

    original_current_user = app.dependency_overrides[get_current_user]
    app.dependency_overrides[get_current_user] = auth_checked_user
    try:
        for headers in [{}, {"Authorization": "Bearer invalid-token"}]:
            response = await _get_events(
                api_client,
                headers,
                params={"after": "0", "timeout_seconds": 5},
            )
            assert_error_response(response, status_code=401, error_code="unauthorized")
    finally:
        app.dependency_overrides[get_current_user] = original_current_user
