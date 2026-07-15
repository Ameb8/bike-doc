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
