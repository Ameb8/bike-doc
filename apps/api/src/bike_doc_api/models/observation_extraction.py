"""Persistence models for diagnostic image observation extraction."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from bike_doc_api.db.base import Base
from bike_doc_api.models._ids import generate_prefixed_ulid


def generate_observation_extraction_run_id() -> str:
    """Return an app-owned observation extraction-run ID."""

    return generate_prefixed_ulid("oer_")


def generate_observation_extraction_attempt_id() -> str:
    """Return an app-owned observation extraction-attempt ID."""

    return generate_prefixed_ulid("oea_")


class ObservationExtractionRun(Base):
    """One durable visual-evidence extraction run for an accepted turn."""

    __tablename__ = "observation_extraction_runs"
    __table_args__ = (
        CheckConstraint(
            "id LIKE 'oer_%'", name="ck_observation_extraction_runs_id_prefix"
        ),
        CheckConstraint(
            "image_analysis_mode IN ('shadow', 'enabled')",
            name="ck_observation_extraction_runs_mode",
        ),
        CheckConstraint(
            "jsonb_typeof(input_artifact_ids) = 'array'",
            name="ck_observation_extraction_runs_input_artifact_ids_array",
        ),
        CheckConstraint(
            "jsonb_array_has_unique_text_values(input_artifact_ids)",
            name="ck_observation_extraction_runs_input_artifact_ids_unique",
        ),
        CheckConstraint(
            "jsonb_typeof(preprocessing_manifest) = 'array'",
            name="ck_observation_extraction_runs_preprocessing_manifest_array",
        ),
        ForeignKeyConstraint(
            ["turn_id", "repair_session_id"],
            ["repair_turns.id", "repair_turns.repair_session_id"],
            ondelete="CASCADE",
            name="fk_observation_extraction_runs_turn_session",
        ),
        CheckConstraint(
            "status IN ('pending', 'completed', 'failed')",
            name="ck_observation_extraction_runs_status",
        ),
        CheckConstraint(
            "validated_output IS NULL OR jsonb_typeof(validated_output) = 'object'",
            name="ck_observation_extraction_runs_validated_output_object",
        ),
        CheckConstraint(
            "failure_metadata IS NULL OR jsonb_typeof(failure_metadata) = 'object'",
            name="ck_observation_extraction_runs_failure_metadata_object",
        ),
        CheckConstraint(
            "failure_metadata IS NULL OR octet_length(failure_metadata::text) <= 4096",
            name="ck_observation_extraction_runs_failure_metadata_size",
        ),
        CheckConstraint(
            "provider_attempt_count >= 0",
            name="ck_observation_extraction_runs_attempt_count",
        ),
        CheckConstraint(
            "provider_latency_ms >= 0",
            name="ck_observation_extraction_runs_latency",
        ),
        CheckConstraint(
            "input_tokens IS NULL OR input_tokens >= 0",
            name="ck_observation_extraction_runs_input_tokens",
        ),
        CheckConstraint(
            "output_tokens IS NULL OR output_tokens >= 0",
            name="ck_observation_extraction_runs_output_tokens",
        ),
        CheckConstraint(
            "provider_cost_microunits IS NULL OR provider_cost_microunits >= 0",
            name="ck_observation_extraction_runs_cost",
        ),
        CheckConstraint(
            "(status = 'pending' AND completed_at IS NULL AND failed_at IS NULL) "
            "OR (status = 'completed' AND completed_at IS NOT NULL "
            "AND failed_at IS NULL) "
            "OR (status = 'failed' AND failed_at IS NOT NULL AND completed_at IS NULL)",
            name="ck_observation_extraction_runs_terminal_timestamps",
        ),
        CheckConstraint(
            "(redacted_at IS NULL AND redaction_reason IS NULL) "
            "OR (redacted_at IS NOT NULL AND redaction_reason IS NOT NULL)",
            name="ck_observation_extraction_runs_redaction_pair",
        ),
        Index("ux_observation_extraction_runs_turn", "turn_id", unique=True),
        Index(
            "ix_observation_extraction_runs_session_usable",
            "repair_session_id",
            text("created_at ASC"),
            text("id ASC"),
            postgresql_where=text("status = 'completed' AND redacted_at IS NULL"),
        ),
    )

    id: Mapped[str] = mapped_column(
        Text, primary_key=True, default=generate_observation_extraction_run_id
    )
    turn_id: Mapped[str] = mapped_column(Text, nullable=False)
    repair_session_id: Mapped[str] = mapped_column(Text, nullable=False)
    image_analysis_mode: Mapped[str] = mapped_column(Text, nullable=False)
    input_artifact_ids: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    preprocessing_version: Mapped[str] = mapped_column(Text, nullable=False)
    extractor_version: Mapped[str] = mapped_column(Text, nullable=False)
    prompt_version: Mapped[str] = mapped_column(Text, nullable=False)
    output_schema_version: Mapped[str] = mapped_column(Text, nullable=False)
    provider: Mapped[str] = mapped_column(Text, nullable=False)
    model: Mapped[str] = mapped_column(Text, nullable=False)
    preprocessing_manifest: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    status: Mapped[str] = mapped_column(
        Text, nullable=False, default="pending", server_default=text("'pending'")
    )
    validated_output: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB(none_as_null=True)
    )
    failure_metadata: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB(none_as_null=True)
    )
    provider_attempt_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    provider_latency_ms: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, server_default=text("0")
    )
    input_tokens: Mapped[int | None] = mapped_column(BigInteger)
    output_tokens: Mapped[int | None] = mapped_column(BigInteger)
    provider_cost_microunits: Mapped[int | None] = mapped_column(BigInteger)
    diagnostic_agent_started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    redacted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    redaction_reason: Mapped[str | None] = mapped_column(Text)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    failed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )


class ObservationExtractionAttempt(Base):
    """One ordered provider invocation within an observation extraction run."""

    __tablename__ = "observation_extraction_attempts"
    __table_args__ = (
        CheckConstraint(
            "id LIKE 'oea_%'", name="ck_observation_extraction_attempts_id_prefix"
        ),
        CheckConstraint(
            "attempt_number >= 1", name="ck_observation_extraction_attempts_number"
        ),
        CheckConstraint(
            "outcome IN ('pending', 'completed', 'failed')",
            name="ck_observation_extraction_attempts_outcome",
        ),
        CheckConstraint(
            "failure_metadata IS NULL OR jsonb_typeof(failure_metadata) = 'object'",
            name="ck_observation_extraction_attempts_failure_metadata_object",
        ),
        CheckConstraint(
            "failure_metadata IS NULL OR octet_length(failure_metadata::text) <= 4096",
            name="ck_observation_extraction_attempts_failure_metadata_size",
        ),
        CheckConstraint(
            "latency_ms IS NULL OR latency_ms >= 0",
            name="ck_observation_extraction_attempts_latency",
        ),
        CheckConstraint(
            "input_tokens IS NULL OR input_tokens >= 0",
            name="ck_observation_extraction_attempts_input_tokens",
        ),
        CheckConstraint(
            "output_tokens IS NULL OR output_tokens >= 0",
            name="ck_observation_extraction_attempts_output_tokens",
        ),
        CheckConstraint(
            "provider_cost_microunits IS NULL OR provider_cost_microunits >= 0",
            name="ck_observation_extraction_attempts_cost",
        ),
        CheckConstraint(
            "(outcome = 'pending' AND completed_at IS NULL) "
            "OR (outcome IN ('completed', 'failed') "
            "AND completed_at IS NOT NULL)",
            name="ck_observation_extraction_attempts_completed_at",
        ),
        Index(
            "ux_observation_extraction_attempts_run_number",
            "run_id",
            "attempt_number",
            unique=True,
        ),
        Index(
            "ix_observation_extraction_attempts_run_order", "run_id", "attempt_number"
        ),
    )

    id: Mapped[str] = mapped_column(
        Text, primary_key=True, default=generate_observation_extraction_attempt_id
    )
    run_id: Mapped[str] = mapped_column(
        Text,
        ForeignKey("observation_extraction_runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False)
    provider: Mapped[str] = mapped_column(Text, nullable=False)
    model: Mapped[str] = mapped_column(Text, nullable=False)
    outcome: Mapped[str] = mapped_column(Text, nullable=False)
    failure_metadata: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB(none_as_null=True)
    )
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    latency_ms: Mapped[int | None] = mapped_column(BigInteger)
    input_tokens: Mapped[int | None] = mapped_column(BigInteger)
    output_tokens: Mapped[int | None] = mapped_column(BigInteger)
    provider_cost_microunits: Mapped[int | None] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
