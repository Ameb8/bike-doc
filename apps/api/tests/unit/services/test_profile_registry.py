"""Registry and projection invariants for positioned rolling-system claims."""

from __future__ import annotations

import pytest

from bike_doc_api.schemas.profile_inference import ROLLING_SYSTEM_INFERENCE_FIELD_PATHS
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
