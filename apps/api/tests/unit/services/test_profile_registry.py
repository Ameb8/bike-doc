"""Registry and projection invariants for positioned rolling-system claims."""

from __future__ import annotations

import pytest

from bike_doc_api.schemas.profile_inference import (
    DRIVETRAIN_INFERENCE_FIELD_PATHS,
    PROFILE_INFERENCE_FIELD_PATHS,
    ROLLING_SYSTEM_INFERENCE_FIELD_PATHS,
)
from bike_doc_api.services.profile_registry import (
    CANONICAL_FIELD_REGISTRY,
    FieldRegistryValidationError,
    get_canonical_field_definition,
)
from bike_doc_api.services.profile_resolution import (
    empty_technical_projection,
    technical_value,
    with_technical_value,
)


@pytest.mark.parametrize("position", ["front", "rear"])
def test_each_rolling_position_has_independent_component_field_matrix(
    position: str,
) -> None:
    prefix = f"rolling_system.{position}"
    expected = {
        *(
            f"{prefix}.{component}.{field}"
            for component in ("wheel", "rim", "tire", "hub")
            for field in ("presence", "manufacturer", "model")
        ),
        f"{prefix}.wheel.nominal_size",
        f"{prefix}.wheel.iso_bsd_mm",
        f"{prefix}.rim.internal_width_mm",
        f"{prefix}.tire.marked_size",
        f"{prefix}.tire.iso_width_mm",
        f"{prefix}.tire.iso_bsd_mm",
        f"{prefix}.tire.setup",
        f"{prefix}.tire.tubeless_ready",
        f"{prefix}.hub.axle_type",
        f"{prefix}.hub.axle_standard",
        f"{prefix}.hub.rotor_mount",
    }

    assert expected <= ROLLING_SYSTEM_INFERENCE_FIELD_PATHS
    assert all(CANONICAL_FIELD_REGISTRY[path].scope == position for path in expected)


def test_rear_driver_is_marking_based_and_front_driver_is_not_a_canonical_field() -> (
    None
):
    driver = get_canonical_field_definition("rolling_system.rear.hub.driver_interface")

    assert driver.permitted_evidence_bases == frozenset({"readable_marking"})
    assert driver.requires_readable_marking is True
    assert driver.image_auto_fill is True
    with pytest.raises(FieldRegistryValidationError):
        get_canonical_field_definition("rolling_system.front.hub.driver_interface")


@pytest.mark.parametrize(
    "field_path",
    [
        "rolling_system.front.wheel.nominal_size",
        "rolling_system.rear.wheel.iso_bsd_mm",
        "rolling_system.front.rim.internal_width_mm",
        "rolling_system.rear.tire.iso_width_mm",
        "rolling_system.front.tire.iso_bsd_mm",
        "rolling_system.rear.hub.axle_standard",
    ],
)
def test_rolling_exact_dimensions_require_readable_markings(field_path: str) -> None:
    field = get_canonical_field_definition(field_path)

    assert field.permitted_evidence_bases == frozenset({"readable_marking"})
    assert field.requires_readable_marking is True
    assert field.policy_bundle == "exact_dimension"


def test_tire_absence_removes_compatible_identity_and_specification_leaves() -> None:
    projection = with_technical_value(
        empty_technical_projection(),
        field_path="rolling_system.rear.tire.model",
        value="Minion DHF",
    )

    absent = with_technical_value(
        projection,
        field_path="rolling_system.rear.tire.presence",
        value="absent",
    )

    assert technical_value(absent, "rolling_system.rear.tire") == {
        "presence": "absent",
    }
    assert technical_value(absent, "rolling_system.front.tire") is None
    assert technical_value(absent, "rolling_system.rear.rim") is None


