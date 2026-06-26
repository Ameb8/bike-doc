"""Background diagnostic turn execution tests."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

import pytest

from bike_doc_api.adk import background
from bike_doc_api.core.config import Settings
from bike_doc_api.models.repair_session import RepairSession, RepairTurn
from bike_doc_api.models.user import User
from bike_doc_api.schemas.event import RepairSessionEventType


class _FakeSession:
    """Fresh async DB session marker used by background tests."""

    def __init__(self) -> None:
        self.commits = 0
        self.rollbacks = 0

    async def commit(self) -> None:
        self.commits += 1

    async def rollback(self) -> None:
        self.rollbacks += 1


class _Store:
    """In-memory rows loaded by ID through repository doubles."""

    def __init__(self) -> None:
        self.user = User(
            id="usr_bg",
            auth_subject="auth|bg",
            email="bg@example.com",
            display_name="Background User",
            skill_level="beginner",
            created_at=datetime(2026, 1, 1, tzinfo=UTC),
        )
        self.session = RepairSession(
            id="rs_bg",
            user_id="usr_bg",
            bike_id="bike_bg",
            phase="diagnostic",
            status="running",
            safety_state="ok",
            current_input_request=None,
            execution_progress=None,
            active_safety_flags=[],
            latest_event_sequence=1,
            created_at=datetime(2026, 1, 1, tzinfo=UTC),
            updated_at=datetime(2026, 1, 1, tzinfo=UTC),
        )
        self.turn = RepairTurn(
            id="turn_bg",
            repair_session_id="rs_bg",
            repair_phase_session_id="phs_bg",
            client_turn_id="client-bg",
            request_hash="hash",
            schema_version="ai_turn.v1",
            phase="diagnostic",
            message={"text": "Chain skips.", "artifact_ids": []},
            start_event_sequence=1,
            created_at=datetime(2026, 1, 1, tzinfo=UTC),
        )
        self.loaded_users: list[str] = []
        self.loaded_turns: list[str] = []
        self.loaded_sessions: list[str] = []
        self.events: list[tuple[str, str | None, dict[str, Any]]] = []


class _UserRepository:
    def __init__(self, _session: _FakeSession, store: _Store) -> None:
        self._store = store

    async def get(self, user_id: str) -> User | None:
        self._store.loaded_users.append(user_id)
        return self._store.user if self._store.user.id == user_id else None


class _TurnRepository:
    def __init__(self, _session: _FakeSession, store: _Store) -> None:
        self._store = store

    async def get(self, turn_id: str) -> RepairTurn | None:
        self._store.loaded_turns.append(turn_id)
        return self._store.turn if self._store.turn.id == turn_id else None


class _RepairSessionRepository:
    def __init__(self, _session: _FakeSession, store: _Store) -> None:
        self._store = store

    async def get(self, repair_session_id: str) -> RepairSession | None:
        self._store.loaded_sessions.append(repair_session_id)
        if self._store.session.id != repair_session_id:
            return None
        return self._store.session

    async def get_for_update(self, repair_session_id: str) -> RepairSession | None:
        return await self.get(repair_session_id)


class _EventRepository:
    def __init__(self, _session: _FakeSession, _store: _Store) -> None:
        pass


class _EventService:
    def __init__(
        self,
        _events: _EventRepository,
        repair_sessions: _RepairSessionRepository,
        *,
        commit: Any = None,
        rollback: Any = None,
    ) -> None:
        self._repair_sessions = repair_sessions
        self._commit = commit
        self._rollback = rollback

    async def append_event(
        self,
        *,
        repair_session_id: str,
        event_type: RepairSessionEventType | str,
        data: dict[str, Any],
        turn_id: str | None = None,
    ) -> object:
        repair_session = await self._repair_sessions.get_for_update(
            repair_session_id,
        )
        assert repair_session is not None
        repair_session.latest_event_sequence += 1
        event_name = RepairSessionEventType(event_type).value
        self._repair_sessions._store.events.append((event_name, turn_id, data))
        if self._commit is not None:
            await self._commit()
        return object()


class _Orchestrator:
    def __init__(self) -> None:
        self.calls: list[tuple[User, RepairTurn]] = []

    async def process_turn(self, *, current_user: User, turn: RepairTurn) -> None:
        self.calls.append((current_user, turn))


def _patch_background_repositories(
    monkeypatch: pytest.MonkeyPatch,
    *,
    store: _Store,
) -> None:
    monkeypatch.setattr(
        background,
        "UserRepository",
        lambda session: _UserRepository(session, store),
    )
    monkeypatch.setattr(
        background,
        "RepairTurnRepository",
        lambda session: _TurnRepository(session, store),
    )
    monkeypatch.setattr(
        background,
        "RepairSessionRepository",
        lambda session: _RepairSessionRepository(session, store),
    )
    monkeypatch.setattr(
        background,
        "RepairSessionEventRepository",
        lambda session: _EventRepository(session, store),
    )


def _patch_background_session(
    monkeypatch: pytest.MonkeyPatch,
    *,
    session: _FakeSession,
) -> None:
    async def get_session_for_database_url(
        database_url: str,
    ) -> AsyncIterator[_FakeSession]:
        assert database_url == "postgresql+asyncpg://test/test"
        yield session

    monkeypatch.setattr(
        background,
        "get_session_for_database_url",
        get_session_for_database_url,
    )
    monkeypatch.setattr(
        background,
        "get_settings",
        lambda: Settings(
            environment="test",
            database_url="postgresql+asyncpg://test/test",
        ),
    )


async def test_background_reloads_rows_by_id_and_invokes_orchestration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _Store()
    fresh_session = _FakeSession()
    orchestrator = _Orchestrator()
    _patch_background_session(monkeypatch, session=fresh_session)
    _patch_background_repositories(monkeypatch, store=store)
    monkeypatch.setattr(
        background,
        "_build_background_orchestrator",
        lambda *, session, settings: orchestrator,
    )

    await background.execute_diagnostic_turn_background(
        "usr_bg",
        "rs_bg",
        "turn_bg",
    )

    assert store.loaded_users == ["usr_bg"]
    assert store.loaded_turns == ["turn_bg"]
    assert store.loaded_sessions == ["rs_bg"]
    assert orchestrator.calls == [(store.user, store.turn)]
    assert fresh_session is not None


async def test_background_missing_user_restores_session_and_writes_terminal_events(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _Store()
    store.user = User(
        id="usr_missing",
        auth_subject="auth|missing",
        email="missing@example.com",
        display_name="Missing User",
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    fresh_session = _FakeSession()
    _patch_background_session(monkeypatch, session=fresh_session)
    _patch_background_repositories(monkeypatch, store=store)
    monkeypatch.setattr(background, "EventService", _EventService)

    await background.execute_diagnostic_turn_background(
        "usr_bg",
        "rs_bg",
        "turn_bg",
    )

    assert store.session.status == "awaiting_user"
    assert [event[0] for event in store.events] == ["error", "turn.completed"]
    assert store.events[0][2] == {
        "code": "diagnostic_processing_error",
        "message": "Diagnostic processing could not be started.",
        "retryable": True,
    }
    assert store.events[1][2]["turn_id"] == "turn_bg"
    assert store.events[1][2]["session"]["status"] == "awaiting_user"
    assert "auth|" not in repr(store.events)
