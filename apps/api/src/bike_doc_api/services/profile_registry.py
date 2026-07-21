"""Versioned canonical registry for bike-profile technical fields."""

from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from typing import Any, Literal

FieldValueKind = Literal["string", "integer", "number", "boolean", "enum"]
FIELD_REGISTRY_VERSION = "bike_profile_registry.v2"


class FieldRegistryValidationError(ValueError):
    """A claim did not target a canonical field with a valid value."""


@dataclass(frozen=True, slots=True)
class CanonicalField:
    """Policy and validation information for one versioned field path."""

    field_path: str
    value_kind: FieldValueKind
    scope: str
    volatility_class: str
    consequence_class: str
    permitted_evidence_bases: frozenset[str]
    image_auto_fill: bool
    image_auto_supersedes: frozenset[str]
    requires_readable_marking: bool = False
    requires_direct_evidence: bool = False
    requires_counted_evidence: bool = False
    derived_rule: str | None = None
    policy_bundle: str = "inference_only_pending"
    calibration_key: str = "bootstrap-v1"
    enum_values: frozenset[str] = frozenset()

    def validate(self, value: Any) -> None:
        """Reject values which cannot appear at this canonical path."""

        if value is None:
            raise FieldRegistryValidationError("Claim values must be non-null.")
        if self.value_kind == "string":
            if not isinstance(value, str) or not value.strip():
                raise FieldRegistryValidationError(
                    f"{self.field_path} requires a non-blank string.",
                )
            return
        if self.value_kind == "integer":
            if isinstance(value, bool) or not isinstance(value, int):
                raise FieldRegistryValidationError(
                    f"{self.field_path} requires an integer.",
                )
            if self.field_path == "identity.model_year" and not 1880 <= value <= 2100:
                raise FieldRegistryValidationError(
                    "identity.model_year must be between 1880 and 2100.",
                )
            if self.field_path != "identity.model_year" and value <= 0:
                raise FieldRegistryValidationError(
                    f"{self.field_path} requires a positive integer.",
                )
            return
        if self.value_kind == "number":
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not isfinite(value)
                or value <= 0
            ):
                raise FieldRegistryValidationError(
                    f"{self.field_path} requires a finite positive number.",
                )
            return
        if self.value_kind == "boolean":
            if not isinstance(value, bool):
                raise FieldRegistryValidationError(
                    f"{self.field_path} requires a boolean.",
                )
            return
        if not isinstance(value, str) or value not in self.enum_values:
            raise FieldRegistryValidationError(
                f"{self.field_path} does not allow {value!r}.",
            )


def get_canonical_field_definition(field_path: str) -> CanonicalField:
    """Return a registry entry while rejecting unknown canonical paths."""

    field = CANONICAL_FIELD_REGISTRY.get(field_path)
    if field is None:
        raise FieldRegistryValidationError(f"Unknown bike-profile field: {field_path}")
    return field


def get_canonical_field(field_path: str, value: Any) -> CanonicalField:
    """Return a validated registry entry; unknown paths are never persisted."""

    field = get_canonical_field_definition(field_path)
    field.validate(value)
    return field


def normalize_canonical_value(field_path: str, value: Any) -> Any:
    """Validate and return the stable representation used for comparisons."""
    field = get_canonical_field_definition(field_path)
    normalized = value
    if isinstance(value, str):
        normalized = value.strip()
        if field.value_kind == "enum":
            normalized = normalized.lower()
    field.validate(normalized)
    return normalized


def _field(
    field_path: str,
    value_kind: FieldValueKind,
    *,
    scope: str = "whole_bike",
    volatility: str = "installed_configuration",
    consequence: str = "compatibility",
    evidence: frozenset[str] = frozenset({"direct_visual"}),
    auto_fill: bool = False,
    supersedes: frozenset[str] = frozenset(),
    marking: bool = False,
    direct: bool = False,
    counted: bool = False,
    derived: str | None = None,
    bundle: str = "inference_only_pending",
    values: frozenset[str] = frozenset(),
) -> CanonicalField:
    return CanonicalField(
        field_path=field_path,
        value_kind=value_kind,
        scope=scope,
        volatility_class=volatility,
        consequence_class=consequence,
        permitted_evidence_bases=evidence,
        image_auto_fill=auto_fill,
        image_auto_supersedes=supersedes,
        requires_readable_marking=marking,
        requires_direct_evidence=direct,
        requires_counted_evidence=counted,
        derived_rule=derived,
        policy_bundle=bundle,
        enum_values=values,
    )


def _enum(
    path: str,
    values: set[str],
    **kwargs: Any,
) -> CanonicalField:
    return _field(path, "enum", values=frozenset(values), **kwargs)


