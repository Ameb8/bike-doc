"""Artifact service boundary."""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Awaitable, Callable
from pathlib import PurePosixPath
from typing import Protocol

from bike_doc_api.core.errors import (
    IdempotencyConflictError,
    NotFoundError,
    PayloadTooLargeError,
    ServerError,
    SessionStateConflictError,
    ValidationAppError,
)
from bike_doc_api.models.artifact import (
    ArtifactRef as ArtifactRefModel,
)
from bike_doc_api.models.artifact import (
    generate_artifact_ref_id,
)
from bike_doc_api.models.repair_session import RepairSession as RepairSessionModel
from bike_doc_api.models.user import User
from bike_doc_api.providers.storage import StorageProvider, StoredObject
from bike_doc_api.schemas.artifact import ArtifactRef, artifact_ref_from_model
from bike_doc_api.schemas.common import (
    ArtifactMediaType,
    ArtifactPurpose,
    ArtifactStatus,
)

logger = logging.getLogger(__name__)

ACCEPTED_DIAGNOSTIC_MIME_TYPES = frozenset({"image/jpeg", "image/png", "image/webp"})


class UploadFileProtocol(Protocol):
    """Upload file attributes required by the artifact service."""

    filename: str | None
    content_type: str | None

    async def read(self, size: int = -1) -> bytes:
        """Read uploaded bytes."""


class ArtifactRepositoryProtocol(Protocol):
    """Artifact persistence operations required by uploads."""

    async def add(self, artifact: ArtifactRefModel) -> ArtifactRefModel:
        """Add an artifact reference to the current transaction."""

    async def get_by_client_artifact_id(
        self,
        *,
        user_id: str,
        client_artifact_id: str,
    ) -> ArtifactRefModel | None:
        """Return an artifact by user-scoped idempotency key."""

    async def list_for_repair_session(
        self,
        repair_session_id: str,
        *,
        limit: int = 50,
    ) -> list[ArtifactRefModel]:
        """Return artifacts associated with a repair session."""


class RepairSessionRepositoryProtocol(Protocol):
    """Repair-session lookups required by diagnostic artifact uploads."""

    async def get_owned(
        self,
        *,
        repair_session_id: str,
        user_id: str,
    ) -> RepairSessionModel | None:
        """Return a repair session owned by a user."""


class ArtifactService:
    """Application-owned artifact upload behavior."""

    def __init__(
        self,
        artifacts: ArtifactRepositoryProtocol,
        repair_sessions: RepairSessionRepositoryProtocol,
        storage: StorageProvider,
        *,
        max_upload_bytes: int,
        commit: Callable[[], Awaitable[None]] | None = None,
        rollback: Callable[[], Awaitable[None]] | None = None,
    ) -> None:
        self._artifacts = artifacts
        self._repair_sessions = repair_sessions
        self._storage = storage
        self._max_upload_bytes = max_upload_bytes
        self._commit = commit
        self._rollback = rollback

    async def upload_artifact(
        self,
        *,
        current_user: User,
        file: UploadFileProtocol,
        purpose: ArtifactPurpose,
        repair_session_id: str | None,
        bike_id: str | None,
        client_artifact_id: str | None,
    ) -> ArtifactRef:
        """Upload and persist a diagnostic photo artifact."""

        if purpose != ArtifactPurpose.DIAGNOSTIC_PHOTO:
            raise ValidationAppError("Only diagnostic photos are supported.")
        if repair_session_id is None or not repair_session_id.strip():
            raise ValidationAppError("repair_session_id is required.")
        repair_session_id = repair_session_id.strip()
        if bike_id is not None:
            raise ValidationAppError("bike_id is not accepted for diagnostic photos.")

        content = await file.read(self._max_upload_bytes + 1)
        if len(content) > self._max_upload_bytes:
            raise PayloadTooLargeError()
        if not content:
            raise ValidationAppError("Uploaded file must not be empty.")

        mime_type = _effective_mime_type(
            content=content, content_type=file.content_type
        )
        if mime_type not in ACCEPTED_DIAGNOSTIC_MIME_TYPES:
            raise ValidationAppError("Unsupported diagnostic photo MIME type.")

        repair_session = await self._repair_sessions.get_owned(
            repair_session_id=repair_session_id,
            user_id=current_user.id,
        )
        if repair_session is None:
            raise NotFoundError()

        filename = _normalize_filename(file.filename, mime_type=mime_type)
        content_sha256 = hashlib.sha256(content).hexdigest()
        request_hash = _canonical_upload_request_hash(
            purpose=purpose.value,
            repair_session_id=repair_session_id,
            bike_id=None,
            filename=filename,
            content_sha256=content_sha256,
        )
        normalized_client_artifact_id = _normalize_client_artifact_id(
            client_artifact_id,
        )
        if normalized_client_artifact_id is not None:
            existing = await self._artifacts.get_by_client_artifact_id(
                user_id=current_user.id,
                client_artifact_id=normalized_client_artifact_id,
            )
            if existing is not None:
                if existing.request_hash != request_hash:
                    raise IdempotencyConflictError()
                return artifact_ref_from_model(existing)

        artifact_id = generate_artifact_ref_id()
        stored_object = await self._store_artifact_object(
            artifact_id=artifact_id,
            user_id=current_user.id,
            repair_session_id=repair_session_id,
            content=content,
            mime_type=mime_type,
            content_sha256=content_sha256,
        )
        artifact = ArtifactRefModel(
            id=artifact_id,
            user_id=current_user.id,
            repair_session_id=repair_session_id,
            bike_id=None,
            client_artifact_id=normalized_client_artifact_id,
            request_hash=(
                request_hash if normalized_client_artifact_id is not None else None
            ),
            purpose=ArtifactPurpose.DIAGNOSTIC_PHOTO.value,
            media_type=ArtifactMediaType.IMAGE.value,
            mime_type=mime_type,
            filename=filename,
            byte_size=stored_object.byte_size,
            width=None,
            height=None,
            duration_seconds=None,
            status=ArtifactStatus.READY.value,
            rejection_reason=None,
            content_sha256=stored_object.content_sha256,
            storage_provider=stored_object.provider,
            storage_bucket=stored_object.bucket,
            storage_path=stored_object.path,
        )

        try:
            created = await self._artifacts.add(artifact)
            if self._commit is not None:
                await self._commit()
        except Exception as exc:
            await self._rollback_if_configured()
            _log_orphaned_object(stored_object)
            raise ServerError() from exc

        return artifact_ref_from_model(created)

    async def list_diagnostic_artifacts(
        self,
        *,
        current_user: User,
        repair_session_id: str,
        purpose: ArtifactPurpose = ArtifactPurpose.DIAGNOSTIC_PHOTO,
        limit: int = 50,
    ) -> list[ArtifactRef]:
        """Return diagnostic artifact metadata for an owned diagnostic session."""

        if purpose is not ArtifactPurpose.DIAGNOSTIC_PHOTO:
            raise ValidationAppError("Only diagnostic photos are supported.")
        repair_session = await self._repair_sessions.get_owned(
            repair_session_id=repair_session_id,
            user_id=current_user.id,
        )
        if repair_session is None:
            raise NotFoundError()
        if repair_session.phase != "diagnostic":
            raise SessionStateConflictError()

        artifacts = await self._artifacts.list_for_repair_session(
            repair_session.id,
            limit=limit,
        )
        return [
            artifact_ref_from_model(artifact)
            for artifact in artifacts
            if artifact.user_id == current_user.id
            and artifact.repair_session_id == repair_session.id
            and artifact.purpose == ArtifactPurpose.DIAGNOSTIC_PHOTO.value
        ]

    async def _store_artifact_object(
        self,
        *,
        artifact_id: str,
        user_id: str,
        repair_session_id: str,
        content: bytes,
        mime_type: str,
        content_sha256: str,
    ) -> StoredObject:
        """Store artifact bytes and map provider failures to a generic error."""

        object_name = _object_name(
            user_id=user_id,
            repair_session_id=repair_session_id,
            artifact_id=artifact_id,
            content_sha256=content_sha256,
            mime_type=mime_type,
        )
        try:
            return await self._storage.put_object(
                object_name=object_name,
                content=content,
                content_type=mime_type,
                content_sha256=content_sha256,
            )
        except Exception as exc:
            await self._rollback_if_configured()
            logger.exception("Artifact storage provider failed")
            raise ServerError() from exc

    async def _rollback_if_configured(self) -> None:
        """Rollback the current unit of work when one is configured."""

        if self._rollback is not None:
            await self._rollback()


