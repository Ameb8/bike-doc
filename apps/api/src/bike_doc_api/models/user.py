"""User persistence models."""

from datetime import datetime

from sqlalchemy import CheckConstraint, Index, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from bike_doc_api.db.base import Base
from bike_doc_api.models._ids import generate_prefixed_ulid


def generate_user_id() -> str:
    """Return an app-owned user ID."""
    return generate_prefixed_ulid("usr_")


class User(Base):
    """Application user profile derived from an external auth subject."""

    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint("id LIKE 'usr_%'", name="ck_users_id_prefix"),
        CheckConstraint(
            "skill_level IN ('unknown', 'beginner', 'intermediate', 'advanced')",
            name="ck_users_skill_level",
        ),
        CheckConstraint(
            "length(trim(email)) > 0",
            name="ck_users_email_not_blank",
        ),
        CheckConstraint(
            "length(trim(display_name)) > 0",
            name="ck_users_display_name_not_blank",
        ),
        Index("ux_users_auth_subject", "auth_subject", unique=True),
        Index("ix_users_email", "email"),
    )

    id: Mapped[str] = mapped_column(
        Text,
        primary_key=True,
        default=generate_user_id,
    )
    auth_subject: Mapped[str] = mapped_column(Text, nullable=False)
    email: Mapped[str] = mapped_column(Text, nullable=False)
    display_name: Mapped[str] = mapped_column(Text, nullable=False)
    skill_level: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default="unknown",
        server_default=text("'unknown'"),
    )
    created_at: Mapped[datetime] = mapped_column(
        nullable=False,
        server_default=text("now()"),
    )
