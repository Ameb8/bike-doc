"""Repair session lifecycle routes."""

from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from bike_doc_api.api.deps import get_current_user, get_db_session
from bike_doc_api.models.user import User as UserModel
from bike_doc_api.repositories.bikes import BikeRepository
from bike_doc_api.repositories.repair_sessions import RepairSessionRepository
from bike_doc_api.schemas.common import RepairSessionStatus
from bike_doc_api.schemas.repair_session import (
    RepairSession,
    RepairSessionCreate,
    RepairSessionList,
)
from bike_doc_api.services.repair_sessions import (
    DEFAULT_REPAIR_SESSION_LIMIT,
    MAX_REPAIR_SESSION_LIMIT,
    RepairSessionService,
)

router = APIRouter(tags=["Repair Sessions"])


def get_repair_session_service(
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> RepairSessionService:
    """Build the repair-session service for this request."""

    return RepairSessionService(
        BikeRepository(session),
        RepairSessionRepository(session),
        rollback=session.rollback,
    )


@router.get(
    "/repair-sessions",
    response_model=RepairSessionList,
)
async def list_repair_sessions(
    current_user: Annotated[UserModel, Depends(get_current_user)],
    service: Annotated[RepairSessionService, Depends(get_repair_session_service)],
    bike_id: Annotated[str, Query(min_length=1)],
    status_filter: Annotated[RepairSessionStatus | None, Query(alias="status")] = None,
    limit: Annotated[int, Query(ge=1, le=MAX_REPAIR_SESSION_LIMIT)] = (
        DEFAULT_REPAIR_SESSION_LIMIT
    ),
    cursor: Annotated[str | None, Query(min_length=1)] = None,
) -> RepairSessionList:
    """List owned repair sessions for an owned bike."""

    return await service.list_sessions(
        current_user=current_user,
        bike_id=bike_id,
        status=status_filter,
        limit=limit,
        cursor=cursor,
    )


@router.post(
    "/repair-sessions",
    response_model=RepairSession,
    status_code=status.HTTP_201_CREATED,
)
async def create_repair_session(
    request: RepairSessionCreate,
    current_user: Annotated[UserModel, Depends(get_current_user)],
    service: Annotated[RepairSessionService, Depends(get_repair_session_service)],
) -> RepairSession:
    """Create a diagnostic repair session for an owned bike."""

    return await service.create_session(current_user=current_user, request=request)


@router.get(
    "/repair-sessions/{sessionId}",
    response_model=RepairSession,
)
async def get_repair_session(
    current_user: Annotated[UserModel, Depends(get_current_user)],
    service: Annotated[RepairSessionService, Depends(get_repair_session_service)],
    session_id: Annotated[str, Path(alias="sessionId", min_length=1)],
) -> RepairSession:
    """Return an owned repair session."""

    return await service.get_session(
        current_user=current_user,
        repair_session_id=session_id,
    )
