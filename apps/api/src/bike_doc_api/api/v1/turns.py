"""User turn acceptance route boundary."""

from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, Path, status
from sqlalchemy.ext.asyncio import AsyncSession

from bike_doc_api.adk.background import execute_diagnostic_turn_background
from bike_doc_api.adk.sessions import (
    DiagnosticADKSessionClientProtocol,
    DiagnosticPhaseSessionManager,
)
from bike_doc_api.api.deps import (
    get_current_user,
    get_db_session,
    get_diagnostic_adk_session_client,
)
from bike_doc_api.models.user import User as UserModel
from bike_doc_api.repositories.artifacts import ArtifactRepository
from bike_doc_api.repositories.events import RepairSessionEventRepository
from bike_doc_api.repositories.repair_sessions import (
    RepairPhaseSessionRepository,
    RepairSessionRepository,
    RepairTurnRepository,
)
from bike_doc_api.schemas.turn import TurnAccepted, TurnCreate
from bike_doc_api.services.turns import TurnService

router = APIRouter(tags=["Turns and Events"])


def get_turn_service(
    session: Annotated[AsyncSession, Depends(get_db_session)],
    adk_sessions: Annotated[
        DiagnosticADKSessionClientProtocol,
        Depends(get_diagnostic_adk_session_client),
    ],
) -> TurnService:
    """Build the turn service for this request."""

    repair_sessions = RepairSessionRepository(session)
    phase_sessions = RepairPhaseSessionRepository(session)
    turns = RepairTurnRepository(session)
    events = RepairSessionEventRepository(session)
    artifacts = ArtifactRepository(session)
    phase_session_manager = DiagnosticPhaseSessionManager(
        phase_sessions=phase_sessions,
        adk_sessions=adk_sessions,
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
    )


@router.post(
    "/repair-sessions/{sessionId}/turns",
    response_model=TurnAccepted,
    status_code=status.HTTP_202_ACCEPTED,
)
async def create_repair_session_turn(
    request: TurnCreate,
    background_tasks: BackgroundTasks,
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
    if not service.last_acceptance_was_idempotent_replay:
        background_tasks.add_task(
            execute_diagnostic_turn_background,
            current_user.id,
            accepted.repair_session_id,
            accepted.turn_id,
        )
    return accepted
