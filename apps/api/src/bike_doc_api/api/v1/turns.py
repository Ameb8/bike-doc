"""User turn acceptance route boundary."""

from typing import Annotated

from fastapi import APIRouter, Depends, Path, status
from sqlalchemy.ext.asyncio import AsyncSession

from bike_doc_api.api.deps import get_current_user, get_db_session
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
) -> TurnService:
    """Build the turn service for this request."""

    return TurnService(
        RepairSessionRepository(session),
        RepairPhaseSessionRepository(session),
        RepairTurnRepository(session),
        RepairSessionEventRepository(session),
        ArtifactRepository(session),
        commit=session.commit,
        rollback=session.rollback,
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
