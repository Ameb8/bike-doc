"""Bike persistence models."""

from datetime import datetime

from sqlalchemy import CheckConstraint, ForeignKey, Index, Integer, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from bike_doc_api.db.base import Base
from bike_doc_api.models._ids import generate_prefixed_ulid


def generate_bike_id() -> str:
    """Return an app-owned bike profile ID."""
    return generate_prefixed_ulid("bike_")


class BikeProfile(Base):
    """User-owned bike profile."""

    __tablename__ = "bike_profiles"
    __table_args__ = (
        CheckConstraint("id LIKE 'bike_%'", name="ck_bike_profiles_id_prefix"),
        CheckConstraint(
            "length(trim(display_name)) > 0",
            name="ck_bike_profiles_display_name_not_blank",
        ),
        CheckConstraint(
            "model_year IS NULL OR model_year BETWEEN 1880 AND 2100",
            name="ck_bike_profiles_model_year",
        ),
        CheckConstraint(
            "bike_type IN ("
            "'unknown', 'road', 'gravel', 'mountain', 'hybrid', "
            "'commuter', 'cargo', 'ebike', 'other'"
            ")",
            name="ck_bike_profiles_bike_type",
        ),
        CheckConstraint(
            "frame_material IN ("
            "'unknown', 'aluminum', 'steel', 'carbon', 'titanium', 'other'"
            ")",
            name="ck_bike_profiles_frame_material",
        ),
        CheckConstraint(
            "brake_type IN ("
            "'unknown', 'rim', 'mechanical_disc', 'hydraulic_disc', "
            "'coaster', 'other'"
            ")",
            name="ck_bike_profiles_brake_type",
        ),
        Index(
            "ix_bike_profiles_user_created",
            "user_id",
            text("created_at DESC"),
            text("id DESC"),
            postgresql_where=text("deleted_at IS NULL"),
        ),
    )

    id: Mapped[str] = mapped_column(
        Text,
        primary_key=True,
        default=generate_bike_id,
    )
    user_id: Mapped[str] = mapped_column(
        Text,
        ForeignKey("users.id", ondelete="RESTRICT", name="fk_bike_profiles_user"),
        nullable=False,
    )
    display_name: Mapped[str] = mapped_column(Text, nullable=False)
    make: Mapped[str | None] = mapped_column(Text)
    model: Mapped[str | None] = mapped_column(Text)
    model_year: Mapped[int | None] = mapped_column(Integer)
    bike_type: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default="unknown",
        server_default=text("'unknown'"),
    )
    frame_material: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default="unknown",
        server_default=text("'unknown'"),
    )
    drivetrain: Mapped[str | None] = mapped_column(Text)
    brake_type: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default="unknown",
        server_default=text("'unknown'"),
    )
    wheel_size: Mapped[str | None] = mapped_column(Text)
    tire_size: Mapped[str | None] = mapped_column(Text)
    notes: Mapped[str | None] = mapped_column(Text)
    deleted_at: Mapped[datetime | None] = mapped_column()
    created_at: Mapped[datetime] = mapped_column(
        nullable=False,
        server_default=text("now()"),
    )
    updated_at: Mapped[datetime] = mapped_column(
        nullable=False,
        server_default=text("now()"),
    )
