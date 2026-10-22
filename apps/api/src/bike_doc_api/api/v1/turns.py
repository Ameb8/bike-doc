"""User turn acceptance route boundary."""

from typing import Annotated

from fastapi import APIRouter, Depends, Path, status
from sqlalchemy.ext.asyncio import AsyncSession

from bike_doc_api.adk.sessions import (
    DiagnosticPhaseSessionManager,
)
from bike_doc_api.api.deps import (
    get_current_user,
    get_db_session,
)
from bike_doc_api.api.responses import error_responses
from bike_doc_api.core.config import Settings, get_settings
from bike_doc_api.models.user import User as UserModel
from bike_doc_api.repositories.artifacts import ArtifactRepository
from bike_doc_api.repositories.background_jobs import BackgroundJobRepository
from bike_doc_api.repositories.events import RepairSessionEventRepository
from bike_doc_api.repositories.repair_sessions import (
    RepairPhaseSessionRepository,
    RepairSessionRepository,
    RepairTurnRepository,
)
from bike_doc_api.schemas.turn import TurnAccepted, TurnCreate
from bike_doc_api.services.background_jobs import BackgroundJobService
from bike_doc_api.services.turns import TurnService

router = APIRouter(tags=["Turns and Events"])


def get_turn_service(
    session: Annotated[AsyncSession, Depends(get_db_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> TurnService:
    """Build the turn service for this request."""

    repair_sessions = RepairSessionRepository(session)
    phase_sessions = RepairPhaseSessionRepository(session)
    turns = RepairTurnRepository(session)
    events = RepairSessionEventRepository(session)
    artifacts = ArtifactRepository(session)
    phase_session_manager = DiagnosticPhaseSessionManager(
        phase_sessions=phase_sessions,
        diagnostic_report_version=settings.diagnostic_report_version,
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
        image_analysis_mode=settings.image_analysis_mode,
        background_jobs=BackgroundJobService(BackgroundJobRepository(session)),
        diagnostic_attempt_limit=settings.diagnostic_worker_attempt_limit,
        profile_inference_extractor_version=settings.profile_inference_extractor_version,
        profile_inference_max_attempts=settings.profile_inference_max_attempts,
        diagnostic_report_version=settings.diagnostic_report_version,
    )


@router.post(
    "/repair-sessions/{sessionId}/turns",
    operation_id="createRepairSessionTurn",
    responses=error_responses(401, 404, 409, 422),
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

    accepted = await service.accept_turn(
        current_user=current_user,
        repair_session_id=session_id,
        request=request,
    )
    return accepted
