"""Repair session persistence models."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, CheckConstraint, ForeignKey, Index, Text, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from bike_doc_api.db.base import Base
from bike_doc_api.models._ids import generate_prefixed_ulid


def generate_repair_session_id() -> str:
    """Return an app-owned repair session ID."""
    return generate_prefixed_ulid("rs_")


def generate_repair_phase_session_id() -> str:
    """Return an app-owned repair phase session ID."""
    return generate_prefixed_ulid("phs_")


def generate_repair_turn_id() -> str:
    """Return an app-owned repair turn ID."""
    return generate_prefixed_ulid("turn_")


class RepairSession(Base):
    """Application-owned repair workflow state."""

    __tablename__ = "repair_sessions"
    __table_args__ = (
        CheckConstraint("id LIKE 'rs_%'", name="ck_repair_sessions_id_prefix"),
        CheckConstraint(
            "(client_session_id IS NULL AND request_hash IS NULL) "
            "OR (client_session_id IS NOT NULL AND request_hash IS NOT NULL)",
            name="ck_repair_sessions_client_hash_pair",
        ),
        CheckConstraint(
            "phase IN ("
            "'diagnostic', 'planning', 'execution', 'completed', "
            "'shop_referred', 'cancelled'"
            ")",
            name="ck_repair_sessions_phase",
        ),
        CheckConstraint(
            "status IN ("
            "'created', 'running', 'awaiting_user', 'awaiting_decision', "
            "'blocked_safety', 'completed', 'failed', 'cancelled'"
            ")",
            name="ck_repair_sessions_status",
        ),
        CheckConstraint(
            "safety_state IN ('ok', 'caution', 'shop_recommended', 'blocked')",
            name="ck_repair_sessions_safety_state",
        ),
        CheckConstraint(
            "current_input_request IS NULL "
            "OR jsonb_typeof(current_input_request) = 'object'",
            name="ck_repair_sessions_current_input_request_object",
        ),
        CheckConstraint(
            "execution_progress IS NULL OR jsonb_typeof(execution_progress) = 'object'",
            name="ck_repair_sessions_execution_progress_object",
        ),
        CheckConstraint(
            "jsonb_typeof(active_safety_flags) = 'array'",
            name="ck_repair_sessions_active_safety_flags_array",
        ),
        CheckConstraint(
            "latest_event_sequence >= 0",
            name="ck_repair_sessions_latest_event_sequence",
        ),
        Index(
            "ux_repair_sessions_user_client_session",
            "user_id",
            "client_session_id",
            unique=True,
            postgresql_where=text("client_session_id IS NOT NULL"),
        ),
        Index(
            "ix_repair_sessions_user_created",
            "user_id",
            text("created_at DESC"),
            text("id DESC"),
        ),
        Index(
            "ix_repair_sessions_user_status_created",
            "user_id",
            "status",
            text("created_at DESC"),
            text("id DESC"),
        ),
        Index(
            "ix_repair_sessions_bike_created",
            "bike_id",
            text("created_at DESC"),
            text("id DESC"),
        ),
        Index(
            "ix_repair_sessions_active_safety_flags_gin",
            "active_safety_flags",
            postgresql_using="gin",
        ),
    )

    id: Mapped[str] = mapped_column(
        Text,
        primary_key=True,
        default=generate_repair_session_id,
    )
    user_id: Mapped[str] = mapped_column(
        Text,
        ForeignKey("users.id", ondelete="RESTRICT", name="fk_repair_sessions_user"),
        nullable=False,
    )
    bike_id: Mapped[str] = mapped_column(
        Text,
        ForeignKey(
            "bike_profiles.id",
            ondelete="RESTRICT",
            name="fk_repair_sessions_bike",
        ),
        nullable=False,
    )
    client_session_id: Mapped[str | None] = mapped_column(Text)
    request_hash: Mapped[str | None] = mapped_column(Text)
    phase: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default="diagnostic",
        server_default=text("'diagnostic'"),
    )
    status: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default="created",
        server_default=text("'created'"),
    )
    safety_state: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default="ok",
        server_default=text("'ok'"),
    )
    current_input_request: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB(none_as_null=True),
    )
    execution_progress: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB(none_as_null=True),
    )
    active_safety_flags: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB,
        nullable=False,
        default=list,
        server_default=text("'[]'::jsonb"),
    )
    latest_event_sequence: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
        default=0,
        server_default=text("0"),
    )
    diagnostic_report_id: Mapped[str | None] = mapped_column(
        Text,
        ForeignKey(
            "phase_reports.id",
            ondelete="SET NULL",
            use_alter=True,
            name="fk_repair_sessions_diagnostic_report",
            deferrable=True,
            initially="DEFERRED",
        ),
    )
    plan_report_id: Mapped[str | None] = mapped_column(
        Text,
        ForeignKey(
            "phase_reports.id",
            ondelete="SET NULL",
            use_alter=True,
            name="fk_repair_sessions_plan_report",
            deferrable=True,
            initially="DEFERRED",
        ),
    )
    execution_report_id: Mapped[str | None] = mapped_column(
        Text,
        ForeignKey(
            "phase_reports.id",
            ondelete="SET NULL",
            use_alter=True,
            name="fk_repair_sessions_execution_report",
            deferrable=True,
            initially="DEFERRED",
        ),
    )
    shop_referral_report_id: Mapped[str | None] = mapped_column(
        Text,
        ForeignKey(
            "phase_reports.id",
            ondelete="SET NULL",
            use_alter=True,
            name="fk_repair_sessions_shop_referral_report",
            deferrable=True,
            initially="DEFERRED",
        ),
    )
    created_at: Mapped[datetime] = mapped_column(
        nullable=False,
        server_default=text("now()"),
    )
    updated_at: Mapped[datetime] = mapped_column(
        nullable=False,
        server_default=text("now()"),
    )


class RepairPhaseSession(Base):
    """Mapping from an app repair session phase to an opaque ADK session."""

    __tablename__ = "repair_phase_sessions"
    __table_args__ = (
        CheckConstraint(
            "id LIKE 'phs_%'",
            name="ck_repair_phase_sessions_id_prefix",
        ),
        CheckConstraint(
            "phase IN ('diagnostic', 'planning', 'execution')",
            name="ck_repair_phase_sessions_phase",
        ),
        CheckConstraint(
            "status IN ('active', 'closed')",
            name="ck_repair_phase_sessions_status",
        ),
        CheckConstraint(
            "(status = 'active' AND closed_at IS NULL) "
            "OR (status = 'closed' AND closed_at IS NOT NULL)",
            name="ck_repair_phase_sessions_closed_at",
        ),
        CheckConstraint(
            "length(trim(adk_session_id)) > 0",
            name="ck_repair_phase_sessions_adk_session_id_not_blank",
        ),
        Index(
            "ux_repair_phase_sessions_session_phase",
            "repair_session_id",
            "phase",
            unique=True,
        ),
        Index(
            "ux_repair_phase_sessions_adk_session",
            "adk_session_id",
            unique=True,
        ),
    )

    id: Mapped[str] = mapped_column(
        Text,
        primary_key=True,
        default=generate_repair_phase_session_id,
    )
    repair_session_id: Mapped[str] = mapped_column(
        Text,
        ForeignKey(
            "repair_sessions.id",
            ondelete="CASCADE",
            name="fk_repair_phase_sessions_repair_session",
        ),
        nullable=False,
    )
    phase: Mapped[str] = mapped_column(Text, nullable=False)
    adk_session_id: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default="active",
        server_default=text("'active'"),
    )
    created_at: Mapped[datetime] = mapped_column(
        nullable=False,
        server_default=text("now()"),
    )
    closed_at: Mapped[datetime | None] = mapped_column()


class RepairTurn(Base):
    """Accepted user turn submitted to a repair session."""

    __tablename__ = "repair_turns"
    __table_args__ = (
        CheckConstraint("id LIKE 'turn_%'", name="ck_repair_turns_id_prefix"),
        CheckConstraint(
            "length(trim(client_turn_id)) > 0",
            name="ck_repair_turns_client_turn_id_not_blank",
        ),
        CheckConstraint(
            "schema_version = 'ai_turn.v1'",
            name="ck_repair_turns_schema_version",
        ),
        CheckConstraint(
            "phase IN ('diagnostic', 'planning', 'execution')",
            name="ck_repair_turns_phase",
        ),
        CheckConstraint(
            "jsonb_typeof(message) = 'object'",
            name="ck_repair_turns_message_object",
        ),
        CheckConstraint(
            "start_event_sequence >= 1",
            name="ck_repair_turns_start_event_sequence",
        ),
        Index(
            "ux_repair_turns_session_client_turn",
            "repair_session_id",
            "client_turn_id",
            unique=True,
        ),
        Index(
            "ux_repair_turns_session_start_event",
            "repair_session_id",
            "start_event_sequence",
            unique=True,
        ),
        Index(
            "ix_repair_turns_session_created",
            "repair_session_id",
            text("created_at ASC"),
            text("id ASC"),
        ),
        Index("ix_repair_turns_phase_session", "repair_phase_session_id"),
    )

    id: Mapped[str] = mapped_column(
        Text,
        primary_key=True,
        default=generate_repair_turn_id,
    )
    repair_session_id: Mapped[str] = mapped_column(
        Text,
        ForeignKey(
            "repair_sessions.id",
            ondelete="CASCADE",
            name="fk_repair_turns_repair_session",
        ),
        nullable=False,
    )
    repair_phase_session_id: Mapped[str] = mapped_column(
        Text,
        ForeignKey(
            "repair_phase_sessions.id",
            ondelete="CASCADE",
            name="fk_repair_turns_repair_phase_session",
        ),
        nullable=False,
    )
    client_turn_id: Mapped[str] = mapped_column(Text, nullable=False)
    request_hash: Mapped[str] = mapped_column(Text, nullable=False)
    schema_version: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default="ai_turn.v1",
        server_default=text("'ai_turn.v1'"),
    )
    phase: Mapped[str] = mapped_column(Text, nullable=False)
    message: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    responds_to_input_request_id: Mapped[str | None] = mapped_column(Text)
    start_event_sequence: Mapped[int] = mapped_column(BigInteger, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        nullable=False,
        server_default=text("now()"),
    )
