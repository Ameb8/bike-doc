"""Background diagnostic turn execution wiring."""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from typing import cast

from sqlalchemy.ext.asyncio import AsyncSession

from bike_doc_api.adk.agents.diagnostic import create_diagnostic_agent
from bike_doc_api.adk.orchestration import DiagnosticTurnOrchestrator
from bike_doc_api.adk.runner import DiagnosticRunner
from bike_doc_api.adk.tools.artifacts import ListDiagnosticArtifactsTool
from bike_doc_api.adk.tools.bike_profile import (
    BikeProfileServiceProtocol,
    GetBikeProfileTool,
)
from bike_doc_api.adk.tools.input_requests import (
    DiagnosticInputRequestServiceProtocol,
    RequestDiagnosticInputTool,
)
from bike_doc_api.adk.tools.repair_history import (
    LookupRepairHistoryTool,
    RepairHistoryServiceProtocol,
)
from bike_doc_api.adk.tools.reports import (
    DiagnosticReportServiceProtocol,
    SaveDiagnosticReportTool,
)
from bike_doc_api.adk.tools.safety import RaiseSafetyFlagTool, SafetyServiceProtocol
from bike_doc_api.adk.tools.tool_catalog import DiagnosticAgentToolDependencies
from bike_doc_api.api.deps import get_adk_session_service, get_storage_provider
from bike_doc_api.core.config import (
    Settings,
    get_settings,
    validate_diagnostic_runtime_configuration,
)
from bike_doc_api.db.session import get_session_for_database_url
from bike_doc_api.models.repair_session import RepairSession as RepairSessionModel
from bike_doc_api.models.repair_session import RepairTurn as RepairTurnModel
from bike_doc_api.models.user import User as UserModel
from bike_doc_api.repositories.artifacts import ArtifactRepository
from bike_doc_api.repositories.bikes import BikeRepository
from bike_doc_api.repositories.events import RepairSessionEventRepository
from bike_doc_api.repositories.repair_sessions import (
    RepairPhaseSessionRepository,
    RepairSessionRepository,
    RepairTurnRepository,
)
from bike_doc_api.repositories.reports import PhaseReportRepository
from bike_doc_api.repositories.users import UserRepository
from bike_doc_api.schemas.common import RepairSessionStatus
from bike_doc_api.schemas.event import RepairSessionEventType
from bike_doc_api.schemas.repair_session import repair_session_from_model
from bike_doc_api.services.artifacts import ArtifactService
from bike_doc_api.services.events import EventService
from bike_doc_api.services.repair_sessions import RepairSessionService
from bike_doc_api.services.reports import ReportService
from bike_doc_api.services.safety import DiagnosticSafetyService
from bike_doc_api.services.turns import TurnService

logger = logging.getLogger(__name__)


async def execute_diagnostic_turn_background(
    user_id: str,
    repair_session_id: str,
    turn_id: str,
) -> None:
    """Run accepted diagnostic turn orchestration outside the request scope."""

    settings = get_settings()
    try:
        validate_diagnostic_runtime_configuration(settings)
    except Exception:
        logger.exception("diagnostic_background_runtime_configuration_invalid")
        async for session in get_session_for_database_url(settings.database_url):
            await _handle_background_setup_failure(
                session=session,
                user_id=user_id,
                repair_session_id=repair_session_id,
                turn_id=turn_id,
            )
            return

    async for session in get_session_for_database_url(settings.database_url):
        user: UserModel | None = None
        turn: RepairTurnModel | None = None
        repair_session: RepairSessionModel | None = None
        try:
            users = UserRepository(session)
            turns = RepairTurnRepository(session)
            repair_sessions = RepairSessionRepository(session)

            turn = await turns.get(turn_id)
            repair_session = await repair_sessions.get(repair_session_id)
            user = await users.get(user_id)
            if (
                user is None
                or turn is None
                or repair_session is None
                or turn.repair_session_id != repair_session_id
                or repair_session.user_id != user_id
            ):
                await _handle_background_setup_failure(
                    session=session,
                    user_id=user_id,
                    repair_session_id=repair_session_id,
                    turn_id=turn_id if turn is not None else None,
                )
                return

            orchestrator = _build_background_orchestrator(
                session=session,
                settings=settings,
            )
            await orchestrator.process_turn(current_user=user, turn=turn)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception(
                "diagnostic_background_turn_failed",
                extra={
                    "user_id": user_id,
                    "repair_session_id": repair_session_id,
                    "turn_id": turn_id,
                },
            )
            await _handle_background_setup_failure(
                session=session,
                user_id=user_id,
                repair_session_id=repair_session_id,
                turn_id=turn_id if turn is not None else None,
            )
        return


