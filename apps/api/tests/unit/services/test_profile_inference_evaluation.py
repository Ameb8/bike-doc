"""Deterministic tests for the positioned-brake evaluation boundary."""

from __future__ import annotations

from pathlib import Path

import pytest

from bike_doc_api.services.profile_inference_evaluation import (
    compare_reports,
    evaluate_dataset,
    load_json,
    load_predictions,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[5]
DATASET_PATH = (
    REPOSITORY_ROOT
    / "evals"
    / "bike-doc"
    / "profile-inference"
    / "rear-brake-shadow-v1.json"
)
PREDICTIONS_PATH = DATASET_PATH.with_name("rear-brake-shadow-v1.responses.json")
ROLLING_DATASET_PATH = (
    REPOSITORY_ROOT
    / "evals"
    / "bike-doc"
    / "profile-inference"
    / "rolling-system-v1.json"
)
ROLLING_PREDICTIONS_PATH = ROLLING_DATASET_PATH.with_name(
    "rolling-system-v1.responses.json",
)
DRIVETRAIN_DATASET_PATH = ROLLING_DATASET_PATH.with_name(
    "drivetrain-topology-v1.json",
)
DRIVETRAIN_PREDICTIONS_PATH = DRIVETRAIN_DATASET_PATH.with_name(
    "drivetrain-topology-v1.responses.json",
)
DRIVETRAIN_ROLES_DATASET_PATH = ROLLING_DATASET_PATH.with_name(
    "drivetrain-roles-v1.json",
)
DRIVETRAIN_ROLES_PREDICTIONS_PATH = DRIVETRAIN_ROLES_DATASET_PATH.with_name(
    "drivetrain-roles-v1.responses.json",
)


@pytest.mark.asyncio
async def test_evaluation_reports_field_evidence_and_resolution_quality() -> None:
    dataset = load_json(DATASET_PATH)
    predictions = load_predictions(load_json(PREDICTIONS_PATH))

    report = await evaluate_dataset(dataset, predictions)

    assert report["case_count"] == 14
    assert {
        (item["field_path"], item["evidence_class"])
        for item in report["field_evidence_metrics"]
    } == {
        ("brakes.front.mechanism", "direct_visual"),
        ("brakes.front.actuation", "direct_visual"),
        ("brakes.rear.mechanism", "direct_visual"),
        ("brakes.rear.actuation", "direct_visual"),
    }
    assert report["metrics"]["conflict_accuracy"] == 1.0
    assert report["metrics"]["auto_fill_precision"] == 0.888889
    assert report["metrics"]["auto_fill_coverage"] == 1.0
    assert report["metrics"]["auto_overwrite_coverage"] == 1.0
    assert report["metrics"]["profile_corruption_failures"] == {"position": 1}
    assert report["metrics"]["expected_profile_corruption_failures"] == {
        "installedness": 4,
        "position": 1,
    }
    position_case = next(
        case
        for case in report["cases"]
        if case["case_id"] == "position_error_profile_corruption"
    )
    assert position_case["actions"] == {"brakes.rear.mechanism": "applied"}
    assert position_case["profile_corruption_failures"] == ["position"]


@pytest.mark.asyncio
async def test_rolling_evaluation_covers_positioned_markings_and_safe_abstention() -> (
    None
):
    report = await evaluate_dataset(
        load_json(ROLLING_DATASET_PATH),
        load_predictions(load_json(ROLLING_PREDICTIONS_PATH)),
    )

    assert report["case_count"] == 6
    assert report["metrics"]["front_rear_position_accuracy"] == 1.0
    assert report["metrics"]["auto_fill_precision"] == 1.0
    assert report["metrics"]["profile_corruption_failures"] == {}
    tubeless_case = next(
        case
        for case in report["cases"]
        if case["case_id"] == "tubeless_ready_but_setup_ambiguous"
    )
    assert tubeless_case["actions"] == {
        "rolling_system.rear.tire.setup": "pending",
        "rolling_system.rear.tire.tubeless_ready": "applied",
    }


@pytest.mark.asyncio
async def test_drivetrain_evaluation_covers_topology_and_installedness() -> None:
    report = await evaluate_dataset(
        load_json(DRIVETRAIN_DATASET_PATH),
        load_predictions(load_json(DRIVETRAIN_PREDICTIONS_PATH)),
    )

    assert report["case_count"] == 5
    assert report["metrics"]["abstention_correctness"] == 1.0
    assert report["metrics"]["installedness_accuracy"] == 1.0
    assert report["metrics"]["auto_fill_precision"] == 1.0
    assert report["metrics"]["profile_corruption_failures"] == {}


@pytest.mark.asyncio
async def test_drivetrain_roles_evaluation_covers_claims_and_abstention() -> None:
    report = await evaluate_dataset(
        load_json(DRIVETRAIN_ROLES_DATASET_PATH),
        load_predictions(load_json(DRIVETRAIN_ROLES_PREDICTIONS_PATH)),
    )

    assert report["case_count"] == 11
    assert report["metrics"]["abstention_correctness"] == 1.0
    assert report["metrics"]["installedness_accuracy"] == 1.0
    assert report["metrics"]["auto_fill_precision"] == 1.0
    assert report["metrics"]["conflict_accuracy"] == 1.0
    assert report["metrics"]["profile_corruption_failures"] == {}
    assert {
        item["field_path"]
        for item in report["field_evidence_metrics"]
        if item["field_path"].startswith("drivetrain.")
    } == {
        "drivetrain.front_shifter.presence",
        "drivetrain.front_shifter.actuation",
        "drivetrain.rear_shifter.presence",
        "drivetrain.rear_shifter.actuation",
        "drivetrain.rear_derailleur.presence",
        "drivetrain.rear_derailleur.mount_type",
        "drivetrain.rear_shifter.manufacturer",
        "drivetrain.rear_shifter.model",
        "drivetrain.rear_cluster.cluster_type",
    }


def test_missing_baseline_is_explicit_and_version_changes_require_comparison() -> None:
    current = {
        "dataset_version": "rear-brake-v1.0.0",
        "versions": {"extractor_version": "rear-brake-shadow.v1"},
        "metrics": {"false_positive_rate": 0.1},
    }

    missing = compare_reports(None, current)
    assert missing["status"] == "missing_prior_baseline"
    assert missing["required"] is True

    previous = {
        "dataset_version": "rear-brake-v1.0.0",
        "versions": {"extractor_version": "rear-brake-shadow.v0"},
        "metrics": {"false_positive_rate": 0.05},
    }
    changed = compare_reports(previous, current)
    assert changed["version_changed"] is True
    assert changed["required"] is True
    assert changed["status"] == "regression_detected"
