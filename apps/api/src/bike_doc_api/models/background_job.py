"""Authoritative background work and bounded publication/execution metadata."""

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    Index,
    Integer,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from bike_doc_api.db.base import Base
from bike_doc_api.models._ids import generate_prefixed_ulid


class BackgroundJob(Base):
    """One logical accepted instruction, independent of broker delivery history."""

    __tablename__ = "background_jobs"
    __table_args__ = (
        UniqueConstraint(
            "job_kind", "deduplication_key", name="uq_background_jobs_identity"
        ),
        CheckConstraint(
            "id ~ '^job_[0-7][0-9A-HJKMNP-TV-Z]{25}$'", name="ck_background_jobs_id"
        ),
        CheckConstraint(
            "job_kind ~ '^[a-z][a-z0-9_]{0,63}$' AND workload_class ~ "
            "'^[a-z][a-z0-9_]{0,63}$' AND input_version BETWEEN 1 AND 32767 "
            "AND length(deduplication_key) BETWEEN 1 AND 256",
            name="ck_background_jobs_definition",
        ),
        CheckConstraint(
            "jsonb_typeof(input) = 'object' AND octet_length(input::text) <= 4096",
            name="ck_background_jobs_input",
        ),
        CheckConstraint(
            "state IN ('queued','running','retrying','succeeded','interrupted','dead')",
            name="ck_background_jobs_state",
        ),
        CheckConstraint(
            "attempt_count >= 0 AND attempt_count <= attempt_limit AND "
            "attempt_limit BETWEEN 1 AND 100",
            name="ck_background_jobs_attempts",
        ),
        CheckConstraint(
            "desired_generation >= 1 AND confirmed_generation >= 0 AND "
            "confirmed_generation <= desired_generation",
            name="ck_background_jobs_generations",
        ),
        CheckConstraint(
            "(publication_claim_token IS NULL AND publication_claim_generation "
            "IS NULL AND publication_claim_until IS NULL) OR "
            "(publication_claim_token IS NOT NULL AND "
            "publication_claim_generation IS NOT NULL AND "
            "publication_claim_generation BETWEEN 1 AND desired_generation AND "
            "publication_claim_until IS NOT NULL)",
            name="ck_background_jobs_publication_claim",
        ),
        CheckConstraint(
            "(confirmed_generation = 0) = (publication_confirmed_at IS NULL)",
            name="ck_background_jobs_confirmation",
        ),
        CheckConstraint(
            "(state IN ('succeeded','interrupted','dead')) = (terminal_at IS NOT NULL)",
            name="ck_background_jobs_terminal",
        ),
        CheckConstraint(
            "(state = 'running') = (execution_token IS NOT NULL) AND "
            "(execution_token IS NULL) = (execution_started_at IS NULL) AND "
            "(execution_token IS NULL) = (execution_deadline IS NULL)",
            name="ck_background_jobs_execution",
        ),
        CheckConstraint(
            "execution_token IS NULL OR (attempt_count > 0 AND "
            "first_started_at IS NOT NULL AND execution_deadline > "
            "execution_started_at AND execution_started_at >= "
            "first_started_at)",
            name="ck_background_jobs_deadline",
        ),
        CheckConstraint(
            "(attempt_count = 0) = (first_started_at IS NULL)",
            name="ck_background_jobs_first_start",
        ),
        CheckConstraint(
            "updated_at >= created_at AND eligible_at >= created_at AND "
            "publication_eligible_at >= created_at AND (first_started_at IS "
            "NULL OR first_started_at >= created_at) AND (terminal_at IS NULL "
            "OR terminal_at >= COALESCE(first_started_at, created_at)) AND "
            "(effect_boundary_at IS NULL OR (first_started_at IS NOT NULL AND "
            "effect_boundary_at >= first_started_at))",
            name="ck_background_jobs_times",
        ),
        CheckConstraint(
            "latest_error_category IS NULL OR latest_error_category IN "
            "('definition_unknown','version_unsupported','input_invalid','attempts_exhausted','provider_unavailable','execution_timeout','execution_lost','permanent_failure')",
            name="ck_background_jobs_error",
        ),
        CheckConstraint(
            "transport_error IS NULL OR transport_error IN "
            "('unavailable','timeout','rejected')",
            name="ck_background_jobs_transport_error",
        ),
        Index(
            "ix_background_jobs_publication_due",
            "publication_eligible_at",
            "id",
            postgresql_where=text("desired_generation > confirmed_generation"),
        ),
        Index(
            "ix_background_jobs_eligible",
            "eligible_at",
            "id",
            postgresql_where=text("state IN ('queued','retrying')"),
        ),
        Index(
            "ix_background_jobs_deadline",
            "execution_deadline",
            "id",
            postgresql_where=text("state = 'running'"),
        ),
        Index(
            "ix_background_jobs_retention",
            "terminal_at",
            "id",
            postgresql_where=text(
                "terminal_at IS NOT NULL AND desired_generation = confirmed_generation"
            ),
        ),
    )

    id: Mapped[str] = mapped_column(
        String(30), primary_key=True, default=lambda: generate_prefixed_ulid("job_")
    )
    job_kind: Mapped[str] = mapped_column(String(64))
    workload_class: Mapped[str] = mapped_column(String(64))
    input_version: Mapped[int] = mapped_column(Integer)
    input: Mapped[dict[str, object]] = mapped_column(JSONB)
    deduplication_key: Mapped[str] = mapped_column(String(256))
    state: Mapped[str] = mapped_column(String(16), server_default="queued")
    eligible_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
    attempt_count: Mapped[int] = mapped_column(Integer, server_default="0")
    attempt_limit: Mapped[int] = mapped_column(Integer)
    desired_generation: Mapped[int] = mapped_column(BigInteger, server_default="1")
    confirmed_generation: Mapped[int] = mapped_column(BigInteger, server_default="0")
    publication_eligible_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
    publication_claim_token: Mapped[str | None] = mapped_column(String(32))
    publication_claim_generation: Mapped[int | None] = mapped_column(BigInteger)
    publication_claim_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    publication_confirmed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    transport_error: Mapped[str | None] = mapped_column(String(32))
    execution_started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    execution_deadline: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    execution_token: Mapped[str | None] = mapped_column(String(32))
    effect_boundary_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    first_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    terminal_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    latest_error_category: Mapped[str | None] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
