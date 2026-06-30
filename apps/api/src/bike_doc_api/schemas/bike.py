"""Bike API schemas and mappers."""

from datetime import datetime
from enum import StrEnum
from typing import Self

from pydantic import Field, model_validator

from bike_doc_api.models.bike import BikeProfile as BikeProfileModel
from bike_doc_api.schemas.common import APIBaseModel


class BikeType(StrEnum):
    """Bike type values."""

    UNKNOWN = "unknown"
    ROAD = "road"
    GRAVEL = "gravel"
    MOUNTAIN = "mountain"
    HYBRID = "hybrid"
    COMMUTER = "commuter"
    CARGO = "cargo"
    EBIKE = "ebike"
    OTHER = "other"


class FrameMaterial(StrEnum):
    """Frame material values."""

    UNKNOWN = "unknown"
    ALUMINUM = "aluminum"
    STEEL = "steel"
    CARBON = "carbon"
    TITANIUM = "titanium"
    OTHER = "other"


class BrakeType(StrEnum):
    """Brake type values."""

    UNKNOWN = "unknown"
    RIM = "rim"
    MECHANICAL_DISC = "mechanical_disc"
    HYDRAULIC_DISC = "hydraulic_disc"
    COASTER = "coaster"
    OTHER = "other"


class BikeProfile(APIBaseModel):
    """Public bike profile."""

    id: str
    user_id: str
    display_name: str
    has_repair_sessions: bool
    make: str | None = None
    model: str | None = None
    model_year: int | None = None
    bike_type: BikeType
    frame_material: FrameMaterial | None = None
    drivetrain: str | None = None
    brake_type: BrakeType | None = None
    wheel_size: str | None = None
    tire_size: str | None = None
    notes: str | None = None
    created_at: datetime
    updated_at: datetime


class BikeProfileCreate(APIBaseModel):
    """Bike profile create request."""

    display_name: str = Field(min_length=1)
    make: str | None = None
    model: str | None = None
    model_year: int | None = Field(default=None, ge=1880, le=2100)
    bike_type: BikeType = BikeType.UNKNOWN
    frame_material: FrameMaterial = FrameMaterial.UNKNOWN
    drivetrain: str | None = None
    brake_type: BrakeType = BrakeType.UNKNOWN
    wheel_size: str | None = None
    tire_size: str | None = None
    notes: str | None = None


class BikeProfilePatch(APIBaseModel):
    """Bike profile patch request."""

    display_name: str | None = Field(default=None, min_length=1)
    make: str | None = None
    model: str | None = None
    model_year: int | None = Field(default=None, ge=1880, le=2100)
    bike_type: BikeType | None = None
    frame_material: FrameMaterial | None = None
    drivetrain: str | None = None
    brake_type: BrakeType | None = None
    wheel_size: str | None = None
    tire_size: str | None = None
    notes: str | None = None

    @model_validator(mode="after")
    def reject_nulls_for_non_nullable_fields(self) -> Self:
        """Allow omitted fields but reject explicit nulls for non-nullable fields."""

        for field_name in {
            "display_name",
            "bike_type",
            "frame_material",
            "brake_type",
        }:
            if (
                field_name in self.model_fields_set
                and getattr(self, field_name) is None
            ):
                msg = f"{field_name} may not be null"
                raise ValueError(msg)
        return self


class BikeProfileList(APIBaseModel):
    """Bike profile list response."""

    items: list[BikeProfile]
    next_cursor: str | None


def bike_profile_from_model(
    bike: BikeProfileModel,
    *,
    has_repair_sessions: bool = False,
) -> BikeProfile:
    """Map a persistence bike profile to the public schema."""

    return BikeProfile(
        id=bike.id,
        user_id=bike.user_id,
        display_name=bike.display_name,
        has_repair_sessions=has_repair_sessions,
        make=bike.make,
        model=bike.model,
        model_year=bike.model_year,
        bike_type=BikeType(bike.bike_type),
        frame_material=FrameMaterial(bike.frame_material),
        drivetrain=bike.drivetrain,
        brake_type=BrakeType(bike.brake_type),
        wheel_size=bike.wheel_size,
        tire_size=bike.tire_size,
        notes=bike.notes,
        created_at=bike.created_at,
        updated_at=bike.updated_at,
    )
