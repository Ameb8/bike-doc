"""Bike API schemas and mappers."""

from datetime import datetime
from enum import StrEnum
from typing import Any, Self

from pydantic import Field, field_validator, model_validator

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
    """Public resolved ``bike_profile.v2`` projection."""

    id: str
    user_id: str
    display_name: str
    has_repair_sessions: bool
    schema_version: str = "bike_profile.v2"
    profile_revision: int = Field(default=0, ge=0)
    identity: dict[str, Any] = Field(default_factory=dict)
    frame: dict[str, Any] = Field(default_factory=dict)
    brakes: dict[str, Any] = Field(default_factory=dict)
    drivetrain_v2: dict[str, Any] = Field(default_factory=dict)
    rolling_system: dict[str, Any] = Field(default_factory=dict)
    suspension: dict[str, Any] = Field(default_factory=dict)
    cockpit: dict[str, Any] = Field(default_factory=dict)
    seating: dict[str, Any] = Field(default_factory=dict)
    electric_assist: dict[str, Any] = Field(default_factory=dict)
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
    identity: dict[str, Any] | None = None
    frame: dict[str, Any] | None = None
    brakes: dict[str, Any] | None = None
    drivetrain_v2: dict[str, Any] | None = None
    rolling_system: dict[str, Any] | None = None
    suspension: dict[str, Any] | None = None
    cockpit: dict[str, Any] | None = None
    seating: dict[str, Any] | None = None
    electric_assist: dict[str, Any] | None = None

    @field_validator("make", "model", "drivetrain", "wheel_size", "tire_size")
    @classmethod
    def reject_blank_technical_text(cls, value: str | None) -> str | None:
        """Reject empty technical values before they reach the claim ledger."""

        if value is not None and not value.strip():
            raise ValueError("Technical values must not be blank.")
        return value

    @model_validator(mode="after")
    def validate_structured_technical_fields(self) -> Self:
        """Reject unknown or invalid V2 leaves before they reach a route."""

        from bike_doc_api.services.profile_resolution import (
            validate_public_technical_patch,
        )

        validate_public_technical_patch(_structured_groups(self))
        return self


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
    identity: dict[str, Any] | None = None
    frame: dict[str, Any] | None = None
    brakes: dict[str, Any] | None = None
    drivetrain_v2: dict[str, Any] | None = None
    rolling_system: dict[str, Any] | None = None
    suspension: dict[str, Any] | None = None
    cockpit: dict[str, Any] | None = None
    seating: dict[str, Any] | None = None
    electric_assist: dict[str, Any] | None = None

    @field_validator("make", "model", "drivetrain", "wheel_size", "tire_size")
    @classmethod
    def reject_blank_technical_text(cls, value: str | None) -> str | None:
        """Reject empty technical values before they reach the claim ledger."""

        if value is not None and not value.strip():
            raise ValueError("Technical values must not be blank.")
        return value

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

    @model_validator(mode="after")
    def validate_structured_technical_fields(self) -> Self:
        """Reject unknown or invalid V2 leaves before they reach a route."""

        from bike_doc_api.services.profile_resolution import (
            validate_public_technical_patch,
        )

        validate_public_technical_patch(_structured_groups(self))
        return self


class BikeProfileList(APIBaseModel):
    """Bike profile list response."""

    items: list[BikeProfile]
    next_cursor: str | None


def _structured_groups(model: BikeProfileCreate | BikeProfilePatch) -> dict[str, Any]:
    """Return only structured groups that were intentionally supplied."""

    return {
        field_name: getattr(model, field_name)
        for field_name in {
            "identity",
            "frame",
            "brakes",
            "drivetrain_v2",
            "rolling_system",
            "suspension",
            "cockpit",
            "seating",
            "electric_assist",
        }
        if field_name in model.model_fields_set
    }
