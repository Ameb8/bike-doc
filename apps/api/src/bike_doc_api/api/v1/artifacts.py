"""Artifact upload and metadata route boundary."""

from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from bike_doc_api.api.deps import (
    get_current_user,
    get_db_session,
    get_storage_provider,
)
from bike_doc_api.core.config import Settings, get_settings
from bike_doc_api.models.user import User as UserModel
from bike_doc_api.providers.storage import StorageProvider
from bike_doc_api.repositories.artifacts import ArtifactRepository
from bike_doc_api.repositories.repair_sessions import RepairSessionRepository
from bike_doc_api.schemas.artifact import ArtifactUploadResponse
from bike_doc_api.schemas.common import ArtifactPurpose
from bike_doc_api.services.artifacts import ArtifactService

router = APIRouter(tags=["Artifacts"])


def get_artifact_service(
    session: Annotated[AsyncSession, Depends(get_db_session)],
    storage: Annotated[StorageProvider, Depends(get_storage_provider)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> ArtifactService:
    """Build the artifact service for this request."""

    return ArtifactService(
        ArtifactRepository(session),
        RepairSessionRepository(session),
        storage,
        max_upload_bytes=settings.artifact_max_upload_bytes,
        commit=session.commit,
        rollback=session.rollback,
    )


@router.post(
    "/artifacts",
    response_model=ArtifactUploadResponse,
    status_code=status.HTTP_201_CREATED,
)
async def upload_artifact(
    current_user: Annotated[UserModel, Depends(get_current_user)],
    service: Annotated[ArtifactService, Depends(get_artifact_service)],
    file: Annotated[UploadFile, File()],
    purpose: Annotated[ArtifactPurpose, Form()],
    repair_session_id: Annotated[str | None, Form()] = None,
    bike_id: Annotated[str | None, Form()] = None,
    client_artifact_id: Annotated[str | None, Form()] = None,
) -> ArtifactUploadResponse:
    """Upload a diagnostic photo through the product artifact boundary."""

    artifact = await service.upload_artifact(
        current_user=current_user,
        file=file,
        purpose=purpose,
        repair_session_id=repair_session_id,
        bike_id=bike_id,
        client_artifact_id=client_artifact_id,
    )
    return ArtifactUploadResponse(artifact=artifact)
