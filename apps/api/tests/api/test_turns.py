"""Diagnostic turn API tests."""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest
from conftest import ApiTestUser, assert_error_response, assert_repair_session_shape
from fastapi import FastAPI, Header

from bike_doc_api.api.deps import get_current_user
from bike_doc_api.api.v1.events import get_event_service
from bike_doc_api.api.v1.turns import get_turn_service
from bike_doc_api.core.errors import AuthenticationError
from bike_doc_api.models.artifact import ArtifactRef as ArtifactRefModel
from bike_doc_api.models.event import RepairSessionEvent as RepairSessionEventModel
from bike_doc_api.models.repair_session import (
    RepairPhaseSession as RepairPhaseSessionModel,
)
from bike_doc_api.models.repair_session import RepairSession as RepairSessionModel
from bike_doc_api.models.repair_session import RepairTurn as RepairTurnModel
from bike_doc_api.models.user import User as UserModel
from bike_doc_api.schemas.event import (
    RepairSessionEventType,
    validate_repair_session_event_data,
)
from bike_doc_api.schemas.repair_session import repair_session_from_model
from bike_doc_api.services.events import EventService
from bike_doc_api.services.turns import TurnService

OWNED_SESSION_ID = "rs_owned_contract"
NOT_OWNED_SESSION_ID = "rs_other_user"
OWNED_BIKE_ID = "bike_owned_contract"
OWNED_ARTIFACT_ID = "art_owned_contract"
WRONG_SESSION_ID = "rs_wrong_artifact_session"


