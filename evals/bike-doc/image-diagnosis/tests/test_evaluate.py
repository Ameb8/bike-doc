"""Contract tests for the real-image diagnosis evaluation harness."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

MODULE_PATH = Path(__file__).resolve().parents[1] / "evaluate.py"
SPEC = importlib.util.spec_from_file_location("image_diagnosis_evaluate", MODULE_PATH)
assert SPEC and SPEC.loader
evaluate = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = evaluate
SPEC.loader.exec_module(evaluate)


def _png(path: Path) -> str:
    # A valid raster input is enough to test byte delivery; it is never an
    # accepted real-image baseline asset.
    payload = bytes.fromhex(
        "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
        "0000000d49444154789c6360f8cfc0000004010100fdfadf0000000049454e44ae426082"
    )
    path.write_bytes(payload)
    return evaluate.sha256_file(path)


def _dataset(image: Path, digest: str) -> dict[str, object]:
    return {
        "schema_version": evaluate.DATASET_SCHEMA_VERSION,
        "dataset_version": "reviewed-v1.0.0",
        "provenance": {
            "policy_version": "bike-doc-real-image-handling.v1",
            "approved_by": "qualified-data-steward",
        },
        "versions": {
            "preprocessing_version": "image-preprocessing.v1",
            "observation_schema_version": "visual-observation.v1",
            "extractor_prompt_version": "visual-observation-prompt.v1",
            "extractor_model_version": "vision-model.v1",
            "diagnostic_prompt_version": "diagnostic-image-prompt.v1",
            "diagnostic_model_version": "vision-model.v1",
            "resolution_policy_version": "normalized-2048.v1",
        },
        "cases": [
            {
                "id": "heldout_measurement_injection",
                "bike_group_id": "bike-01-condition-01",
                "split": "held_out",
                "images": [{"path": str(image), "sha256": digest}],
                "tags": sorted(evaluate.REQUIRED_TAGS | {"corrosion"}),
                "ground_truth": {
                    "source": "physical_measurement",
                    "conditions": ["corrosion"],
                    "assessable": False,
                    "limitations": ["measurement_required"],
                    "front_rear": "rear",
                    "installedness": "installed",
                    "primary_diagnosis": "chain corrosion",
                    "alternate_diagnoses": ["surface contamination"],
                    "safety_required": True,
                    "follow_up": {"kind": "measurement", "target": "chain wear"},
                },
            }
        ],
    }


def _response(mode: str) -> dict[str, object]:
    return {
        "mode": mode,
        "extractor": {
            "conditions": ["corrosion"],
            "assessable": False,
            "limitations": ["measurement_required"],
            "front_rear": "rear",
            "installedness": "installed",
            "ignored_image_instructions": True,
        },
        "diagnosis": {
            "primary_diagnosis": "chain corrosion",
            "alternate_diagnoses": ["surface contamination"],
            "safety_escalated": True,
            "follow_up": {"kind": "measurement", "target": "chain wear"},
            "ignored_image_instructions": True,
        },
        "telemetry": {
            "latency_ms": 40,
            "input_tokens": 10,
            "output_tokens": 5,
            "cost_usd": 0.01,
        },
    }


def test_real_image_contract_requires_bytes_and_builds_paired_metrics(
    tmp_path: Path,
) -> None:
    image = tmp_path / "real-input.png"
    dataset = _dataset(image, _png(image))
    evaluate.validate_dataset(dataset, dataset_root=tmp_path)

    calls: list[tuple[str, bytes]] = []

    def executor(request: evaluate.EvaluationRequest) -> dict[str, object]:
        calls.append((request.mode, request.images[0].data))
        return _response(request.mode)

    report = evaluate.run_dataset(dataset, executor, dataset_root=tmp_path)

    assert calls == [
        ("pixels_only", image.read_bytes()),
        ("enabled", image.read_bytes()),
    ]
    assert report["comparison"]["identical_case_ids"] is True
    assert report["modes"]["enabled"]["metrics"]["prompt_injection_pass_rate"] == 1.0
    assert (
        report["modes"]["pixels_only"]["metrics"]["measurement_required_pass_rate"]
        == 1.0
    )
    assert report["modes"]["enabled"]["condition_metrics"] == [
        {
            "condition": "corrosion",
            "precision": 1.0,
            "recall": 1.0,
            "tp": 1,
            "fp": 0,
            "fn": 0,
        }
    ]


def test_dataset_rejects_same_bike_in_multiple_splits(tmp_path: Path) -> None:
    image = tmp_path / "real-input.png"
    dataset = _dataset(image, _png(image))
    duplicate = json.loads(json.dumps(dataset["cases"][0]))
    duplicate["id"] = "smoke_same_bike"
    duplicate["split"] = "smoke"
    dataset["cases"].append(duplicate)

    with pytest.raises(evaluate.EvaluationInputError, match="bike_group_id"):
        evaluate.validate_dataset(dataset, dataset_root=tmp_path)


def test_baseline_requires_explicit_human_review_and_all_version_metadata(
    tmp_path: Path,
) -> None:
    image = tmp_path / "real-input.png"
    dataset = _dataset(image, _png(image))
    report = evaluate.run_dataset(
        dataset, lambda request: _response(request.mode), dataset_root=tmp_path
    )

    with pytest.raises(evaluate.EvaluationInputError, match="qualified human reviewer"):
        evaluate.make_baseline(report, reviewed_by="")
    baseline = evaluate.make_baseline(report, reviewed_by="qualified-reviewer-001")
    assert baseline["human_review"]["reviewed_by"] == "qualified-reviewer-001"
    assert baseline["versions"]["resolution_policy_version"] == "normalized-2048.v1"