def test_drivetrain_inference_registry_includes_roles_but_not_derived_specs() -> None:
    components = (
        "front_shifter",
        "rear_shifter",
        "front_derailleur",
        "rear_derailleur",
        "crankset",
        "rear_cluster",
        "chain",
        "belt",
        "gear_unit",
        "bottom_bracket",
    )

    expected = {
        "drivetrain.architecture",
        "drivetrain.drive_medium",
        *(f"drivetrain.{component}.presence" for component in components),
        *(
            f"drivetrain.{component}.{field}"
            for component in components
            for field in ("manufacturer", "model")
        ),
        "drivetrain.front_shifter.actuation",
        "drivetrain.rear_shifter.actuation",
        "drivetrain.front_shifter.speed_count",
        "drivetrain.rear_shifter.speed_count",
        "drivetrain.rear_derailleur.mount_type",
        "drivetrain.rear_cluster.cluster_type",
        "drivetrain.crankset.chainring_count",
        "drivetrain.crankset.chainring_tooth_counts",
        "drivetrain.rear_cluster.speed_count",
        "drivetrain.rear_cluster.smallest_sprocket_teeth",
        "drivetrain.rear_cluster.largest_sprocket_teeth",
        "drivetrain.rear_cluster.driver_interface",
        "drivetrain.chain.speed_compatibility",
        "drivetrain.gear_unit.speed_count",
        "drivetrain.bottom_bracket.interface",
        "drivetrain.bottom_bracket.shell_width_mm",
    }

    assert expected == DRIVETRAIN_INFERENCE_FIELD_PATHS
    assert "drivetrain.front_chainring_count" not in PROFILE_INFERENCE_FIELD_PATHS
    assert "drivetrain.rear_speed_count" not in PROFILE_INFERENCE_FIELD_PATHS
    assert "drivetrain.legacy_description" not in PROFILE_INFERENCE_FIELD_PATHS
    assert all(
        CANONICAL_FIELD_REGISTRY[path].image_auto_fill
        for path in DRIVETRAIN_INFERENCE_FIELD_PATHS
    )


@pytest.mark.parametrize(
    "field_path",
    [
        "drivetrain.crankset.chainring_count",
        "drivetrain.crankset.chainring_tooth_counts",
        "drivetrain.rear_cluster.speed_count",
        "drivetrain.rear_cluster.smallest_sprocket_teeth",
        "drivetrain.rear_cluster.largest_sprocket_teeth",
        "drivetrain.front_shifter.speed_count",
        "drivetrain.rear_shifter.speed_count",
        "drivetrain.gear_unit.speed_count",
        "drivetrain.chain.speed_compatibility",
    ],
)
def test_counted_drivetrain_specs_require_counted_or_readable_evidence(
    field_path: str,
) -> None:
    field = get_canonical_field_definition(field_path)

    assert field.permitted_evidence_bases == frozenset(
        {"counted_visual", "readable_marking"}
    )
    assert field.requires_counted_evidence is True
    assert field.policy_bundle == "counted_spec"


@pytest.mark.parametrize(
    "field_path",
    [
        "drivetrain.rear_cluster.driver_interface",
        "drivetrain.bottom_bracket.interface",
        "drivetrain.bottom_bracket.shell_width_mm",
    ],
)
def test_marked_drivetrain_specs_require_readable_markings(field_path: str) -> None:
    field = get_canonical_field_definition(field_path)

    assert field.permitted_evidence_bases == frozenset({"readable_marking"})
    assert field.requires_readable_marking is True


@pytest.mark.parametrize(
    "field_path",
    [
        "drivetrain.front_shifter.actuation",
        "drivetrain.rear_shifter.actuation",
        "drivetrain.rear_derailleur.mount_type",
        "drivetrain.rear_cluster.cluster_type",
    ],
)
def test_drivetrain_role_configuration_requires_clear_direct_evidence(
    field_path: str,
) -> None:
    field = get_canonical_field_definition(field_path)

    assert field.permitted_evidence_bases == frozenset({"direct_visual"})
    assert field.requires_direct_evidence is True
    assert field.image_auto_fill is True
    assert field.policy_bundle == "installed_mechanism"


@pytest.mark.parametrize(
    "component",
    [
        "front_shifter",
        "rear_shifter",
        "front_derailleur",
        "rear_derailleur",
        "crankset",
        "rear_cluster",
        "chain",
        "belt",
        "gear_unit",
        "bottom_bracket",
    ],
)
def test_absent_drivetrain_component_requires_empty_component_leaves(
    component: str,
) -> None:
    projection = with_technical_value(
        empty_technical_projection(),
        field_path=f"drivetrain.{component}.model",
        value="Example",
    )

    absent = with_technical_value(
        projection,
        field_path=f"drivetrain.{component}.presence",
        value="absent",
    )

    assert technical_value(absent, f"drivetrain.{component}") == {
        "presence": "absent",
    }