class _InMemoryTurnStore:
    """Repository double shared by turn acceptance and event replay tests."""

    def __init__(self, user_id: str) -> None:
        self.sessions = {
            OWNED_SESSION_ID: self._session(
                session_id=OWNED_SESSION_ID,
                user_id=user_id,
                bike_id=OWNED_BIKE_ID,
                phase="diagnostic",
                status="created",
            ),
            WRONG_SESSION_ID: self._session(
                session_id=WRONG_SESSION_ID,
                user_id=user_id,
                bike_id=OWNED_BIKE_ID,
                phase="diagnostic",
                status="created",
            ),
            NOT_OWNED_SESSION_ID: self._session(
                session_id=NOT_OWNED_SESSION_ID,
                user_id="usr_other",
                bike_id="bike_other_contract",
                phase="diagnostic",
                status="created",
            ),
            "rs_completed_contract": self._session(
                session_id="rs_completed_contract",
                user_id=user_id,
                bike_id=OWNED_BIKE_ID,
                phase="diagnostic",
                status="completed",
            ),
        }
        self.phase_sessions: dict[tuple[str, str], RepairPhaseSessionModel] = {}
        self.turns: dict[tuple[str, str], RepairTurnModel] = {}
        self.events: list[RepairSessionEventModel] = []
        self.orchestrated_turn_ids: list[str] = []
        self.artifacts = {
            OWNED_ARTIFACT_ID: ArtifactRefModel(
                id=OWNED_ARTIFACT_ID,
                user_id=user_id,
                repair_session_id=OWNED_SESSION_ID,
                purpose="diagnostic_photo",
                media_type="image",
                mime_type="image/jpeg",
                filename="owned.jpg",
                byte_size=123,
                status="ready",
                content_sha256="a" * 64,
                storage_provider="local",
                storage_path="objects/owned.jpg",
                created_at=datetime(2026, 1, 1, tzinfo=UTC),
                updated_at=datetime(2026, 1, 1, tzinfo=UTC),
            ),
            "art_other_user": ArtifactRefModel(
                id="art_other_user",
                user_id="usr_other",
                repair_session_id=OWNED_SESSION_ID,
                purpose="diagnostic_photo",
                media_type="image",
                mime_type="image/jpeg",
                filename="other.jpg",
                byte_size=123,
                status="ready",
                content_sha256="b" * 64,
                storage_provider="local",
                storage_path="objects/other.jpg",
                created_at=datetime(2026, 1, 1, tzinfo=UTC),
                updated_at=datetime(2026, 1, 1, tzinfo=UTC),
            ),
            "art_wrong_session": ArtifactRefModel(
                id="art_wrong_session",
                user_id=user_id,
                repair_session_id=WRONG_SESSION_ID,
                purpose="diagnostic_photo",
                media_type="image",
                mime_type="image/jpeg",
                filename="wrong-session.jpg",
                byte_size=123,
                status="ready",
                content_sha256="c" * 64,
                storage_provider="local",
                storage_path="objects/wrong-session.jpg",
                created_at=datetime(2026, 1, 1, tzinfo=UTC),
                updated_at=datetime(2026, 1, 1, tzinfo=UTC),
            ),
        }

    @staticmethod
    def _session(
        *,
        session_id: str,
        user_id: str,
        bike_id: str,
        phase: str,
        status: str,
    ) -> RepairSessionModel:
        return RepairSessionModel(
            id=session_id,
            user_id=user_id,
            bike_id=bike_id,
            phase=phase,
            status=status,
            safety_state="ok",
            current_input_request=None,
            execution_progress=None,
            active_safety_flags=[],
            latest_event_sequence=0,
            created_at=datetime(2026, 1, 1, tzinfo=UTC),
            updated_at=datetime(2026, 1, 1, tzinfo=UTC),
        )

    async def get_owned(
        self,
        *,
        user_id: str,
        repair_session_id: str | None = None,
        artifact_id: str | None = None,
    ) -> RepairSessionModel | ArtifactRefModel | None:
        if repair_session_id is not None:
            session = self.sessions.get(repair_session_id)
            if session is None or session.user_id != user_id:
                return None
            return session
        if artifact_id is not None:
            artifact = self.artifacts.get(artifact_id)
            if artifact is None or artifact.user_id != user_id:
                return None
            return artifact
        return None

    async def get_owned_for_update(
        self,
        *,
        repair_session_id: str,
        user_id: str,
    ) -> RepairSessionModel | None:
        result = await self.get_owned(
            repair_session_id=repair_session_id,
            user_id=user_id,
        )
        return result if isinstance(result, RepairSessionModel) else None

    async def add(
        self,
        model: RepairPhaseSessionModel | RepairTurnModel | RepairSessionEventModel,
    ) -> RepairPhaseSessionModel | RepairTurnModel | RepairSessionEventModel:
        if isinstance(model, RepairPhaseSessionModel):
            if model.id is None:
                model.id = f"phs_{len(self.phase_sessions) + 1}"
            model.created_at = datetime(2026, 1, 1, tzinfo=UTC)
            self.phase_sessions[(model.repair_session_id, model.phase)] = model
            return model
        if isinstance(model, RepairTurnModel):
            if model.id is None:
                model.id = f"turn_{len(self.turns) + 1}"
            model.created_at = datetime(2026, 1, 1, len(self.turns) + 1, tzinfo=UTC)
            self.turns[(model.repair_session_id, model.client_turn_id)] = model
            return model
        if model.id is None:
            model.id = f"evt_internal_{len(self.events) + 1}"
        model.created_at = datetime(2026, 1, 1, 0, 0, model.sequence, tzinfo=UTC)
        self.events.append(model)
        return model

    async def get_for_session_phase(
        self,
        *,
        repair_session_id: str,
        phase: str,
    ) -> RepairPhaseSessionModel | None:
        return self.phase_sessions.get((repair_session_id, phase))

    async def get_by_client_turn_id(
        self,
        *,
        repair_session_id: str,
        client_turn_id: str,
    ) -> RepairTurnModel | None:
        return self.turns.get((repair_session_id, client_turn_id))

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
            created_at=datetime(2026, 1, 1, 0, 0, sequence, tzinfo=UTC),
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


class _CompletedTurnOrchestrator:
    """Fake orchestration boundary used by route tests."""

    def __init__(self, store: _InMemoryTurnStore) -> None:
        self.store = store

    async def process_turn(
        self,
        *,
        current_user: UserModel,
        turn: RepairTurnModel,
    ) -> None:
        self.store.orchestrated_turn_ids.append(turn.id)
        session = self.store.sessions[turn.repair_session_id]
        sequence = session.latest_event_sequence + 1
        session.latest_event_sequence = sequence
        event_data = validate_repair_session_event_data(
            RepairSessionEventType.TURN_COMPLETED,
            {
                "turn_id": turn.id,
                "session": repair_session_from_model(session).model_dump(mode="json"),
            },
        )
        await self.store.add(
            RepairSessionEventModel(
                repair_session_id=session.id,
                turn_id=turn.id,
                sequence=sequence,
                type=RepairSessionEventType.TURN_COMPLETED.value,
                data=event_data,
            ),
        )


