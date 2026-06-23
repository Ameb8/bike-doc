"""Artifact persistence models."""

from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    Text,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from bike_doc_api.db.base import Base
from bike_doc_api.models._ids import generate_prefixed_ulid


def generate_artifact_ref_id() -> str:
    """Return an app-owned artifact reference ID."""
    return generate_prefixed_ulid("art_")


class ArtifactRef(Base):
    """Metadata and storage reference for an uploaded artifact."""

    __tablename__ = "artifact_refs"
    __table_args__ = (
        CheckConstraint("id LIKE 'art_%'", name="ck_artifact_refs_id_prefix"),
        CheckConstraint(
            "(client_artifact_id IS NULL AND request_hash IS NULL) "
            "OR (client_artifact_id IS NOT NULL AND request_hash IS NOT NULL)",
            name="ck_artifact_refs_client_hash_pair",
        ),
        CheckConstraint(
            "purpose IN ("
            "'diagnostic_photo', 'verification_photo', 'bike_profile_photo', "
            "'repair_reference', 'other'"
            ")",
            name="ck_artifact_refs_purpose",
        ),
        CheckConstraint(
            "((purpose IN ('diagnostic_photo', 'verification_photo') "
            "AND repair_session_id IS NOT NULL AND bike_id IS NULL) "
            "OR (purpose = 'bike_profile_photo' "
            "AND bike_id IS NOT NULL AND repair_session_id IS NULL) "
            "OR (purpose IN ('repair_reference', 'other')))",
            name="ck_artifact_refs_parent_by_purpose",
        ),
        CheckConstraint(
            "media_type IN ('image', 'video', 'audio', 'document', 'other')",
            name="ck_artifact_refs_media_type",
        ),
        CheckConstraint("byte_size >= 0", name="ck_artifact_refs_byte_size"),
        CheckConstraint(
            "(width IS NULL OR width > 0) AND (height IS NULL OR height > 0)",
            name="ck_artifact_refs_dimensions",
        ),
        CheckConstraint(
            "duration_seconds IS NULL OR duration_seconds >= 0",
            name="ck_artifact_refs_duration",
        ),
        CheckConstraint(
            "status IN ('uploaded', 'processing', 'ready', 'rejected')",
            name="ck_artifact_refs_status",
        ),
        CheckConstraint(
            "(status = 'rejected' AND rejection_reason IS NOT NULL) "
            "OR (status <> 'rejected')",
            name="ck_artifact_refs_rejection_reason",
        ),
        CheckConstraint(
            "length(trim(filename)) > 0",
            name="ck_artifact_refs_filename_not_blank",
        ),
        CheckConstraint(
            "content_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_artifact_refs_content_sha256",
        ),
        CheckConstraint(
            "length(trim(storage_provider)) > 0",
            name="ck_artifact_refs_storage_provider_not_blank",
        ),
        CheckConstraint(
            "length(trim(storage_path)) > 0",
            name="ck_artifact_refs_storage_path_not_blank",
        ),
        Index(
            "ux_artifact_refs_user_client_artifact",
            "user_id",
            "client_artifact_id",
            unique=True,
            postgresql_where=text("client_artifact_id IS NOT NULL"),
        ),
        Index(
            "ix_artifact_refs_session_created",
            "repair_session_id",
            text("created_at DESC"),
            text("id DESC"),
            postgresql_where=text("repair_session_id IS NOT NULL"),
        ),
        Index(
            "ix_artifact_refs_bike_created",
            "bike_id",
            text("created_at DESC"),
            text("id DESC"),
            postgresql_where=text("bike_id IS NOT NULL"),
        ),
        Index(
            "ix_artifact_refs_user_created",
            "user_id",
            text("created_at DESC"),
            text("id DESC"),
        ),
    )

    id: Mapped[str] = mapped_column(
        Text,
        primary_key=True,
        default=generate_artifact_ref_id,
    )
    user_id: Mapped[str] = mapped_column(
        Text,
        ForeignKey("users.id", ondelete="RESTRICT", name="fk_artifact_refs_user"),
        nullable=False,
    )
    repair_session_id: Mapped[str | None] = mapped_column(
        Text,
        ForeignKey(
            "repair_sessions.id",
            ondelete="SET NULL",
            name="fk_artifact_refs_repair_session",
        ),
    )
    bike_id: Mapped[str | None] = mapped_column(
        Text,
        ForeignKey(
            "bike_profiles.id",
            ondelete="SET NULL",
            name="fk_artifact_refs_bike",
        ),
    )
    client_artifact_id: Mapped[str | None] = mapped_column(Text)
    request_hash: Mapped[str | None] = mapped_column(Text)
    purpose: Mapped[str] = mapped_column(Text, nullable=False)
    media_type: Mapped[str] = mapped_column(Text, nullable=False)
    mime_type: Mapped[str] = mapped_column(Text, nullable=False)
    filename: Mapped[str] = mapped_column(Text, nullable=False)
    byte_size: Mapped[int] = mapped_column(BigInteger, nullable=False)
    width: Mapped[int | None] = mapped_column(Integer)
    height: Mapped[int | None] = mapped_column(Integer)
    duration_seconds: Mapped[Decimal | None] = mapped_column(Numeric(10, 3))
    status: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default="uploaded",
        server_default=text("'uploaded'"),
    )
    rejection_reason: Mapped[str | None] = mapped_column(Text)
    content_sha256: Mapped[str] = mapped_column(Text, nullable=False)
    storage_provider: Mapped[str] = mapped_column(Text, nullable=False)
    storage_bucket: Mapped[str | None] = mapped_column(Text)
    storage_path: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        nullable=False,
        server_default=text("now()"),
    )
    updated_at: Mapped[datetime] = mapped_column(
        nullable=False,
        server_default=text("now()"),
    )
