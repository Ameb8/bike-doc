"""Diagnostic ADK phase-session manager tests."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from google.adk.sessions import InMemorySessionService
from sqlalchemy.exc import IntegrityError

from bike_doc_api.adk.sessions import (
    DIAGNOSTIC_ADK_APP_NAME,
    DIAGNOSTIC_ADK_USER_ID,
    DiagnosticADKSessionClient,
    DiagnosticPhaseSessionManager,
    StaleInMemoryADKSessionError,
    ensure_adk_session_available,
)
from bike_doc_api.models.repair_session import RepairPhaseSession
from bike_doc_api.schemas.common import RepairSessionPhase


class _PhaseSessionRepo:
    """In-memory phase-session repository double."""

    def __init__(self) -> None:
        self.rows: dict[tuple[str, str], RepairPhaseSession] = {}
        self.raise_integrity_on_add = False

    async def add(self, phase_session: RepairPhaseSession) -> RepairPhaseSession:
        if self.raise_integrity_on_add:
            winning = RepairPhaseSession(
                id="phs_winning",
                repair_session_id=phase_session.repair_session_id,
                phase=phase_session.phase,
                adk_session_id="adk_winning",
                status="active",
                created_at=datetime(2026, 1, 1, tzinfo=UTC),
            )
            self.rows[(winning.repair_session_id, winning.phase)] = winning
            raise IntegrityError("insert", {}, Exception("race"))
        phase_session.id = phase_session.id or "phs_created"
        phase_session.created_at = datetime(2026, 1, 1, tzinfo=UTC)
        self.rows[(phase_session.repair_session_id, phase_session.phase)] = (
            phase_session
        )
        return phase_session

    async def get_for_session_phase(
        self,
        *,
        repair_session_id: str,
        phase: str,
    ) -> RepairPhaseSession | None:
        return self.rows.get((repair_session_id, phase))


class _ADKSessionClient:
    """Fake opaque ADK session client."""

    def __init__(self) -> None:
        self.created: list[str] = []
        self.closed: list[str] = []

    async def create_session(self, *, repair_session_id: str, phase: object) -> str:
        adk_session_id = f"adk_created_{len(self.created) + 1}"
        self.created.append(adk_session_id)
        return adk_session_id

    async def close_session(self, *, adk_session_id: str) -> None:
        self.closed.append(adk_session_id)


async def test_creates_diagnostic_phase_session_lazily() -> None:
    repo = _PhaseSessionRepo()
    client = _ADKSessionClient()

    phase_session = await DiagnosticPhaseSessionManager(
        phase_sessions=repo,
        adk_sessions=client,
    ).ensure_diagnostic_session(repair_session_id="rs_1")

    assert phase_session.id == "phs_created"
    assert phase_session.phase == "diagnostic"
    assert phase_session.adk_session_id == "adk_created_1"
    assert client.created == ["adk_created_1"]


async def test_real_adk_session_client_creates_retrievable_session() -> None:
    service = InMemorySessionService()
    client = DiagnosticADKSessionClient(service)

    adk_session_id = await client.create_session(
        repair_session_id="rs_1",
        phase=RepairSessionPhase.DIAGNOSTIC,
    )

    stored = await service.get_session(
        app_name=DIAGNOSTIC_ADK_APP_NAME,
        user_id=DIAGNOSTIC_ADK_USER_ID,
        session_id=adk_session_id,
    )
    assert adk_session_id.startswith("adk_diagnostic_sess_")
    assert stored is not None
    assert stored.state["repair_session_id"] == "rs_1"
    assert stored.state["phase"] == "diagnostic"


async def test_real_adk_session_client_close_deletes_session_when_supported() -> None:
    service = InMemorySessionService()
    client = DiagnosticADKSessionClient(service)
    adk_session_id = await client.create_session(
        repair_session_id="rs_1",
        phase=RepairSessionPhase.DIAGNOSTIC,
    )

    await client.close_session(adk_session_id=adk_session_id)

    assert (
        await service.get_session(
            app_name=DIAGNOSTIC_ADK_APP_NAME,
            user_id=DIAGNOSTIC_ADK_USER_ID,
            session_id=adk_session_id,
        )
        is None
    )


async def test_missing_in_memory_adk_session_is_recoverable_stale_state() -> None:
    service = InMemorySessionService()

    with pytest.raises(StaleInMemoryADKSessionError):
        await ensure_adk_session_available(
            service,
            adk_session_id="adk_diagnostic_missing",
        )


async def test_resumes_existing_diagnostic_phase_session() -> None:
    repo = _PhaseSessionRepo()
    existing = RepairPhaseSession(
        id="phs_existing",
        repair_session_id="rs_1",
        phase="diagnostic",
        adk_session_id="adk_existing",
        status="active",
    )
    repo.rows[("rs_1", "diagnostic")] = existing
    client = _ADKSessionClient()

    phase_session = await DiagnosticPhaseSessionManager(
        phase_sessions=repo,
        adk_sessions=client,
    ).ensure_diagnostic_session(repair_session_id="rs_1")

    assert phase_session is existing
    assert client.created == []


async def test_app_owned_phase_session_id_is_report_session_context() -> None:
    repo = _PhaseSessionRepo()
    client = _ADKSessionClient()

    phase_session = await DiagnosticPhaseSessionManager(
        phase_sessions=repo,
        adk_sessions=client,
    ).ensure_diagnostic_session(repair_session_id="rs_1")

    assert phase_session.id.startswith("phs_")
    assert not phase_session.id.startswith("adk_")
    assert phase_session.adk_session_id.startswith("adk_")


async def test_creation_race_reuses_winning_row_and_closes_orphan() -> None:
    repo = _PhaseSessionRepo()
    repo.raise_integrity_on_add = True
    client = _ADKSessionClient()
    rollback_count = 0

    async def rollback() -> None:
        nonlocal rollback_count
        rollback_count += 1

    phase_session = await DiagnosticPhaseSessionManager(
        phase_sessions=repo,
        adk_sessions=client,
        rollback=rollback,
    ).ensure_diagnostic_session(repair_session_id="rs_1")

    assert phase_session.id == "phs_winning"
    assert phase_session.adk_session_id == "adk_winning"
    assert client.closed == ["adk_created_1"]
    assert rollback_count == 1
