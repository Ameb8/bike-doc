"""Event persistence models."""

from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, CheckConstraint, ForeignKey, Index, Text, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from bike_doc_api.db.base import Base
from bike_doc_api.models._ids import generate_prefixed_ulid


def generate_repair_session_event_id() -> str:
    """Return an internal app-owned event row ID."""
    return generate_prefixed_ulid("evt_")


class RepairSessionEvent(Base):
    """Persisted product-level repair session event."""

    __tablename__ = "repair_session_events"
    __table_args__ = (
        CheckConstraint(
            "id LIKE 'evt_%'",
            name="ck_repair_session_events_id_prefix",
        ),
        CheckConstraint(
            "sequence >= 1",
            name="ck_repair_session_events_sequence",
        ),
        CheckConstraint(
            "type IN ("
            "'turn.started', "
            "'assistant.delta', "
            "'assistant.message.completed', "
            "'input.requested', "
            "'artifact.referenced', "
            "'phase.report.created', "
            "'phase.transitioned', "
            "'safety.escalated', "
            "'execution.step.updated', "
            "'turn.completed', "
            "'error', "
            "'heartbeat'"
            ")",
            name="ck_repair_session_events_type",
        ),
        CheckConstraint(
            "jsonb_typeof(data) = 'object'",
            name="ck_repair_session_events_data_object",
        ),
        Index(
            "ux_repair_session_events_session_sequence",
            "repair_session_id",
            "sequence",
            unique=True,
        ),
        Index(
            "ix_repair_session_events_turn_sequence",
            "turn_id",
            "sequence",
            postgresql_where=text("turn_id IS NOT NULL"),
        ),
    )

    id: Mapped[str] = mapped_column(
        Text,
        primary_key=True,
        default=generate_repair_session_event_id,
    )
    repair_session_id: Mapped[str] = mapped_column(
        Text,
        ForeignKey(
            "repair_sessions.id",
            ondelete="CASCADE",
            name="fk_repair_session_events_repair_session",
        ),
        nullable=False,
    )
    turn_id: Mapped[str | None] = mapped_column(
        Text,
        ForeignKey(
            "repair_turns.id",
            ondelete="SET NULL",
            name="fk_repair_session_events_turn",
        ),
    )
    sequence: Mapped[int] = mapped_column(BigInteger, nullable=False)
    type: Mapped[str] = mapped_column(Text, nullable=False)
    data: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        nullable=False,
        server_default=text("now()"),
    )
