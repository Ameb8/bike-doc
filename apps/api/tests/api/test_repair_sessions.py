"""Diagnostic repair-session API tests."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import httpx
import pytest
from conftest import (
    ApiTestUser,
    assert_error_response,
    assert_no_private_fields,
    assert_repair_session_shape,
)
from fastapi import FastAPI

from bike_doc_api.api.deps import get_current_user
from bike_doc_api.api.v1.repair_sessions import get_repair_session_service
from bike_doc_api.core.errors import IdempotencyConflictError, NotFoundError
from bike_doc_api.models.user import User as UserModel
from bike_doc_api.schemas.repair_session import (
    LatestReports,
    RepairSession,
    RepairSessionCreate,
    RepairSessionList,
)

OWNED_BIKE_ID = "bike_owned_contract"
NOT_OWNED_BIKE_ID = "bike_other_user"
OWNED_SESSION_ID = "rs_owned_contract"
OWNED_NEWER_SESSION_ID = "rs_owned_newer_contract"
OWNED_SECOND_BIKE_SESSION_ID = "rs_owned_second_bike_contract"
NOT_OWNED_SESSION_ID = "rs_other_user"


class FakeRepairSessionService:
    """In-memory route service for repair-session API tests."""

    def __init__(self) -> None:
        timestamp = datetime(2026, 1, 1, tzinfo=UTC)
        newer_timestamp = datetime(2026, 1, 2, tzinfo=UTC)
        self.owned_bike_ids = {OWNED_BIKE_ID, "bike_owned_second"}
        self.sessions: dict[str, RepairSession] = {
            OWNED_SESSION_ID: _public_session(
                session_id=OWNED_SESSION_ID,
                user_id="usr_contract_user",
                bike_id=OWNED_BIKE_ID,
                created_at=timestamp,
                updated_at=timestamp,
            ),
            OWNED_NEWER_SESSION_ID: _public_session(
                session_id=OWNED_NEWER_SESSION_ID,
                user_id="usr_contract_user",
                bike_id=OWNED_BIKE_ID,
                created_at=newer_timestamp,
                updated_at=newer_timestamp,
                status="awaiting_user",
            ),
            OWNED_SECOND_BIKE_SESSION_ID: _public_session(
                session_id=OWNED_SECOND_BIKE_SESSION_ID,
                user_id="usr_contract_user",
                bike_id="bike_owned_second",
                created_at=newer_timestamp,
                updated_at=newer_timestamp,
            ),
            NOT_OWNED_SESSION_ID: _public_session(
                session_id=NOT_OWNED_SESSION_ID,
                user_id="usr_other_user",
                bike_id=NOT_OWNED_BIKE_ID,
                created_at=timestamp,
                updated_at=timestamp,
            ),
        }
        self.sessions_by_client_id: dict[str, RepairSession] = {}
        self.hashes_by_client_id: dict[str, str] = {}

    async def list_sessions(
        self,
        *,
        current_user: UserModel,
        bike_id: str,
        status: Any | None = None,
        limit: int = 20,
        cursor: str | None = None,
    ) -> RepairSessionList:
        _ = cursor
        if bike_id not in self.owned_bike_ids:
            raise NotFoundError()
        status_value = None if status is None else status.value
        sessions = [
            session
            for session in self.sessions.values()
            if session.user_id == current_user.id
            and session.bike_id == bike_id
            and (status_value is None or session.status == status_value)
        ]
        sessions.sort(
            key=lambda session: (session.created_at, session.id), reverse=True
        )
        return RepairSessionList(items=sessions[:limit], next_cursor=None)

    async def create_session(
        self,
        *,
        current_user: UserModel,
        request: RepairSessionCreate,
    ) -> RepairSession:
        request_hash = request.bike_id
        if request.client_session_id is not None:
            existing = self.sessions_by_client_id.get(request.client_session_id)
            if existing is not None:
                if self.hashes_by_client_id[request.client_session_id] != request_hash:
                    raise IdempotencyConflictError()
                return existing

        if request.bike_id not in self.owned_bike_ids:
            raise NotFoundError()

        timestamp = datetime(2026, 1, len(self.sessions_by_client_id) + 2, tzinfo=UTC)
        session = _public_session(
            session_id=f"rs_created_{len(self.sessions_by_client_id) + 1}",
            user_id=current_user.id,
            bike_id=request.bike_id,
            created_at=timestamp,
            updated_at=timestamp,
        )
        self.sessions[session.id] = session
        if request.client_session_id is not None:
            self.sessions_by_client_id[request.client_session_id] = session
            self.hashes_by_client_id[request.client_session_id] = request_hash
        return session

    async def get_session(
        self,
        *,
        current_user: UserModel,
        repair_session_id: str,
    ) -> RepairSession:
        session = self.sessions.get(repair_session_id)
        if session is None or session.user_id != current_user.id:
            raise NotFoundError()
        return session


def _public_session(
    *,
    session_id: str,
    user_id: str,
    bike_id: str,
    created_at: datetime,
    updated_at: datetime,
    status: str = "created",
) -> RepairSession:
    return RepairSession(
        id=session_id,
        user_id=user_id,
        bike_id=bike_id,
        phase="diagnostic",
        status=status,
        safety_state="ok",
        current_input_request=None,
        execution_progress=None,
        latest_reports=LatestReports(
            diagnostic_report_id=None,
            plan_report_id=None,
            execution_report_id=None,
            shop_referral_report_id=None,
        ),
        latest_event_id="0",
        created_at=created_at,
        updated_at=updated_at,
    )


@pytest.fixture(autouse=True)
def repair_session_service_override(app: FastAPI) -> None:
    """Override the route service without hitting a real database."""

    service = FakeRepairSessionService()
    app.dependency_overrides[get_repair_session_service] = lambda: service


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


async def test_list_repair_sessions_returns_owned_bike_sessions_newest_first(
    api_client: httpx.AsyncClient,
    auth_headers: dict[str, str],
    test_user: ApiTestUser,
) -> None:
    response = await api_client.get(
        f"/v1/repair-sessions?bike_id={OWNED_BIKE_ID}",
        headers=auth_headers,
    )

    assert response.status_code == 200
    body = response.json()
    assert body["next_cursor"] is None
    assert [session["id"] for session in body["items"]] == [
        OWNED_NEWER_SESSION_ID,
        OWNED_SESSION_ID,
    ]
    assert all(session["user_id"] == test_user.id for session in body["items"])
    assert all(session["bike_id"] == OWNED_BIKE_ID for session in body["items"])
    assert_no_private_fields(body)


async def test_list_repair_sessions_filters_by_bike_status_and_limit(
    api_client: httpx.AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    bike_filtered = await api_client.get(
        "/v1/repair-sessions?bike_id=bike_owned_second",
        headers=auth_headers,
    )
    status_filtered = await api_client.get(
        f"/v1/repair-sessions?bike_id={OWNED_BIKE_ID}&status=awaiting_user",
        headers=auth_headers,
    )
    limited = await api_client.get(
        f"/v1/repair-sessions?bike_id={OWNED_BIKE_ID}&limit=1",
        headers=auth_headers,
    )

    assert [session["id"] for session in bike_filtered.json()["items"]] == [
        OWNED_SECOND_BIKE_SESSION_ID
    ]
    assert [session["id"] for session in status_filtered.json()["items"]] == [
        OWNED_NEWER_SESSION_ID
    ]
    assert [session["id"] for session in limited.json()["items"]] == [
        OWNED_NEWER_SESSION_ID
    ]


async def test_list_repair_sessions_empty_result_keeps_list_envelope(
    api_client: httpx.AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    response = await api_client.get(
        f"/v1/repair-sessions?bike_id={OWNED_BIKE_ID}&status=completed",
        headers=auth_headers,
    )

    assert response.status_code == 200
    assert response.json() == {"items": [], "next_cursor": None}


async def test_list_repair_sessions_omitted_limit_defaults_to_twenty(
    api_client: httpx.AsyncClient,
    auth_headers: dict[str, str],
    app: FastAPI,
) -> None:
    service = app.dependency_overrides[get_repair_session_service]()
    timestamp = datetime(2026, 2, 1, tzinfo=UTC)
    for index in range(25):
        session_id = f"rs_bulk_{index:02d}"
        service.sessions[session_id] = _public_session(
            session_id=session_id,
            user_id="usr_contract_user",
            bike_id=OWNED_BIKE_ID,
            created_at=timestamp,
            updated_at=timestamp,
        )

    response = await api_client.get(
        f"/v1/repair-sessions?bike_id={OWNED_BIKE_ID}",
        headers=auth_headers,
    )

    assert response.status_code == 200
    assert len(response.json()["items"]) == 20


async def test_list_repair_sessions_with_unknown_or_not_owned_bike_returns_404(
    api_client: httpx.AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    for bike_id in ["bike_missing", NOT_OWNED_BIKE_ID]:
        response = await api_client.get(
            f"/v1/repair-sessions?bike_id={bike_id}",
            headers=auth_headers,
        )
        assert_error_response(response, status_code=404, error_code="not_found")


async def test_list_repair_sessions_validation_errors(
    api_client: httpx.AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    for params in [
        {},
        {"bike_id": OWNED_BIKE_ID, "status": "paused"},
        {"bike_id": OWNED_BIKE_ID, "limit": "0"},
        {"bike_id": OWNED_BIKE_ID, "limit": "101"},
        {"bike_id": OWNED_BIKE_ID, "cursor": ""},
    ]:
        response = await api_client.get(
            "/v1/repair-sessions",
            headers=auth_headers,
            params=params,
        )
        assert_error_response(
            response,
            status_code=422,
            error_code="validation_error",
        )


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
    app: FastAPI,
    api_client: httpx.AsyncClient,
) -> None:
    app.dependency_overrides.pop(get_current_user, None)

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
