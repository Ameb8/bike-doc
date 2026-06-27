"""Repair session service tests."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy.exc import IntegrityError

from bike_doc_api.core.errors import IdempotencyConflictError, NotFoundError
from bike_doc_api.models.bike import BikeProfile
from bike_doc_api.models.repair_session import RepairSession as RepairSessionModel
from bike_doc_api.models.user import User
from bike_doc_api.schemas.repair_session import RepairSessionCreate
from bike_doc_api.services.repair_sessions import RepairSessionService


class FakeBikeRepository:
    """In-memory bike repository for service tests."""

    def __init__(self, bikes: list[BikeProfile]) -> None:
        self.bikes = bikes

    async def get_owned_active(
        self,
        *,
        bike_id: str,
        user_id: str,
    ) -> BikeProfile | None:
        for bike in self.bikes:
            if (
                bike.id == bike_id
                and bike.user_id == user_id
                and bike.deleted_at is None
            ):
                return bike
        return None


class FakeRepairSessionRepository:
    """In-memory repair-session repository for service tests."""

    def __init__(self) -> None:
        self.sessions: list[RepairSessionModel] = []

    async def add(self, repair_session: RepairSessionModel) -> RepairSessionModel:
        if repair_session.id is None:
            repair_session.id = f"rs_{len(self.sessions) + 1}"
        timestamp = datetime(2026, 1, len(self.sessions) + 1, tzinfo=UTC)
        repair_session.created_at = timestamp
        repair_session.updated_at = timestamp
        self.sessions.append(repair_session)
        return repair_session

    async def get_owned(
        self,
        *,
        repair_session_id: str,
        user_id: str,
    ) -> RepairSessionModel | None:
        for repair_session in self.sessions:
            if (
                repair_session.id == repair_session_id
                and repair_session.user_id == user_id
            ):
                return repair_session
        return None

    async def get_by_client_session_id(
        self,
        *,
        user_id: str,
        client_session_id: str,
    ) -> RepairSessionModel | None:
        for repair_session in self.sessions:
            if (
                repair_session.user_id == user_id
                and repair_session.client_session_id == client_session_id
            ):
                return repair_session
        return None


class _UniqueViolationError(Exception):
    sqlstate = "23505"


class RaceRepairSessionRepository(FakeRepairSessionRepository):
    """Repair-session repository double that simulates an idempotency race."""

    def __init__(self, winning_session: RepairSessionModel) -> None:
        super().__init__()
        self._winning_session = winning_session
        self._add_attempts = 0

    async def add(self, repair_session: RepairSessionModel) -> RepairSessionModel:
        self._add_attempts += 1
        if self._add_attempts == 1:
            self.sessions.append(self._winning_session)
            raise IntegrityError("insert", {}, _UniqueViolationError())
        return await super().add(repair_session)


class ExpiringUser:
    """User double that fails if the service re-reads the user ID after rollback."""

    def __init__(self, user_id: str) -> None:
        self._user_id = user_id
        self._reads = 0

    @property
    def id(self) -> str:
        self._reads += 1
        if self._reads > 1:
            raise AssertionError("user id was re-read after rollback")
        return self._user_id


def _user(user_id: str = "usr_owner") -> User:
    return User(
        id=user_id,
        auth_subject=f"auth|{user_id}",
        email=f"{user_id}@example.com",
        display_name=user_id,
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
    )


def _bike(*, bike_id: str = "bike_owned", user_id: str = "usr_owner") -> BikeProfile:
    return BikeProfile(
        id=bike_id,
        user_id=user_id,
        display_name="Commuter",
        deleted_at=None,
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
        updated_at=datetime(2026, 1, 1, tzinfo=UTC),
    )


def _service(
    *,
    bikes: list[BikeProfile] | None = None,
) -> tuple[RepairSessionService, FakeRepairSessionRepository]:
    repair_sessions = FakeRepairSessionRepository()
    service = RepairSessionService(
        FakeBikeRepository(bikes if bikes is not None else [_bike()]),
        repair_sessions,
    )
    return service, repair_sessions


async def test_create_session_initializes_public_diagnostic_defaults() -> None:
    service, _ = _service()

    session = await service.create_session(
        current_user=_user(),
        request=RepairSessionCreate(
            bike_id="bike_owned",
            client_session_id="client-session-1",
        ),
    )

    assert session.user_id == "usr_owner"
    assert session.bike_id == "bike_owned"
    assert session.phase == "diagnostic"
    assert session.status == "created"
    assert session.safety_state == "ok"
    assert session.current_input_request is None
    assert session.execution_progress is None
    assert session.latest_reports.diagnostic_report_id is None
    assert session.latest_event_id == "0"


async def test_create_session_requires_owned_active_bike() -> None:
    service, _ = _service(bikes=[_bike(user_id="usr_other")])

    with pytest.raises(NotFoundError):
        await service.create_session(
            current_user=_user(),
            request=RepairSessionCreate(bike_id="bike_owned"),
        )


async def test_repeating_client_session_id_returns_original_session() -> None:
    service, repair_sessions = _service()
    request = RepairSessionCreate(
        bike_id="bike_owned",
        client_session_id="client-session-repeat",
    )

    first = await service.create_session(current_user=_user(), request=request)
    retry = await service.create_session(current_user=_user(), request=request)

    assert retry == first
    assert len(repair_sessions.sessions) == 1


async def test_reusing_client_session_id_with_different_payload_conflicts() -> None:
    service, _ = _service(bikes=[_bike(), _bike(bike_id="bike_second")])
    current_user = _user()

    await service.create_session(
        current_user=current_user,
        request=RepairSessionCreate(
            bike_id="bike_owned",
            client_session_id="client-session-conflict",
        ),
    )

    with pytest.raises(IdempotencyConflictError):
        await service.create_session(
            current_user=current_user,
            request=RepairSessionCreate(
                bike_id="bike_second",
                client_session_id="client-session-conflict",
            ),
        )


async def test_get_session_requires_ownership() -> None:
    service, repair_sessions = _service()
    owned = await repair_sessions.add(
        RepairSessionModel(
            user_id="usr_owner",
            bike_id="bike_owned",
            phase="diagnostic",
            status="created",
            safety_state="ok",
            active_safety_flags=[],
            latest_event_sequence=0,
        ),
    )
    await repair_sessions.add(
        RepairSessionModel(
            user_id="usr_other",
            bike_id="bike_other",
            phase="diagnostic",
            status="created",
            safety_state="ok",
            active_safety_flags=[],
            latest_event_sequence=0,
        ),
    )

    assert (
        await service.get_session(
            current_user=_user(),
            repair_session_id=owned.id,
        )
    ).id == owned.id

    with pytest.raises(NotFoundError):
        await service.get_session(
            current_user=_user(),
            repair_session_id="rs_2",
        )


async def test_create_session_recovers_from_unique_idempotency_race() -> None:
    winning_session = RepairSessionModel(
        id="rs_winning",
        user_id="usr_owner",
        bike_id="bike_owned",
        client_session_id="client-session-race",
        request_hash="47fb0e3b3c610b3a329c2c21b3135388c410fbd7c4beccbbf6798aeff0983ffb",
        phase="diagnostic",
        status="created",
        safety_state="ok",
        active_safety_flags=[],
        latest_event_sequence=0,
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
        updated_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    repair_sessions = RaceRepairSessionRepository(winning_session)
    rollback_calls = 0

    async def rollback() -> None:
        nonlocal rollback_calls
        rollback_calls += 1

    service = RepairSessionService(
        FakeBikeRepository([_bike()]),
        repair_sessions,
        rollback=rollback,
    )

    session = await service.create_session(
        current_user=ExpiringUser("usr_owner"),
        request=RepairSessionCreate(
            bike_id="bike_owned",
            client_session_id="client-session-race",
        ),
    )

    assert session.id == "rs_winning"
    assert rollback_calls == 1
