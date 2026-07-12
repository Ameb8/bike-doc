"""Persistence models for versioned bike-profile inference runs."""

from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Integer, Text, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from bike_doc_api.db.base import Base
from bike_doc_api.models._ids import generate_prefixed_ulid


def generate_profile_inference_run_id() -> str:
    """Return an app-owned profile inference-run ID."""

    return generate_prefixed_ulid("pir_")


class ProfileInferenceRun(Base):
    """One idempotent extraction attempt for images in an accepted user turn."""

    __tablename__ = "profile_inference_runs"
    __table_args__ = (
        CheckConstraint("id LIKE 'pir_%'", name="ck_profile_inference_runs_id_prefix"),
        CheckConstraint(
            "status IN ('running', 'completed', 'abstained', 'retryable', 'failed')",
            name="ck_profile_inference_runs_status",
        ),
        Index(
            "ux_profile_inference_runs_turn_schema_extractor",
            "turn_id",
            "inference_schema_version",
            "extractor_version",
            unique=True,
        ),
    )

    id: Mapped[str] = mapped_column(
        Text, primary_key=True, default=generate_profile_inference_run_id
    )
    turn_id: Mapped[str] = mapped_column(
        Text,
        ForeignKey("repair_turns.id", ondelete="RESTRICT"),
        nullable=False,
    )
    repair_session_id: Mapped[str] = mapped_column(
        Text,
        ForeignKey("repair_sessions.id", ondelete="RESTRICT"),
        nullable=False,
    )
    bike_id: Mapped[str] = mapped_column(
        Text,
        ForeignKey("bike_profiles.id", ondelete="RESTRICT"),
        nullable=False,
    )
    inference_schema_version: Mapped[str] = mapped_column(Text, nullable=False)
    extractor_version: Mapped[str] = mapped_column(Text, nullable=False)
    input_artifact_ids: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="running")
    claim_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    failure_code: Mapped[str | None] = mapped_column(Text)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
