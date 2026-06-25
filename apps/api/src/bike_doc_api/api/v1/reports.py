"""Phase report read route boundary."""

from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query
from sqlalchemy.ext.asyncio import AsyncSession

from bike_doc_api.api.deps import get_current_user, get_db_session
from bike_doc_api.models.user import User as UserModel
from bike_doc_api.repositories.artifacts import ArtifactRepository
from bike_doc_api.repositories.events import RepairSessionEventRepository
from bike_doc_api.repositories.repair_sessions import (
    RepairPhaseSessionRepository,
    RepairSessionRepository,
)
from bike_doc_api.repositories.reports import PhaseReportRepository
from bike_doc_api.schemas.report import PhaseReportEnvelope, PhaseReportList
from bike_doc_api.services.reports import (
    DEFAULT_REPORT_LIMIT,
    MAX_REPORT_LIMIT,
    ReportService,
)

router = APIRouter(tags=["Reports"])


def get_report_service(
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> ReportService:
    """Build the report service for this request."""

    return ReportService(
        RepairSessionRepository(session),
        RepairPhaseSessionRepository(session),
        PhaseReportRepository(session),
        RepairSessionEventRepository(session),
        ArtifactRepository(session),
        commit=session.commit,
        rollback=session.rollback,
    )


@router.get(
    "/repair-sessions/{sessionId}/reports",
    response_model=PhaseReportList,
)
async def list_repair_session_reports(
    current_user: Annotated[UserModel, Depends(get_current_user)],
    service: Annotated[ReportService, Depends(get_report_service)],
    session_id: Annotated[str, Path(alias="sessionId", min_length=1)],
    limit: Annotated[
        int,
        Query(ge=1, le=MAX_REPORT_LIMIT),
    ] = DEFAULT_REPORT_LIMIT,
    cursor: Annotated[str | None, Query(min_length=1)] = None,
) -> PhaseReportList:
    """List diagnostic report envelopes for an owned repair session."""

    return await service.list_reports(
        current_user=current_user,
        repair_session_id=session_id,
        limit=limit,
        cursor=cursor,
    )


@router.get(
    "/repair-sessions/{sessionId}/reports/{reportId}",
    response_model=PhaseReportEnvelope,
)
async def get_repair_session_report(
    current_user: Annotated[UserModel, Depends(get_current_user)],
    service: Annotated[ReportService, Depends(get_report_service)],
    session_id: Annotated[str, Path(alias="sessionId", min_length=1)],
    report_id: Annotated[str, Path(alias="reportId", min_length=1)],
) -> PhaseReportEnvelope:
    """Return one diagnostic report envelope for an owned repair session."""

    return await service.get_report(
        current_user=current_user,
        repair_session_id=session_id,
        report_id=report_id,
    )
