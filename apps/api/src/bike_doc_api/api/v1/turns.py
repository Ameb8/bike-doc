"""User turn acceptance route boundary."""

from typing import Annotated, cast

from fastapi import APIRouter, Depends, Path, status
from sqlalchemy.ext.asyncio import AsyncSession

from bike_doc_api.adk.orchestration import DiagnosticTurnOrchestrator
from bike_doc_api.adk.runner import DiagnosticRunner
from bike_doc_api.adk.sessions import (
    DiagnosticPhaseSessionManager,
    LocalDiagnosticADKSessionClient,
)
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
from bike_doc_api.api.deps import get_current_user, get_db_session, get_storage_provider
from bike_doc_api.core.config import Settings, get_settings
from bike_doc_api.models.user import User as UserModel
from bike_doc_api.providers.storage import StorageProvider
from bike_doc_api.repositories.artifacts import ArtifactRepository
from bike_doc_api.repositories.bikes import BikeRepository
from bike_doc_api.repositories.events import RepairSessionEventRepository
from bike_doc_api.repositories.repair_sessions import (
    RepairPhaseSessionRepository,
    RepairSessionRepository,
    RepairTurnRepository,
)
from bike_doc_api.repositories.reports import PhaseReportRepository
from bike_doc_api.schemas.turn import TurnAccepted, TurnCreate
from bike_doc_api.services.artifacts import ArtifactService
from bike_doc_api.services.events import EventService
from bike_doc_api.services.repair_sessions import RepairSessionService
from bike_doc_api.services.reports import ReportService
from bike_doc_api.services.safety import DiagnosticSafetyService
from bike_doc_api.services.turns import TurnService

router = APIRouter(tags=["Turns and Events"])


def get_turn_service(
    session: Annotated[AsyncSession, Depends(get_db_session)],
    settings: Annotated[Settings, Depends(get_settings)],
    storage: Annotated[StorageProvider, Depends(get_storage_provider)],
) -> TurnService:
    """Build the turn service for this request."""

    repair_sessions = RepairSessionRepository(session)
    phase_sessions = RepairPhaseSessionRepository(session)
    turns = RepairTurnRepository(session)
    events = RepairSessionEventRepository(session)
    artifacts = ArtifactRepository(session)
    phase_session_manager = DiagnosticPhaseSessionManager(
        phase_sessions=phase_sessions,
        adk_sessions=LocalDiagnosticADKSessionClient(),
        commit=session.commit,
        rollback=session.rollback,
    )
    input_request_service = TurnService(
        repair_sessions,
        phase_sessions,
        turns,
        events,
        artifacts,
        commit=session.commit,
        rollback=session.rollback,
        phase_session_manager=phase_session_manager,
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
    orchestrator = DiagnosticTurnOrchestrator(
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
        runner=DiagnosticRunner(),
        get_bike_profile=GetBikeProfileTool(
            cast(BikeProfileServiceProtocol, repair_session_service),
        ),
        lookup_repair_history=LookupRepairHistoryTool(
            cast(RepairHistoryServiceProtocol, repair_session_service),
        ),
        list_diagnostic_artifacts=ListDiagnosticArtifactsTool(artifact_service),
        request_diagnostic_input=RequestDiagnosticInputTool(
            cast(DiagnosticInputRequestServiceProtocol, input_request_service),
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
    return TurnService(
        repair_sessions,
        phase_sessions,
        turns,
        events,
        artifacts,
        commit=session.commit,
        rollback=session.rollback,
        phase_session_manager=phase_session_manager,
        orchestrator=orchestrator,
    )


@router.post(
    "/repair-sessions/{sessionId}/turns",
    response_model=TurnAccepted,
    status_code=status.HTTP_202_ACCEPTED,
)
async def create_repair_session_turn(
    request: TurnCreate,
    current_user: Annotated[UserModel, Depends(get_current_user)],
    service: Annotated[TurnService, Depends(get_turn_service)],
    session_id: Annotated[str, Path(alias="sessionId", min_length=1)],
) -> TurnAccepted:
    """Accept a user turn for diagnostic processing."""

    return await service.accept_turn(
        current_user=current_user,
        repair_session_id=session_id,
        request=request,
    )
