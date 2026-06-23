"""Artifact API schemas and mappers."""

from datetime import datetime

from bike_doc_api.models.artifact import ArtifactRef as ArtifactRefModel
from bike_doc_api.schemas.common import (
    APIBaseModel,
    ArtifactMediaType,
    ArtifactPurpose,
    ArtifactStatus,
)


class ArtifactRef(APIBaseModel):
    """Public artifact metadata."""

    id: str
    user_id: str
    repair_session_id: str | None = None
    bike_id: str | None = None
    purpose: ArtifactPurpose
    media_type: ArtifactMediaType
    mime_type: str
    filename: str
    byte_size: int
    width: int | None = None
    height: int | None = None
    duration_seconds: float | None = None
    status: ArtifactStatus
    rejection_reason: str | None = None
    created_at: datetime


def artifact_ref_from_model(artifact: ArtifactRefModel) -> ArtifactRef:
    """Map a persistence artifact to the public schema."""

    return ArtifactRef(
        id=artifact.id,
        user_id=artifact.user_id,
        repair_session_id=artifact.repair_session_id,
        bike_id=artifact.bike_id,
        purpose=ArtifactPurpose(artifact.purpose),
        media_type=ArtifactMediaType(artifact.media_type),
        mime_type=artifact.mime_type,
        filename=artifact.filename,
        byte_size=artifact.byte_size,
        width=artifact.width,
        height=artifact.height,
        duration_seconds=(
            None
            if artifact.duration_seconds is None
            else float(artifact.duration_seconds)
        ),
        status=ArtifactStatus(artifact.status),
        rejection_reason=artifact.rejection_reason,
        created_at=artifact.created_at,
    )
