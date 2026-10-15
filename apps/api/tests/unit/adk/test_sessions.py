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


async def test_creates_diagnostic_phase_session_lazily() -> None:
    repo = _PhaseSessionRepo()

    phase_session = await DiagnosticPhaseSessionManager(
        phase_sessions=repo,
    ).ensure_diagnostic_session(repair_session_id="rs_1")

    assert phase_session.id == "phs_created"
    assert phase_session.phase == "diagnostic"
    assert phase_session.adk_session_id is None
    assert phase_session.diagnostic_report_schema_version == "diagnostic_report.v2"


async def test_snapshots_configured_report_version() -> None:
    repo = _PhaseSessionRepo()

    phase_session = await DiagnosticPhaseSessionManager(
        phase_sessions=repo,
        diagnostic_report_version="diagnostic_report.v2",
    ).ensure_diagnostic_session(repair_session_id="rs_1")

    assert phase_session.diagnostic_report_schema_version == "diagnostic_report.v2"


async def test_existing_session_keeps_report_version_after_rollback() -> None:
    repo = _PhaseSessionRepo()
    existing = RepairPhaseSession(
        id="phs_existing",
        repair_session_id="rs_1",
        phase="diagnostic",
        adk_session_id="adk_existing",
        diagnostic_report_schema_version="diagnostic_report.v1",
        status="active",
    )
    repo.rows[("rs_1", "diagnostic")] = existing

    phase_session = await DiagnosticPhaseSessionManager(
        phase_sessions=repo,
        diagnostic_report_version="diagnostic_report.v2",
    ).ensure_diagnostic_session(repair_session_id="rs_1")

    assert phase_session is existing
    assert phase_session.diagnostic_report_schema_version == "diagnostic_report.v1"


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

    phase_session = await DiagnosticPhaseSessionManager(
        phase_sessions=repo,
    ).ensure_diagnostic_session(repair_session_id="rs_1")

    assert phase_session is existing


async def test_app_owned_phase_session_id_is_report_session_context() -> None:
    repo = _PhaseSessionRepo()

    phase_session = await DiagnosticPhaseSessionManager(
        phase_sessions=repo,
    ).ensure_diagnostic_session(repair_session_id="rs_1")

    assert phase_session.id.startswith("phs_")
    assert not phase_session.id.startswith("adk_")
    assert phase_session.adk_session_id is None


async def test_creation_failure_leaves_rollback_to_acceptance() -> None:
    repo = _PhaseSessionRepo()
    repo.raise_integrity_on_add = True
    with pytest.raises(IntegrityError):
        await DiagnosticPhaseSessionManager(
            phase_sessions=repo,
        ).ensure_diagnostic_session(repair_session_id="rs_1")


class _BindingRepo:
    def __init__(self) -> None:
        self.binding: str | None = None
        self.calls = 0
        self.crash = False

    async def bind_adk_session_if_null(
        self, *, phase_session_id: str, adk_session_id: str
    ) -> str:
        self.calls += 1
        if self.crash:
            raise RuntimeError("injected crash before binding")
        if self.binding is None:
            self.binding = adk_session_id
        return self.binding


async def test_crash_before_binding_reuses_durable_identity() -> None:
    service = InMemorySessionService()
    repo = _BindingRepo()
    repo.crash = True
    phase = RepairPhaseSession(id="phs_one", repair_session_id="rs_one")
    with pytest.raises(RuntimeError, match="injected crash"):
        await DiagnosticADKSessionClient(service).ensure_or_resume(
            phase_session=phase, phase_sessions=repo
        )
    repo.crash = False
    identity = await DiagnosticADKSessionClient(service).ensure_or_resume(
        phase_session=phase, phase_sessions=repo
    )
    assert identity == "adk_diagnostic_phs_one"
    sessions = await service.list_sessions(
        app_name=DIAGNOSTIC_ADK_APP_NAME, user_id=DIAGNOSTIC_ADK_USER_ID
    )
    assert len(sessions.sessions) == 1


async def test_existing_binding_is_never_recreated_or_rebound() -> None:
    service = InMemorySessionService()
    repo = _BindingRepo()
    phase = RepairPhaseSession(
        id="phs_one", repair_session_id="rs_one", adk_session_id="exact_missing"
    )
    with pytest.raises(StaleInMemoryADKSessionError):
        await DiagnosticADKSessionClient(service).ensure_or_resume(
            phase_session=phase, phase_sessions=repo
        )
    assert repo.calls == 0
    sessions = await service.list_sessions(
        app_name=DIAGNOSTIC_ADK_APP_NAME, user_id=DIAGNOSTIC_ADK_USER_ID
    )
    assert sessions.sessions == []


async def test_concurrent_winning_binding_is_resumed_exactly() -> None:
    service = InMemorySessionService()
    repo = _BindingRepo()
    repo.binding = await DiagnosticADKSessionClient(service).create_session(
        repair_session_id="rs_one", phase=RepairSessionPhase.DIAGNOSTIC
    )
    identity = await DiagnosticADKSessionClient(service).ensure_or_resume(
        phase_session=RepairPhaseSession(id="phs_one", repair_session_id="rs_one"),
        phase_sessions=repo,
    )
    assert identity == repo.binding
