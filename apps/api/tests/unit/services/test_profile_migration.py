"""Legacy profile migration behavior."""

from __future__ import annotations

import pytest

from bike_doc_api.services.profile_registry import (
    FieldRegistryValidationError,
    get_canonical_field,
)
from bike_doc_api.services.profile_resolution import (
    LegacyBikeProfileValues,
    migrate_legacy_profile,
    with_technical_value,
)


def test_legacy_profile_migration_preserves_scoped_technical_meaning() -> None:
    claims = migrate_legacy_profile(
        LegacyBikeProfileValues(
            make="Surly",
            model="Straggler",
            model_year=2021,
            bike_type="gravel",
            frame_material="steel",
            drivetrain="Shimano 2x10",
            brake_type="hydraulic_disc",
            wheel_size="700c",
            tire_size="700x38",
        ),
    )

    assert {(claim.field_path, claim.value) for claim in claims} == {
        ("identity.make", "Surly"),
        ("identity.model", "Straggler"),
        ("identity.model_year", 2021),
        ("identity.bike_type", "gravel"),
        ("frame.material", "steel"),
        ("drivetrain.legacy_description", "Shimano 2x10"),
        ("brakes.front.mechanism", "disc"),
        ("brakes.front.actuation", "hydraulic"),
        ("brakes.rear.mechanism", "disc"),
        ("brakes.rear.actuation", "hydraulic"),
        ("rolling_system.front.wheel.nominal_size", "700c"),
        ("rolling_system.rear.wheel.nominal_size", "700c"),
        ("rolling_system.front.tire.marked_size", "700x38"),
        ("rolling_system.rear.tire.marked_size", "700x38"),
    }
    assert {claim.source_type for claim in claims} == {"legacy_profile_migration"}
    assert {
        claim.scope_assumption
        for claim in claims
        if claim.field_path.startswith(("brakes.", "rolling_system."))
    } == {"whole_bike"}


def test_legacy_coaster_and_ambiguous_other_do_not_invent_front_brake_facts() -> None:
    coaster_claims = migrate_legacy_profile(
        LegacyBikeProfileValues(brake_type="coaster"),
    )
    other_claims = migrate_legacy_profile(
        LegacyBikeProfileValues(brake_type="other"),
    )

    assert [(claim.field_path, claim.value) for claim in coaster_claims] == [
        ("brakes.rear.mechanism", "coaster"),
        ("brakes.rear.actuation", "none"),
    ]
    assert [(claim.field_path, claim.value) for claim in other_claims] == [
        ("brakes.legacy_summary", "other"),
    ]


def test_legacy_unknown_sentinels_migrate_to_no_claims() -> None:
    claims = migrate_legacy_profile(
        LegacyBikeProfileValues(
            bike_type="unknown",
            frame_material="unknown",
            brake_type="unknown",
        ),
    )

    assert claims == []


def test_legacy_blank_and_text_unknown_values_migrate_to_no_claims() -> None:
    claims = migrate_legacy_profile(
        LegacyBikeProfileValues(
            make="  ",
            model="UNKNOWN",
            drivetrain="unknown",
            wheel_size="",
            tire_size="  ",
        ),
    )

    assert claims == []


def test_canonical_field_registry_rejects_unknown_and_invalid_values() -> None:
    rear_brake = get_canonical_field("brakes.rear.mechanism", "disc")

    assert rear_brake.volatility_class == "installed_configuration"
    assert rear_brake.scope == "rear"

    with pytest.raises(FieldRegistryValidationError):
        get_canonical_field("brakes.whole_bike.mechanism", "disc")
    with pytest.raises(FieldRegistryValidationError):
        get_canonical_field("brakes.rear.mechanism", "hydraulic_disc")
    with pytest.raises(FieldRegistryValidationError):
        get_canonical_field("brakes.front.mechanism", "coaster")
    with pytest.raises(FieldRegistryValidationError):
        get_canonical_field("brakes.front.actuation", "none")
    with pytest.raises(FieldRegistryValidationError):
        get_canonical_field("identity.model_year", 1)
    with pytest.raises(FieldRegistryValidationError):
        get_canonical_field("rolling_system.front.tire.iso_width_mm", 0)


def test_component_absence_clears_identity_and_specification_leaves() -> None:
    projection = with_technical_value(
        None,
        field_path="brakes.rear.brake_unit.manufacturer",
        value="Shimano",
    )

    component_absent = with_technical_value(
        projection,
        field_path="brakes.rear.brake_unit.presence",
        value="absent",
    )

    assert component_absent["brakes"]["rear"]["brake_unit"] == {
        "presence": "absent",
    }


def test_non_disc_mechanism_clears_current_rotor_facts() -> None:
    projection = with_technical_value(
        None,
        field_path="brakes.front.mechanism",
        value="disc",
    )
    projection = with_technical_value(
        projection,
        field_path="brakes.front.rotor.presence",
        value="present",
    )
    projection = with_technical_value(
        projection,
        field_path="brakes.front.rotor.diameter_mm",
        value=160,
    )

    rim_projection = with_technical_value(
        projection,
        field_path="brakes.front.mechanism",
        value="rim_caliper",
    )

    assert rim_projection["brakes"]["front"]["rotor"] == {}
