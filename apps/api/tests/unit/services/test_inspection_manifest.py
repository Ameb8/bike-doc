from copy import deepcopy

import pytest
from pydantic import ValidationError

from bike_doc_api.services.inspection_plan.manifest import (
    APPLICABILITY_VALUES,
    GENERAL_INSPECTION_V1,
    MANIFESTS,
    Applicability,
    Evidence,
    Manifest,
    SafetyCode,
    get_manifest,
)


def test_complete_ordered_manifest_and_cross_references() -> None:
    manifest = GENERAL_INSPECTION_V1
    assert [(s.id, s.group) for s in manifest.sections] == [
        ("overview", 1),
        ("wheels_tires", 2),
        ("brakes", 3),
        ("contact_controls", 4),
        ("drivetrain", 5),
        ("suspension", 6),
        ("electric_assist", 7),
    ]
    expected = [
        ("whole_bike_overview", "overview", Applicability.ALL, None, "report"),
        (
            "frame_fork_visible_condition",
            "overview",
            Applicability.ALL,
            SafetyCode.FRAME,
            "visual_photo",
        ),
        (
            "front_tire_condition",
            "wheels_tires",
            Applicability.ALL,
            SafetyCode.UNSAFE,
            "visual",
        ),
        (
            "rear_tire_condition",
            "wheels_tires",
            Applicability.ALL,
            SafetyCode.UNSAFE,
            "visual",
        ),
        (
            "front_wheel_security_rotation",
            "wheels_tires",
            Applicability.ALL,
            SafetyCode.UNSAFE,
            "functional_report",
        ),
        (
            "rear_wheel_security_rotation",
            "wheels_tires",
            Applicability.ALL,
            SafetyCode.UNSAFE,
            "functional_report",
        ),
        (
            "front_brake_condition_operation",
            "brakes",
            Applicability.FRONT_BRAKE,
            SafetyCode.BRAKE,
            "both",
        ),
        (
            "rear_brake_condition_operation",
            "brakes",
            Applicability.REAR_BRAKE,
            SafetyCode.BRAKE,
            "both",
        ),
        (
            "steering_cockpit_security",
            "contact_controls",
            Applicability.ALL,
            SafetyCode.UNSAFE,
            "functional_report",
        ),
        (
            "saddle_seatpost_security",
            "contact_controls",
            Applicability.ALL,
            SafetyCode.UNSAFE,
            "functional_report",
        ),
        (
            "drivetrain_condition_operation",
            "drivetrain",
            Applicability.SUPPORTED_DRIVETRAIN,
            SafetyCode.UNSAFE,
            "both",
        ),
        (
            "front_suspension_condition_operation",
            "suspension",
            Applicability.FRONT_SUSPENSION,
            SafetyCode.SUSPENSION,
            "both",
        ),
        (
            "rear_suspension_condition_operation",
            "suspension",
            Applicability.REAR_SUSPENSION,
            SafetyCode.SUSPENSION,
            "both",
        ),
        (
            "electric_assist_condition_operation",
            "electric_assist",
            Applicability.ELECTRIC_ASSIST,
            SafetyCode.ELECTRIC,
            "both",
        ),
    ]
    evidence = {
        "report": ({Evidence.TARGETED_REPORT}, {Evidence.PHOTO}),
        "visual_photo": ({Evidence.VISUAL_REPORT}, {Evidence.PHOTO}),
        "visual": ({Evidence.VISUAL_REPORT},),
        "functional_report": ({Evidence.FUNCTIONAL, Evidence.SPECIFIC_REPORT},),
        "both": (
            {Evidence.VISUAL_REPORT, Evidence.FUNCTIONAL},
            {Evidence.PHOTO, Evidence.FUNCTIONAL},
        ),
    }
    assert len(manifest.checks) == len(expected)
    for check, (id, section, applicability, code, rule) in zip(
        manifest.checks, expected, strict=True
    ):
        assert (
            check.id,
            check.section_id,
            check.applicability,
            check.default_safety_code,
        ) == (id, section, applicability, code)
        assert check.safety_critical == (code is not None)
        assert check.condition and check.evidence_alternatives == evidence[rule]
        assert any(
            Evidence.PHOTO not in alternative
            for alternative in check.evidence_alternatives
        )
        group = manifest.groups[
            manifest.sections[[s.id for s in manifest.sections].index(section)].group
            - 1
        ]
        assert group.procedure and group.stops_and_prohibitions and group.constraints
    assert APPLICABILITY_VALUES[Applicability.FRONT_SUSPENSION] == (
        "suspension",
        "rigid",
    )
    assert set(APPLICABILITY_VALUES) == {
        Applicability.FRONT_BRAKE,
        Applicability.REAR_BRAKE,
        Applicability.FRONT_SUSPENSION,
        Applicability.REAR_SUSPENSION,
        Applicability.ELECTRIC_ASSIST,
    }
    assert all(
        values == ("present", "absent")
        for policy, values in APPLICABILITY_VALUES.items()
        if policy != Applicability.FRONT_SUSPENSION
    )


def test_versions_are_immutable_and_exactly_resolved() -> None:
    assert get_manifest("general_inspection.v1") is GENERAL_INSPECTION_V1
    with pytest.raises(KeyError):
        get_manifest("general_inspection.v999")
    with pytest.raises(TypeError):
        MANIFESTS["new"] = GENERAL_INSPECTION_V1  # type: ignore[index]
    with pytest.raises(ValidationError):
        GENERAL_INSPECTION_V1.version = "changed"
    later = Manifest.model_validate(
        {**GENERAL_INSPECTION_V1.model_dump(), "version": "general_inspection.v2"}
    )
    versions = {
        GENERAL_INSPECTION_V1.version: GENERAL_INSPECTION_V1,
        later.version: later,
    }
    assert versions["general_inspection.v1"] is GENERAL_INSPECTION_V1
    assert versions["general_inspection.v2"] is later


@pytest.mark.parametrize(
    "mutation",
    [
        "unknown_field",
        "unknown_check",
        "unknown_policy",
        "duplicate",
        "unknown_section",
        "missing_group",
        "unsafe_evidence",
        "missing_safety",
        "empty_procedure",
        "order",
    ],
)
def test_invalid_manifest_fails_closed(mutation: str) -> None:
    data = deepcopy(GENERAL_INSPECTION_V1.model_dump())
    checks = list(data["checks"])
    if mutation == "unknown_field":
        checks[0]["unknown"] = True
    elif mutation == "unknown_check":
        checks[0]["id"] = "unknown_check"
    elif mutation == "unknown_policy":
        checks[0]["applicability"] = "invented"
    elif mutation == "duplicate":
        checks[1]["id"] = checks[0]["id"]
    elif mutation == "unknown_section":
        checks[0]["section_id"] = "unknown"
    elif mutation == "missing_group":
        data["groups"] = data["groups"][:-1]
    elif mutation == "unsafe_evidence":
        checks[0]["evidence_alternatives"] = (frozenset({Evidence.PHOTO}),)
    elif mutation == "missing_safety":
        checks[1]["default_safety_code"] = None
    elif mutation == "empty_procedure":
        data["groups"][0]["procedure"] = ""
    elif mutation == "order":
        checks.reverse()
    data["checks"] = tuple(checks)
    with pytest.raises(ValidationError):
        Manifest.model_validate(data)