def _build_background_orchestrator(
    *,
    session: AsyncSession,
    settings: Settings,
) -> DiagnosticTurnOrchestrator:
    """Rebuild the ADK orchestration graph around a fresh DB session."""

    repair_sessions = RepairSessionRepository(session)
    phase_sessions = RepairPhaseSessionRepository(session)
    events = RepairSessionEventRepository(session)
    artifacts = ArtifactRepository(session)
    storage = get_storage_provider(settings)

    turn_service = TurnService(
        repair_sessions,
        phase_sessions,
        RepairTurnRepository(session),
        events,
        artifacts,
        commit=session.commit,
        rollback=session.rollback,
    )
    repair_session_service = RepairSessionService(
        BikeRepository(session),
        repair_sessions,
        phase_sessions=phase_sessions,
        rollback=session.rollback,
    )
    artifact_service = ArtifactService(
        artifacts,
        repair_sessions,
        storage,
        max_upload_bytes=settings.artifact_max_upload_bytes,
        commit=session.commit,
        rollback=session.rollback,
    )
    report_service = ReportService(
        repair_sessions,
        phase_sessions,
        PhaseReportRepository(session),
        events,
        artifacts,
        commit=session.commit,
        rollback=session.rollback,
    )
    safety_service = DiagnosticSafetyService(
        repair_sessions,
        events,
        commit=session.commit,
        rollback=session.rollback,
    )
    tool_dependencies = DiagnosticAgentToolDependencies(
        bike_profile_service=cast(BikeProfileServiceProtocol, repair_session_service),
        repair_history_service=cast(
            RepairHistoryServiceProtocol,
            repair_session_service,
        ),
        artifact_service=artifact_service,
        input_request_service=cast(
            DiagnosticInputRequestServiceProtocol,
            turn_service,
        ),
        safety_service=cast(SafetyServiceProtocol, safety_service),
        report_service=cast(DiagnosticReportServiceProtocol, report_service),
    )
    session_service = get_adk_session_service()
    runner = DiagnosticRunner(
        agent=create_diagnostic_agent(tool_dependencies, settings=settings),
        session_service=session_service,
    )
    return DiagnosticTurnOrchestrator(
        phase_sessions=phase_sessions,
        repair_sessions=repair_sessions,
        events=events,
        artifacts=artifacts,
        event_service=EventService(
            events,
            repair_sessions,
            commit=session.commit,
            rollback=session.rollback,
        ),
        runner=runner,
        get_bike_profile=GetBikeProfileTool(
            cast(BikeProfileServiceProtocol, repair_session_service),
        ),
        lookup_repair_history=LookupRepairHistoryTool(
            cast(RepairHistoryServiceProtocol, repair_session_service),
        ),
        list_diagnostic_artifacts=ListDiagnosticArtifactsTool(artifact_service),
        request_diagnostic_input=RequestDiagnosticInputTool(
            cast(DiagnosticInputRequestServiceProtocol, turn_service),
        ),
        raise_safety_flag=RaiseSafetyFlagTool(
            cast(SafetyServiceProtocol, safety_service),
        ),
        save_diagnostic_report=SaveDiagnosticReportTool(
            cast(DiagnosticReportServiceProtocol, report_service),
        ),
        commit=session.commit,
        rollback=session.rollback,
    )


async def _handle_background_setup_failure(
    *,
    session: AsyncSession,
    user_id: str,
    repair_session_id: str,
    turn_id: str | None,
) -> None:
    """Restore public session state and emit safe terminal events when possible."""

    repair_sessions = RepairSessionRepository(session)
    events = RepairSessionEventRepository(session)
    repair_session = await repair_sessions.get_for_update(repair_session_id)
    if repair_session is None:
        return
    if repair_session.user_id != user_id:
        return
    if turn_id is None:
        repair_session.status = RepairSessionStatus.AWAITING_USER.value
        repair_session.updated_at = datetime.now(UTC)
        await session.commit()
        return

    event_service = EventService(
        events,
        repair_sessions,
        commit=session.commit,
        rollback=session.rollback,
    )
    await event_service.append_event(
        repair_session_id=repair_session.id,
        turn_id=turn_id,
        event_type=RepairSessionEventType.ERROR,
        data={
            "code": "diagnostic_processing_error",
            "message": "Diagnostic processing could not be started.",
            "retryable": True,
        },
    )

    repair_session = await repair_sessions.get_for_update(repair_session_id)
    if repair_session is None or repair_session.user_id != user_id:
        return
    repair_session.status = RepairSessionStatus.AWAITING_USER.value
    repair_session.updated_at = datetime.now(UTC)
    await event_service.append_event(
        repair_session_id=repair_session.id,
        turn_id=turn_id,
        event_type=RepairSessionEventType.TURN_COMPLETED,
        data={
            "turn_id": turn_id,
            "session": repair_session_from_model(repair_session).model_dump(
                mode="json",
            ),
        },
    )
