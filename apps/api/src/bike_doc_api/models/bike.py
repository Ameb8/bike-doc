"""Bike persistence models."""

from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from bike_doc_api.db.base import Base
from bike_doc_api.models._ids import generate_prefixed_ulid


def generate_bike_id() -> str:
    """Return an app-owned bike profile ID."""
    return generate_prefixed_ulid("bike_")


def generate_bike_fact_claim_id() -> str:
    """Return an app-owned bike fact-claim ID."""
    return generate_prefixed_ulid("bfc_")


def empty_technical_profile() -> dict[str, Any]:
    """Return a fresh empty V2 technical projection."""

    return {
        "schema_version": "bike_profile.v2",
        "identity": {},
        "frame": {},
        "brakes": {"front": {}, "rear": {}},
        "drivetrain": {},
        "rolling_system": {"front": {}, "rear": {}},
        "suspension": {},
        "cockpit": {},
        "seating": {},
        "electric_assist": {},
    }


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
        CheckConstraint(
            "jsonb_typeof(technical_profile) = 'object' "
            "AND technical_profile->>'schema_version' = 'bike_profile.v2' "
            "AND technical_profile ?& ARRAY["
            "'identity', 'frame', 'brakes', 'drivetrain', 'rolling_system', "
            "'suspension', 'cockpit', 'seating', 'electric_assist'"
            "]",
            name="ck_bike_profiles_technical_profile_v2",
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
    profile_revision: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
        default=0,
        server_default=text("0"),
    )
    technical_profile: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        default=empty_technical_profile,
        server_default=text(
            '\'{"schema_version":"bike_profile.v2","identity":{},"frame":{},"brakes":{"front":{},"rear":{}},'
            '"drivetrain":{},"rolling_system":{"front":{},"rear":{}},'
            '"suspension":{},"cockpit":{},"seating":{},"electric_assist":{}}\'::jsonb',
        ),
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
    )


class BikeFactClaim(Base):
    """Immutable provenance for one technical bike-profile assertion."""

    __tablename__ = "bike_fact_claims"
    __table_args__ = (
        CheckConstraint("id LIKE 'bfc_%'", name="ck_bike_fact_claims_id_prefix"),
        CheckConstraint(
            "source_type IN ("
            "'manual_profile_edit', 'manual_profile_clear', 'image_inference', "
            "'legacy_profile_migration', 'derived_resolution'"
            ")",
            name="ck_bike_fact_claims_source_type",
        ),
        CheckConstraint(
            "disposition IN ('pending', 'applied', 'supporting', 'conflict', "
            "'superseded', 'rejected')",
            name="ck_bike_fact_claims_disposition",
        ),
        CheckConstraint(
            "(source_type = 'manual_profile_clear') = (value IS NULL)",
            name="ck_bike_fact_claims_clear_value",
        ),
        Index("ix_bike_fact_claims_bike_field", "bike_id", "field_path", "created_at"),
    )

    id: Mapped[str] = mapped_column(
        Text, primary_key=True, default=generate_bike_fact_claim_id
    )
    bike_id: Mapped[str] = mapped_column(
        Text,
        ForeignKey(
            "bike_profiles.id", ondelete="RESTRICT", name="fk_bike_fact_claims_bike"
        ),
        nullable=False,
    )
    field_path: Mapped[str] = mapped_column(Text, nullable=False)
    value: Mapped[Any | None] = mapped_column(JSONB)
    source_type: Mapped[str] = mapped_column(Text, nullable=False)
    source_ref: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict
    )
    evidence_refs: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB,
        nullable=False,
        default=list,
        server_default=text("'[]'::jsonb"),
    )
    scope_assumption: Mapped[str | None] = mapped_column(Text)
    observed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    evidence_basis: Mapped[str | None] = mapped_column(Text)
    visibility: Mapped[str | None] = mapped_column(Text)
    model_score: Mapped[float | None] = mapped_column()
    evidence_cues: Mapped[list[str] | None] = mapped_column(JSONB)
    disposition: Mapped[str] = mapped_column(Text, nullable=False, default="pending")
    disposition_reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
    )


class BikeFieldResolution(Base):
    """Current selected value and provenance metadata for one canonical field."""

    __tablename__ = "bike_field_resolutions"
    __table_args__ = (
        CheckConstraint(
            "resolution_state IN ('unknown', 'resolved', 'disputed', 'cleared')",
            name="ck_bike_field_resolutions_state",
        ),
        CheckConstraint(
            "effective_confidence IN ('unknown', 'low', 'medium', 'high')",
            name="ck_bike_field_resolutions_confidence",
        ),
    )

    bike_id: Mapped[str] = mapped_column(
        Text,
        ForeignKey(
            "bike_profiles.id",
            ondelete="RESTRICT",
            name="fk_bike_field_resolutions_bike",
        ),
        primary_key=True,
    )
    field_path: Mapped[str] = mapped_column(Text, primary_key=True)
    current_value: Mapped[Any | None] = mapped_column(JSONB)
    resolution_state: Mapped[str] = mapped_column(Text, nullable=False)
    current_claim_id: Mapped[str | None] = mapped_column(
        Text,
        ForeignKey(
            "bike_fact_claims.id",
            ondelete="RESTRICT",
            name="fk_bike_field_resolutions_claim",
        ),
    )
    supporting_claim_ids: Mapped[list[str]] = mapped_column(
        JSONB,
        nullable=False,
        default=list,
        server_default=text("'[]'::jsonb"),
    )
    conflicting_claim_ids: Mapped[list[str]] = mapped_column(
        JSONB,
        nullable=False,
        default=list,
        server_default=text("'[]'::jsonb"),
    )
    effective_confidence: Mapped[str] = mapped_column(
        Text, nullable=False, default="unknown"
    )
    source_type: Mapped[str | None] = mapped_column(Text)
    observed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    manual_clear_barrier_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
