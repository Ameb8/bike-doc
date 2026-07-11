"""Canonical bike-profile claim mapping and resolution primitives."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class LegacyBikeProfileValues:
    """Values read from the deprecated V1 bike-profile columns."""

    make: str | None = None
    model: str | None = None
    model_year: int | None = None
    bike_type: str | None = None
    frame_material: str | None = None
    drivetrain: str | None = None
    brake_type: str | None = None
    wheel_size: str | None = None
    tire_size: str | None = None


@dataclass(frozen=True, slots=True)
class NewBikeFactClaim:
    """An unpersisted canonical fact claim produced by a profile operation."""

    field_path: str
    value: Any
    source_type: str
    scope_assumption: str | None = None


LEGACY_FIELD_PATHS: dict[str, tuple[str, ...]] = {
    "make": ("identity.make",),
    "model": ("identity.model",),
    "model_year": ("identity.model_year",),
    "bike_type": ("identity.bike_type",),
    "frame_material": ("frame.material",),
    "drivetrain": ("drivetrain.legacy_description",),
    "brake_type": (
        "brakes.front.mechanism",
        "brakes.front.actuation",
        "brakes.rear.mechanism",
        "brakes.rear.actuation",
        "brakes.legacy_summary",
    ),
    "wheel_size": (
        "rolling_system.front.wheel.nominal_size",
        "rolling_system.rear.wheel.nominal_size",
    ),
    "tire_size": (
        "rolling_system.front.tire.marked_size",
        "rolling_system.rear.tire.marked_size",
    ),
}


def manual_legacy_field_claims(
    field_name: str,
    value: str | int | None,
) -> list[NewBikeFactClaim]:
    """Convert one compatibility PATCH field into manual claims or clears."""

    paths = LEGACY_FIELD_PATHS[field_name]
    if value is None or value == "unknown":
        return [
            NewBikeFactClaim(
                field_path=field_path,
                value=None,
                source_type="manual_profile_clear",
            )
            for field_path in paths
        ]
    values = _legacy_values_for_field(field_name, value)
    return legacy_profile_claims(values, source_type="manual_profile_edit")


def _legacy_values_for_field(
    field_name: str,
    value: str | int,
) -> LegacyBikeProfileValues:
    if field_name == "model_year":
        assert isinstance(value, int)
        return LegacyBikeProfileValues(model_year=value)
    assert isinstance(value, str)
    return LegacyBikeProfileValues(**{field_name: value})  # type: ignore[arg-type]


def empty_technical_projection() -> dict[str, Any]:
    """Return the stable top-level shape of the internal V2 projection."""

    return {
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


def with_technical_value(
    projection: dict[str, Any] | None,
    *,
    field_path: str,
    value: Any | None,
) -> dict[str, Any]:
    """Return a V2 projection with one resolved leaf applied or cleared."""

    result = deepcopy(projection) if projection else empty_technical_projection()
    for key, default in empty_technical_projection().items():
        result.setdefault(key, default)
    parts = field_path.split(".")
    cursor: dict[str, Any] = result
    for part in parts[:-1]:
        child = cursor.get(part)
        if not isinstance(child, dict):
            child = {}
            cursor[part] = child
        cursor = child
    cursor[parts[-1]] = value
    return result


def technical_value(
    projection: dict[str, Any] | None,
    field_path: str,
) -> Any | None:
    """Read a resolved technical leaf without exposing storage traversal."""

    cursor: Any = projection or {}
    for part in field_path.split("."):
        if not isinstance(cursor, dict):
            return None
        cursor = cursor.get(part)
    return cursor


def has_technical_value_path(
    projection: dict[str, Any] | None,
    field_path: str,
) -> bool:
    """Return whether a projection deliberately carries a leaf, including null."""

    cursor: Any = projection or {}
    parts = field_path.split(".")
    for part in parts[:-1]:
        if not isinstance(cursor, dict):
            return False
        cursor = cursor.get(part)
    return isinstance(cursor, dict) and parts[-1] in cursor


def migrate_legacy_profile(values: LegacyBikeProfileValues) -> list[NewBikeFactClaim]:
    """Map V1 columns to scoped canonical claims without inventing detail."""

    return legacy_profile_claims(
        values,
        source_type="legacy_profile_migration",
    )


def legacy_profile_claims(
    values: LegacyBikeProfileValues,
    *,
    source_type: str,
) -> list[NewBikeFactClaim]:
    """Map a V1-shaped technical write to canonical claims."""

    claims: list[NewBikeFactClaim] = []
    _append_value(claims, "identity.make", values.make, source_type)
    _append_value(claims, "identity.model", values.model, source_type)
    _append_value(claims, "identity.model_year", values.model_year, source_type)
    _append_value(claims, "identity.bike_type", values.bike_type, source_type)
    _append_value(claims, "frame.material", values.frame_material, source_type)
    _append_value(
        claims,
        "drivetrain.legacy_description",
        values.drivetrain,
        source_type,
    )

    _append_legacy_brake_claims(claims, values.brake_type, source_type)
    _append_symmetric_claims(
        claims,
        value=values.wheel_size,
        paths=(
            "rolling_system.front.wheel.nominal_size",
            "rolling_system.rear.wheel.nominal_size",
        ),
        source_type=source_type,
    )
    _append_symmetric_claims(
        claims,
        value=values.tire_size,
        paths=(
            "rolling_system.front.tire.marked_size",
            "rolling_system.rear.tire.marked_size",
        ),
        source_type=source_type,
    )
    return claims


def _append_value(
    claims: list[NewBikeFactClaim],
    field_path: str,
    value: str | int | None,
    source_type: str,
) -> None:
    if value is not None and value != "unknown":
        claims.append(
            NewBikeFactClaim(
                field_path=field_path,
                value=value,
                source_type=source_type,
            ),
        )


def _append_symmetric_claims(
    claims: list[NewBikeFactClaim],
    *,
    value: str | None,
    paths: tuple[str, str],
    source_type: str,
) -> None:
    if value is None or value == "unknown":
        return
    for path in paths:
        claims.append(
            NewBikeFactClaim(
                field_path=path,
                value=value,
                source_type=source_type,
                scope_assumption="whole_bike",
            ),
        )


def _append_legacy_brake_claims(
    claims: list[NewBikeFactClaim],
    brake_type: str | None,
    source_type: str,
) -> None:
    if brake_type in {None, "unknown"}:
        return
    assert brake_type is not None
    if brake_type == "coaster":
        for field_path, value in (
            ("brakes.rear.mechanism", "coaster"),
            ("brakes.rear.actuation", "none"),
        ):
            claims.append(
                NewBikeFactClaim(
                    field_path=field_path,
                    value=value,
                    source_type=source_type,
                ),
            )
        return
    if brake_type == "other":
        claims.append(
            NewBikeFactClaim(
                field_path="brakes.legacy_summary",
                value="other",
                source_type=source_type,
            ),
        )
        return
    mappings = {
        "mechanical_disc": ("disc", "mechanical"),
        "hydraulic_disc": ("disc", "hydraulic"),
        "rim": ("rim_other", None),
    }
    mechanism, actuation = mappings[brake_type]
    for position in ("front", "rear"):
        claims.append(
            NewBikeFactClaim(
                field_path=f"brakes.{position}.mechanism",
                value=mechanism,
                source_type=source_type,
                scope_assumption="whole_bike",
            ),
        )
        if actuation is not None:
            claims.append(
                NewBikeFactClaim(
                    field_path=f"brakes.{position}.actuation",
                    value=actuation,
                    source_type=source_type,
                    scope_assumption="whole_bike",
                ),
            )
