"""Phase report persistence models."""

from datetime import datetime
from typing import Any

from sqlalchemy import CheckConstraint, ForeignKey, Index, Text, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from bike_doc_api.db.base import Base
from bike_doc_api.models._ids import generate_prefixed_ulid


def generate_phase_report_id() -> str:
    """Return an app-owned phase report ID."""
    return generate_prefixed_ulid("rpt_")


class PhaseReport(Base):
    """Structured phase report envelope."""

    __tablename__ = "phase_reports"
    __table_args__ = (
        CheckConstraint("id LIKE 'rpt_%'", name="ck_phase_reports_id_prefix"),
        CheckConstraint(
            "type IN ('diagnostic', 'plan', 'execution', 'shop_referral')",
            name="ck_phase_reports_type",
        ),
        CheckConstraint(
            "phase IN ("
            "'diagnostic', 'planning', 'execution', 'completed', "
            "'shop_referred', 'cancelled'"
            ")",
            name="ck_phase_reports_phase",
        ),
        CheckConstraint(
            "schema_version IN ("
            "'diagnostic_report.v1', 'plan_report.v1', "
            "'execution_report.v1', 'shop_referral_report.v1'"
            ")",
            name="ck_phase_reports_schema_version",
        ),
        CheckConstraint(
            "(type = 'diagnostic' AND schema_version = 'diagnostic_report.v1') "
            "OR (type = 'plan' AND schema_version = 'plan_report.v1') "
            "OR (type = 'execution' AND schema_version = 'execution_report.v1') "
            "OR (type = 'shop_referral' "
            "AND schema_version = 'shop_referral_report.v1')",
            name="ck_phase_reports_type_schema_version_pair",
        ),
        CheckConstraint(
            "length(trim(summary)) > 0",
            name="ck_phase_reports_summary_not_blank",
        ),
        CheckConstraint(
            "jsonb_typeof(safety_flags) = 'array'",
            name="ck_phase_reports_safety_flags_array",
        ),
        CheckConstraint(
            "jsonb_typeof(source_artifact_ids) = 'array'",
            name="ck_phase_reports_source_artifact_ids_array",
        ),
        CheckConstraint(
            "jsonb_typeof(payload) = 'object'",
            name="ck_phase_reports_payload_object",
        ),
        Index(
            "ix_phase_reports_session_created",
            "repair_session_id",
            text("created_at DESC"),
            text("id DESC"),
        ),
        Index(
            "ix_phase_reports_session_type_created",
            "repair_session_id",
            "type",
            text("created_at DESC"),
            text("id DESC"),
        ),
        Index(
            "ix_phase_reports_phase_session_created",
            "repair_phase_session_id",
            text("created_at DESC"),
            text("id DESC"),
            postgresql_where=text("repair_phase_session_id IS NOT NULL"),
        ),
    )

    id: Mapped[str] = mapped_column(
        Text,
        primary_key=True,
        default=generate_phase_report_id,
    )
    repair_session_id: Mapped[str] = mapped_column(
        Text,
        ForeignKey(
            "repair_sessions.id",
            ondelete="CASCADE",
            name="fk_phase_reports_repair_session",
        ),
        nullable=False,
    )
    repair_phase_session_id: Mapped[str | None] = mapped_column(
        Text,
        ForeignKey(
            "repair_phase_sessions.id",
            ondelete="SET NULL",
            name="fk_phase_reports_repair_phase_session",
        ),
    )
    type: Mapped[str] = mapped_column(Text, nullable=False)
    schema_version: Mapped[str] = mapped_column(Text, nullable=False)
    phase: Mapped[str] = mapped_column(Text, nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    safety_flags: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB,
        nullable=False,
        default=list,
        server_default=text("'[]'::jsonb"),
    )
    source_artifact_ids: Mapped[list[str]] = mapped_column(
        JSONB,
        nullable=False,
        default=list,
        server_default=text("'[]'::jsonb"),
    )
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        nullable=False,
        server_default=text("now()"),
    )
