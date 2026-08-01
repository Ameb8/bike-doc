"""Contract tests for the diagnostic-observation-handling dataset."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

MODULE_PATH = Path(__file__).resolve().parents[1] / "validate_dataset.py"
SPEC = importlib.util.spec_from_file_location(
    "diagnostic_observation_dataset", MODULE_PATH
)
assert SPEC and SPEC.loader
dataset_validator = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = dataset_validator
SPEC.loader.exec_module(dataset_validator)


def _case() -> dict[str, object]:
    return {
        "id": "valid_case",
        "scenario_tags": sorted(dataset_validator.REQUIRED_SCENARIO_TAGS),
        "complaint_cluster": {
            "id": "drivetrain",
            "symptoms": ["Chain skips under load"],
        },
        "labels": {
            "findings": [{"id": "chain_discoloration", "source": "image"}],
            "diagnoses": ["chain_wear"],
            "contributors": ["indexing_error"],
            "alternates": ["freehub_fault"],
            "follow_up_requests": ["chain_wear_measurement"],
        },
        "turns": [
            {
                "order": 1,
                "user_input": {"message": "My chain skips under load."},
                "expected": {
                    "findings_to_communicate": ["chain_discoloration"],
                    "forbidden_causal_assertions": ["chain_discoloration"],
                    "acceptable_primary_diagnoses": [],
                    "expected_contributing_factors": [],
                    "meaningful_alternate_hypotheses": ["freehub_fault"],
                    "acceptable_follow_up_requests": ["chain_wear_measurement"],
                    "save_diagnostic_report_permitted": False,
                    "required_safety_behavior": {
                        "must_raise_safety_flag": False,
                        "requires_in_person_assessment": False,
                    },
                    "findings_retained_in_report": ["chain_discoloration"],
                    "allowed_completion_outcomes": [],
                },
            },
            {
                "order": 2,
                "user_input": {"message": "The checker says the chain is worn."},
                "expected": {
                    "findings_to_communicate": ["chain_discoloration"],
                    "forbidden_causal_assertions": [],
                    "acceptable_primary_diagnoses": ["chain_wear"],
                    "expected_contributing_factors": ["indexing_error"],
                    "meaningful_alternate_hypotheses": ["freehub_fault"],
                    "acceptable_follow_up_requests": [],
                    "save_diagnostic_report_permitted": True,
                    "required_safety_behavior": {
                        "must_raise_safety_flag": False,
                        "requires_in_person_assessment": False,
                    },
                    "findings_retained_in_report": ["chain_discoloration"],
                    "allowed_completion_outcomes": ["diagnosis_supported"],
                },
            },
        ],
    }


def _dataset() -> dict[str, object]:
    return {
        "schema_version": dataset_validator.DATASET_SCHEMA_VERSION,
        "dataset_version": "v1.0.0",
        "cases": [_case()],
    }


def test_validator_loads_every_committed_case_in_order_with_labels() -> None:
    dataset = dataset_validator.load_dataset(
        Path(__file__).resolve().parents[1] / "dataset.json"
    )

    cases = dataset_validator.validate_dataset(dataset)

    assert [case["id"] for case in cases] == [case["id"] for case in dataset["cases"]]
    assert len(cases) >= 14
    assert all(turn["expected"] for case in cases for turn in case["turns"])


def test_validator_rejects_missing_required_labels() -> None:
    dataset = _dataset()
    expected = dataset["cases"][0]["turns"][0]["expected"]
    del expected["findings_retained_in_report"]

    with pytest.raises(
        dataset_validator.DatasetValidationError, match="findings_retained_in_report"
    ):
        dataset_validator.validate_dataset(dataset)


def test_validator_rejects_duplicate_ids_and_invalid_turn_ordering() -> None:
    dataset = _dataset()
    duplicate = json.loads(json.dumps(dataset["cases"][0]))
    duplicate["turns"][1]["order"] = 3
    dataset["cases"].append(duplicate)

    with pytest.raises(
        dataset_validator.DatasetValidationError, match="duplicate case id"
    ):
        dataset_validator.validate_dataset(dataset)

    dataset["cases"].pop()
    dataset["cases"][0]["turns"][1]["order"] = 3
    with pytest.raises(dataset_validator.DatasetValidationError, match="turn order"):
        dataset_validator.validate_dataset(dataset)


def test_validator_rejects_inconsistent_completion_and_unknown_label_references() -> (
    None
):
    dataset = _dataset()
    expected = dataset["cases"][0]["turns"][0]["expected"]
    expected["save_diagnostic_report_permitted"] = True
    with pytest.raises(
        dataset_validator.DatasetValidationError, match="completion outcomes"
    ):
        dataset_validator.validate_dataset(dataset)

    dataset = _dataset()
    dataset["cases"][0]["turns"][0]["expected"]["findings_to_communicate"] = ["missing"]
    with pytest.raises(
        dataset_validator.DatasetValidationError, match="unknown finding label"
    ):
        dataset_validator.validate_dataset(dataset)


def test_validator_rejects_unknown_evidence_sources_and_missing_outcome_coverage() -> (
    None
):
    dataset = _dataset()
    dataset["cases"][0]["scenario_tags"] = sorted(
        dataset_validator.REQUIRED_SCENARIO_TAGS
    )
    dataset["cases"][0]["labels"]["findings"][0]["source"] = "model_score"
    with pytest.raises(
        dataset_validator.DatasetValidationError, match="invalid evidence source"
    ):
        dataset_validator.validate_dataset(dataset)

    dataset["cases"][0]["labels"]["findings"][0]["source"] = "image"
    with pytest.raises(
        dataset_validator.DatasetValidationError, match="completion outcome coverage"
    ):
        dataset_validator.validate_dataset(dataset)
