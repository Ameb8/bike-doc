"""Repair session event stream routes."""

from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import APIRouter, Depends, Header, Path, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from bike_doc_api.api.deps import get_current_user, get_db_session
from bike_doc_api.models.user import User as UserModel
from bike_doc_api.repositories.events import RepairSessionEventRepository
from bike_doc_api.repositories.repair_sessions import RepairSessionRepository
from bike_doc_api.services.events import DEFAULT_TIMEOUT_SECONDS, EventService

router = APIRouter(tags=["Turns and Events"])


def get_event_service(
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> EventService:
    """Build the event service for this request."""

    return EventService(
        RepairSessionEventRepository(session),
        RepairSessionRepository(session),
        commit=session.commit,
        rollback=session.rollback,
    )


@router.get("/repair-sessions/{sessionId}/events")
async def stream_repair_session_events(
    current_user: Annotated[UserModel, Depends(get_current_user)],
    service: Annotated[EventService, Depends(get_event_service)],
    session_id: Annotated[str, Path(alias="sessionId", min_length=1)],
    after: Annotated[str | None, Query()] = None,
    timeout_seconds: Annotated[
        int,
        Query(ge=5, le=120),
    ] = DEFAULT_TIMEOUT_SECONDS,
    last_event_id: Annotated[str | None, Header(alias="Last-Event-ID")] = None,
) -> StreamingResponse:
    """Replay persisted events, then stream live repair-session events."""

    stream = await service.prepare_stream(
        current_user=current_user,
        repair_session_id=session_id,
        after=after,
        last_event_id=last_event_id,
        timeout_seconds=timeout_seconds,
    )

    async def frames() -> AsyncIterator[str]:
        async for frame in service.stream_sse_frames(stream):
            yield frame

    return StreamingResponse(frames(), media_type="text/event-stream")