def _parse_sse_frames(text: str) -> list[dict[str, Any]]:
    frames: list[dict[str, Any]] = []
    for raw_frame in text.strip().split("\n\n"):
        if not raw_frame:
            continue
        frame: dict[str, Any] = {}
        for line in raw_frame.splitlines():
            field, separator, value = line.partition(":")
            assert separator
            frame[field] = value.lstrip()
        frame["data"] = json.loads(frame["data"])
        frames.append(frame)
    return frames


@pytest.fixture(autouse=True)
def turn_service_override(
    app: FastAPI,
    test_user: ApiTestUser,
) -> Iterator[_InMemoryTurnStore]:
    """Override turn and event services with shared in-memory storage."""

    store = _InMemoryTurnStore(test_user.id)
    turn_service = TurnService(
        store,
        store,
        store,
        store,
        store,
        orchestrator=_CompletedTurnOrchestrator(store),
    )
    event_service = EventService(store, store)
    app.dependency_overrides[get_turn_service] = lambda: turn_service
    app.dependency_overrides[get_event_service] = lambda: event_service
    yield store
    app.dependency_overrides.pop(get_turn_service, None)
    app.dependency_overrides.pop(get_event_service, None)


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


async def test_accepted_text_turn_replays_started_and_completed_events(
    api_client: httpx.AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    accepted = await _post_turn(
        api_client,
        auth_headers,
        OWNED_SESSION_ID,
        _valid_turn_payload(client_turn_id="client-turn-replay"),
    )
    assert accepted.status_code == 202
    accepted_body = accepted.json()

    events = await api_client.get(
        f"/v1/repair-sessions/{OWNED_SESSION_ID}/events",
        headers={**auth_headers, "Accept": "text/event-stream"},
        params={"after": "0", "timeout_seconds": 5},
    )

    assert events.status_code == 200
    frames = _parse_sse_frames(events.text)
    assert [frame["event"] for frame in frames] == ["turn.started", "turn.completed"]
    assert frames[0]["id"] == accepted_body["start_event_id"]
    assert frames[0]["data"]["turn_id"] == accepted_body["turn_id"]
    assert frames[0]["data"]["data"] == {
        "turn_id": accepted_body["turn_id"],
        "phase": "diagnostic",
    }
    assert frames[1]["data"]["turn_id"] == accepted_body["turn_id"]


async def test_repeating_client_turn_id_returns_original_acceptance(
    api_client: httpx.AsyncClient,
    auth_headers: dict[str, str],
    turn_service_override: _InMemoryTurnStore,
) -> None:
    payload = _valid_turn_payload(client_turn_id="client-turn-repeat")

    first = await _post_turn(api_client, auth_headers, OWNED_SESSION_ID, payload)
    retry = await _post_turn(api_client, auth_headers, OWNED_SESSION_ID, payload)

    assert first.status_code == 202
    assert retry.status_code == 202
    assert retry.json() == first.json()
    assert turn_service_override.orchestrated_turn_ids == [first.json()["turn_id"]]


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


async def test_post_turn_with_missing_or_invalid_auth_returns_401(
    api_client: httpx.AsyncClient,
    app: FastAPI,
    auth_headers: dict[str, str],
    test_user: ApiTestUser,
) -> None:
    async def auth_checked_user(
        authorization: str | None = Header(alias="Authorization", default=None),
    ) -> UserModel:
        if authorization != auth_headers["Authorization"]:
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
            response = await _post_turn(
                api_client,
                headers,
                OWNED_SESSION_ID,
                _valid_turn_payload(),
            )
            assert_error_response(response, status_code=401, error_code="unauthorized")
    finally:
        app.dependency_overrides[get_current_user] = original_current_user