def _component_fields(
    registry: dict[str, CanonicalField],
    prefix: str,
    *,
    scope: str = "whole_bike",
) -> None:
    registry[f"{prefix}.presence"] = _enum(
        f"{prefix}.presence",
        {"unknown", "present", "absent"},
        scope=scope,
        consequence="safety",
        auto_fill=True,
        bundle="visual_descriptive",
    )
    for field_name in ("manufacturer", "model"):
        registry[f"{prefix}.{field_name}"] = _field(
            f"{prefix}.{field_name}",
            "string",
            scope=scope,
            evidence=frozenset({"readable_marking", "derived_visual"}),
            auto_fill=True,
            supersedes=frozenset({"image_inference", "legacy_profile_migration"}),
            marking=True,
            bundle="readable_identity",
        )


def _build_registry() -> dict[str, CanonicalField]:
    registry: dict[str, CanonicalField] = {}
    readable_identity: dict[str, Any] = {
        # Similarity claims are retained for review, but the readable-marking
        # requirement below keeps them out of automatic mutation.
        "evidence": frozenset({"readable_marking", "derived_visual"}),
        "auto_fill": True,
        "supersedes": frozenset({"image_inference", "legacy_profile_migration"}),
        "marking": True,
        "bundle": "readable_identity",
    }
    registry["identity.make"] = _field(
        "identity.make",
        "string",
        volatility="stable_identity",
        **readable_identity,
    )
    registry["identity.model"] = _field(
        "identity.model",
        "string",
        volatility="stable_identity",
        **readable_identity,
    )
    registry["identity.model_year"] = _field(
        "identity.model_year",
        "integer",
        volatility="stable_identity",
        **readable_identity,
    )
    registry["identity.bike_type"] = _enum(
        "identity.bike_type",
        {
            "road",
            "gravel",
            "mountain",
            "hybrid",
            "commuter",
            "cargo",
            "ebike",
            "bmx",
            "folding",
            "recumbent",
            "other",
        },
        volatility="descriptive",
        consequence="low",
        auto_fill=True,
        bundle="visual_descriptive",
    )
    registry["frame.material"] = _enum(
        "frame.material",
        {"aluminum", "steel", "carbon", "titanium", "other"},
        volatility="stable_identity",
        **readable_identity,
    )
    for name in ("size_label", "primary_color", "secondary_color"):
        registry[f"frame.{name}"] = _field(
            f"frame.{name}",
            "string",
            volatility="stable_identity" if name == "size_label" else "descriptive",
            consequence="low",
            auto_fill=True,
            supersedes=(
                frozenset({"image_inference", "legacy_profile_migration"})
                if name == "size_label"
                else frozenset()
            ),
            bundle="readable_identity"
            if name == "size_label"
            else "visual_descriptive",
            evidence=frozenset({"readable_marking", "derived_visual"})
            if name == "size_label"
            else frozenset({"direct_visual"}),
            marking=name == "size_label",
        )

    mechanism_values = {
        "disc",
        "rim_caliper",
        "rim_cantilever",
        "rim_v_brake",
        "rim_u_brake",
        "rim_other",
        "coaster",
        "drum",
        "roller",
        "other",
    }
    actuation_values = {"mechanical", "hydraulic", "electronic", "none", "other"}
    for position in ("front", "rear"):
        prefix = f"brakes.{position}"
        registry[f"{prefix}.presence"] = _enum(
            f"{prefix}.presence",
            {"unknown", "present", "absent"},
            scope=position,
            consequence="safety",
            auto_fill=True,
            bundle="visual_descriptive",
        )
        registry[f"{prefix}.mechanism"] = _enum(
            f"{prefix}.mechanism",
            mechanism_values if position == "rear" else mechanism_values - {"coaster"},
            scope=position,
            consequence="safety",
            auto_fill=True,
            supersedes=frozenset(
                {"image_inference", "legacy_profile_migration", "manual_profile_edit"}
            ),
            bundle="installed_mechanism",
        )
        registry[f"{prefix}.actuation"] = _enum(
            f"{prefix}.actuation",
            actuation_values if position == "rear" else actuation_values - {"none"},
            scope=position,
            consequence="safety",
            auto_fill=True,
            supersedes=frozenset(
                {"image_inference", "legacy_profile_migration", "manual_profile_edit"}
            ),
            bundle="installed_mechanism",
        )
        _component_fields(registry, f"{prefix}.control", scope=position)
        _component_fields(registry, f"{prefix}.brake_unit", scope=position)
        registry[f"{prefix}.brake_unit.mount_standard"] = _enum(
            f"{prefix}.brake_unit.mount_standard",
            {
                "flat_mount",
                "post_mount",
                "international_standard",
                "center_bolt",
                "frame_boss",
                "other",
            },
            scope=position,
            evidence=frozenset({"direct_visual", "derived_visual"}),
            auto_fill=True,
            direct=True,
            bundle="installed_mechanism",
        )
        registry[f"{prefix}.brake_unit.pad_family"] = _field(
            f"{prefix}.brake_unit.pad_family",
            "string",
            scope=position,
            evidence=frozenset({"readable_marking", "derived_visual"}),
            auto_fill=True,
            marking=True,
            bundle="readable_identity",
        )
        _component_fields(registry, f"{prefix}.rotor", scope=position)
        registry[f"{prefix}.rotor.diameter_mm"] = _field(
            f"{prefix}.rotor.diameter_mm",
            "number",
            scope=position,
            consequence="safety",
            evidence=frozenset({"readable_marking", "derived_visual"}),
            auto_fill=True,
            marking=True,
            bundle="exact_dimension",
        )
    registry["brakes.legacy_summary"] = _enum(
        "brakes.legacy_summary",
        {"mechanical_disc", "hydraulic_disc", "rim", "coaster", "other"},
        volatility="derived",
        consequence="low",
        derived="legacy_compatibility_only",
    )

    registry["drivetrain.architecture"] = _enum(
        "drivetrain.architecture",
        {
            "derailleur",
            "internal_gear_hub",
            "gearbox",
            "singlespeed_freewheel",
            "fixed_gear",
            "continuously_variable",
            "other",
        },
        auto_fill=True,
        supersedes=frozenset(
            {"image_inference", "legacy_profile_migration", "manual_profile_edit"}
        ),
        bundle="installed_mechanism",
    )
    registry["drivetrain.drive_medium"] = _enum(
        "drivetrain.drive_medium",
        {"chain", "belt", "shaft", "other"},
        auto_fill=True,
        supersedes=frozenset(
            {"image_inference", "legacy_profile_migration", "manual_profile_edit"}
        ),
        bundle="installed_mechanism",
    )
    for path in ("drivetrain.front_chainring_count", "drivetrain.rear_speed_count"):
        registry[path] = _field(
            path,
            "integer",
            volatility="derived",
            derived="component_count",
            bundle="derived",
        )
    registry["drivetrain.legacy_description"] = _field(
        "drivetrain.legacy_description",
        "string",
        volatility="derived",
        consequence="low",
        derived="legacy_compatibility_only",
    )
    drivetrain_components: dict[
        str, tuple[dict[str, set[str]], dict[str, FieldValueKind]]
    ] = {
        "front_shifter": (
            {"actuation": {"mechanical", "electronic", "hydraulic", "other"}},
            {"speed_count": "integer"},
        ),
        "rear_shifter": (
            {"actuation": {"mechanical", "electronic", "hydraulic", "other"}},
            {"speed_count": "integer"},
        ),
        "front_derailleur": ({}, {}),
        "rear_derailleur": (
            {"mount_type": {"hanger", "direct_mount", "full_mount", "other"}},
            {},
        ),
        "crankset": (
            {},
            {"chainring_count": "integer", "chainring_tooth_counts": "string"},
        ),
        "rear_cluster": (
            {
                "cluster_type": {
                    "cassette",
                    "freewheel",
                    "single_sprocket",
                    "belt_cog",
                    "other",
                },
                "driver_interface": {
                    "hg",
                    "microspline",
                    "xd",
                    "xdr",
                    "campagnolo",
                    "threaded_freewheel",
                    "other",
                },
            },
            {
                "speed_count": "integer",
                "smallest_sprocket_teeth": "integer",
                "largest_sprocket_teeth": "integer",
            },
        ),
        "chain": ({}, {"speed_compatibility": "integer"}),
        "belt": ({}, {}),
        "gear_unit": ({}, {"speed_count": "integer"}),
        "bottom_bracket": ({}, {"shell_width_mm": "number"}),
    }
    for component, (enum_fields, scalar_fields) in drivetrain_components.items():
        prefix = f"drivetrain.{component}"
        _component_fields(registry, prefix)
        for name, values in enum_fields.items():
            is_direct_configuration = name in {
                "actuation",
                "mount_type",
                "cluster_type",
            }
            is_marked_specification = name == "driver_interface"
            registry[f"{prefix}.{name}"] = _enum(
                f"{prefix}.{name}",
                values,
                evidence=(
                    frozenset({"readable_marking"})
                    if is_marked_specification
                    else frozenset({"direct_visual"})
                    if is_direct_configuration
                    else frozenset({"direct_visual", "derived_visual"})
                ),
                auto_fill=is_direct_configuration or is_marked_specification,
                supersedes=frozenset({"image_inference", "legacy_profile_migration"}),
                marking=is_marked_specification,
                direct=is_direct_configuration,
                bundle="installed_mechanism"
                if name
                in {
                    "actuation",
                    "mount_type",
                    "cluster_type",
                }
                else "readable_identity"
                if is_marked_specification
                else "inference_only_pending",
            )
        if component == "bottom_bracket":
            registry[f"{prefix}.interface"] = _field(
                f"{prefix}.interface",
                "string",
                evidence=frozenset({"readable_marking"}),
                auto_fill=True,
                supersedes=frozenset({"image_inference", "legacy_profile_migration"}),
                marking=True,
                bundle="exact_dimension",
            )
        for name, kind in scalar_fields.items():
            is_counted_specification = name in {
                "speed_count",
                "chainring_count",
                "chainring_tooth_counts",
                "smallest_sprocket_teeth",
                "largest_sprocket_teeth",
                "speed_compatibility",
            }
            registry[f"{prefix}.{name}"] = _field(
                f"{prefix}.{name}",
                kind,
                evidence=(
                    frozenset({"counted_visual", "readable_marking"})
                    if is_counted_specification
                    else frozenset({"readable_marking"})
                ),
                auto_fill=True,
                supersedes=frozenset({"image_inference", "legacy_profile_migration"}),
                marking=not is_counted_specification,
                counted=is_counted_specification,
                bundle="counted_spec"
                if is_counted_specification
                else "exact_dimension",
            )

    driver_interfaces = {
        "hg",
        "microspline",
        "xd",
        "xdr",
        "campagnolo",
        "threaded_freewheel",
        "other",
    }
    for position in ("front", "rear"):
        for component in ("wheel", "rim", "tire", "hub"):
            _component_fields(
                registry, f"rolling_system.{position}.{component}", scope=position
            )
        prefix = f"rolling_system.{position}"
        registry[f"{prefix}.wheel.nominal_size"] = _field(
            f"{prefix}.wheel.nominal_size",
            "string",
            scope=position,
            evidence=frozenset({"readable_marking"}),
            marking=True,
            auto_fill=True,
            bundle="exact_dimension",
        )
        registry[f"{prefix}.wheel.iso_bsd_mm"] = _field(
            f"{prefix}.wheel.iso_bsd_mm",
            "integer",
            scope=position,
            evidence=frozenset({"readable_marking"}),
            marking=True,
            auto_fill=True,
            bundle="exact_dimension",
        )
        registry[f"{prefix}.rim.internal_width_mm"] = _field(
            f"{prefix}.rim.internal_width_mm",
            "number",
            scope=position,
            evidence=frozenset({"readable_marking"}),
            marking=True,
            auto_fill=True,
            bundle="exact_dimension",
        )
        registry[f"{prefix}.tire.marked_size"] = _field(
            f"{prefix}.tire.marked_size",
            "string",
            scope=position,
            evidence=frozenset({"readable_marking"}),
            marking=True,
            auto_fill=True,
            bundle="readable_identity",
        )
        for name in ("iso_width_mm", "iso_bsd_mm"):
            registry[f"{prefix}.tire.{name}"] = _field(
                f"{prefix}.tire.{name}",
                "integer",
                scope=position,
                evidence=frozenset({"readable_marking"}),
                marking=True,
                auto_fill=True,
                bundle="exact_dimension",
            )
        registry[f"{prefix}.tire.setup"] = _enum(
            f"{prefix}.tire.setup",
            {"tubed", "tubeless", "tubular", "airless", "other"},
            scope=position,
        )
        registry[f"{prefix}.tire.tubeless_ready"] = _field(
            f"{prefix}.tire.tubeless_ready",
            "boolean",
            scope=position,
            evidence=frozenset({"readable_marking"}),
            marking=True,
            auto_fill=True,
            bundle="readable_identity",
        )
        registry[f"{prefix}.hub.axle_type"] = _enum(
            f"{prefix}.hub.axle_type",
            {"quick_release", "thru_axle", "bolt_on", "solid_axle", "other"},
            scope=position,
            auto_fill=True,
            bundle="installed_mechanism",
        )
        registry[f"{prefix}.hub.axle_standard"] = _field(
            f"{prefix}.hub.axle_standard",
            "string",
            scope=position,
            evidence=frozenset({"readable_marking"}),
            marking=True,
            auto_fill=True,
            bundle="exact_dimension",
        )
        registry[f"{prefix}.hub.rotor_mount"] = _enum(
            f"{prefix}.hub.rotor_mount",
            {"six_bolt", "centerlock", "none", "other"},
            scope=position,
            auto_fill=True,
            bundle="installed_mechanism",
        )
        if position == "rear":
            registry[f"{prefix}.hub.driver_interface"] = _enum(
                f"{prefix}.hub.driver_interface",
                driver_interfaces,
                scope=position,
                evidence=frozenset({"readable_marking"}),
                marking=True,
                auto_fill=True,
                bundle="readable_identity",
            )

    registry["suspension.fork.type"] = _enum(
        "suspension.fork.type",
        {"rigid", "suspension", "other"},
        auto_fill=True,
        supersedes=frozenset(
            {"image_inference", "legacy_profile_migration", "manual_profile_edit"}
        ),
        direct=True,
        bundle="installed_mechanism",
    )
    for name in ("manufacturer", "model"):
        registry[f"suspension.fork.{name}"] = _field(
            f"suspension.fork.{name}", "string", **readable_identity
        )
    registry["suspension.fork.travel_mm"] = _field(
        "suspension.fork.travel_mm",
        "integer",
        evidence=frozenset({"readable_marking"}),
        marking=True,
        auto_fill=True,
        supersedes=frozenset({"image_inference", "legacy_profile_migration"}),
        bundle="exact_dimension",
    )
    _component_fields(registry, "suspension.rear_shock")
    registry["suspension.rear_travel_mm"] = _field(
        "suspension.rear_travel_mm",
        "integer",
        evidence=frozenset({"readable_marking"}),
        marking=True,
        auto_fill=True,
        supersedes=frozenset({"image_inference", "legacy_profile_migration"}),
        bundle="exact_dimension",
    )

    registry["cockpit.handlebar.style"] = _enum(
        "cockpit.handlebar.style",
        {"drop", "flat", "riser", "swept", "bullhorn", "bmx", "other"},
        consequence="low",
        auto_fill=True,
        direct=True,
        bundle="visual_descriptive",
    )
    registry["cockpit.stem.type"] = _enum(
        "cockpit.stem.type",
        {"threadless", "quill", "integrated", "other"},
        auto_fill=True,
        direct=True,
        bundle="installed_mechanism",
    )
    registry["cockpit.headset.type"] = _enum(
        "cockpit.headset.type",
        {"external_cup", "zero_stack", "integrated", "threaded", "other"},
        auto_fill=True,
        direct=True,
        bundle="installed_mechanism",
    )
    for component in ("handlebar", "stem"):
        for name in ("manufacturer", "model"):
            registry[f"cockpit.{component}.{name}"] = _field(
                f"cockpit.{component}.{name}", "string", **readable_identity
            )
    _component_fields(registry, "seating.seatpost")
    registry["seating.seatpost.type"] = _enum(
        "seating.seatpost.type",
        {"rigid", "dropper", "suspension", "other"},
        auto_fill=True,
        direct=True,
        bundle="installed_mechanism",
    )
    registry["seating.seatpost.diameter_mm"] = _field(
        "seating.seatpost.diameter_mm",
        "number",
        evidence=frozenset({"readable_marking"}),
        auto_fill=True,
        marking=True,
        bundle="exact_dimension",
    )

    registry["electric_assist.presence"] = _enum(
        "electric_assist.presence",
        {"unknown", "present", "absent"},
        consequence="safety",
        auto_fill=True,
        bundle="visual_descriptive",
    )
    for path in ("electric_assist.system_manufacturer", "electric_assist.system_model"):
        registry[path] = _field(path, "string", **readable_identity)
    registry["electric_assist.motor.position"] = _enum(
        "electric_assist.motor.position",
        {"front_hub", "rear_hub", "mid_drive", "other"},
        consequence="safety",
        auto_fill=True,
        supersedes=frozenset(
            {"image_inference", "legacy_profile_migration", "manual_profile_edit"}
        ),
        direct=True,
        bundle="installed_mechanism",
    )
    for component in ("motor", "battery"):
        for name in ("manufacturer", "model"):
            registry[f"electric_assist.{component}.{name}"] = _field(
                f"electric_assist.{component}.{name}", "string", **readable_identity
            )
    registry["electric_assist.battery.nominal_voltage_v"] = _field(
        "electric_assist.battery.nominal_voltage_v",
        "number",
        consequence="safety",
        evidence=frozenset({"readable_marking"}),
        auto_fill=True,
        marking=True,
        bundle="exact_dimension",
    )
    return registry


FIELD_REGISTRY_VERSION = "bike_profile.v2"
CANONICAL_FIELD_REGISTRY = _build_registry()