def _effective_mime_type(*, content: bytes, content_type: str | None) -> str:
    """Return the accepted image MIME type indicated by content or headers."""

    if content.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if content.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if content.startswith(b"RIFF") and content[8:12] == b"WEBP":
        return "image/webp"
    normalized = (content_type or "").split(";", 1)[0].strip().lower()
    return normalized


def _normalize_filename(filename: str | None, *, mime_type: str) -> str:
    """Normalize the public display filename without using it as a storage key."""

    extension = _extension_for_mime_type(mime_type)
    if filename is None:
        return f"upload.{extension}"
    display_name = PurePosixPath(filename.replace("\\", "/").strip()).name
    if not display_name:
        return f"upload.{extension}"
    return display_name[:255]


def _normalize_client_artifact_id(client_artifact_id: str | None) -> str | None:
    """Normalize an optional client idempotency key."""

    if client_artifact_id is None:
        return None
    normalized = client_artifact_id.strip()
    if not normalized:
        raise ValidationAppError("client_artifact_id must not be blank.")
    return normalized


def _canonical_upload_request_hash(
    *,
    purpose: str,
    repair_session_id: str,
    bike_id: str | None,
    filename: str,
    content_sha256: str,
) -> str:
    """Return the canonical semantic request hash for artifact idempotency."""

    canonical = json.dumps(
        {
            "bike_id": bike_id,
            "content_sha256": content_sha256,
            "filename": filename,
            "purpose": purpose,
            "repair_session_id": repair_session_id,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _object_name(
    *,
    user_id: str,
    repair_session_id: str,
    artifact_id: str,
    content_sha256: str,
    mime_type: str,
) -> str:
    """Return the provider object name for an artifact upload."""

    return (
        f"users/{user_id}/repair-sessions/{repair_session_id}/artifacts/"
        f"{artifact_id}/{content_sha256}.{_extension_for_mime_type(mime_type)}"
    )


def _extension_for_mime_type(mime_type: str) -> str:
    """Return the storage extension for an accepted image MIME type."""

    if mime_type == "image/jpeg":
        return "jpg"
    if mime_type == "image/png":
        return "png"
    if mime_type == "image/webp":
        return "webp"
    raise ValidationAppError("Unsupported diagnostic photo MIME type.")


def _log_orphaned_object(stored_object: StoredObject) -> None:
    """Log provider-neutral object metadata for later cleanup."""

    logger.error(
        "Artifact metadata persistence failed after provider storage",
        extra={
            "storage_provider": stored_object.provider,
            "storage_bucket": stored_object.bucket,
            "storage_path": stored_object.path,
        },
    )
