"""Behavioral contracts for the diagnostic-observation evaluation seam."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

MODULE_PATH = Path(__file__).resolve().parents[1] / "evaluate.py"
SPEC = importlib.util.spec_from_file_location("diagnostic_observation_evaluate", MODULE_PATH)
assert SPEC and SPEC.loader
evaluate = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = evaluate
SPEC.loader.exec_module(evaluate)

DATASET_PATH = Path(__file__).resolve().parents[1] / "dataset.json"


def _configuration() -> dict[str, str]:
    return {
        "diagnostic_prompt_version": "diagnostic-observation.v1",
        "diagnostic_model_version": "fixture-model.v1",
        "report_schema_version": "diagnostic_report.v2",
        "tool_contract_version": "save-diagnostic-report.v2",
    }


def _perfect_document() -> dict[str, object]:
    dataset = json.loads(DATASET_PATH.read_text())
    responses: list[dict[str, object]] = []
    for case in dataset["cases"]:
        for turn in case["turns"]:
            expected = turn["expected"]
            report = None
            if expected["save_diagnostic_report_permitted"]:
                report = {
                    "completion_outcome": expected["allowed_completion_outcomes"][0],
                    "finding_ids": expected["findings_retained_in_report"],
                }
            responses.append(
                {
                    "case_id": case["id"],
                    "turn_order": turn["order"],
                    "communicated_findings": expected["findings_to_communicate"],
                    "causal_assertions": [],
                    "primary_diagnosis": (
                        expected["acceptable_primary_diagnoses"][0]
                        if expected["acceptable_primary_diagnoses"]
                        else None
                    ),
                    "contributing_factors": expected["expected_contributing_factors"],
                    "alternate_hypotheses": expected[
                        "meaningful_alternate_hypotheses"
                    ],
                    "follow_up_request": (
                        expected["acceptable_follow_up_requests"][0]
                        if expected["acceptable_follow_up_requests"]
                        else None
                    ),
                    "report": report,
                    "safety": expected["required_safety_behavior"],
                }
            )
    return {
        "response_schema_version": evaluate.RESPONSE_SCHEMA_VERSION,
        "evaluated_configuration": _configuration(),
        "responses": responses,
    }


def test_fixture_responses_emit_every_required_metric_and_turn_evidence() -> None:
    report = evaluate.evaluate_responses(evaluate.load_dataset(DATASET_PATH), _perfect_document())

    metrics = report["metrics"]
    assert set(metrics) == set(evaluate.REQUIRED_METRICS)
    assert metrics["premature_report_rate"]["value"] == 0.0
    assert metrics["causal_overreach_rate"]["value"] == 0.0
    assert metrics["safety_critical_miss_rate"]["value"] == 0.0
    assert metrics["unnecessary_follow_up_rate"]["value"] == 0.0
    assert metrics["required_finding_communication_rate"]["value"] == 1.0
    assert metrics["contributing_factor_recall"]["value"] == 1.0
    assert metrics["alternate_hypothesis_correctness"]["value"] == 1.0
    assert metrics["follow_up_usefulness"]["value"] == 1.0
    assert metrics["report_finding_retention_rate"]["value"] == 1.0
    assert metrics["confidence_calibration"]["value"] is None
    assert report["evaluation_failures"] == []
    assert len(report["turn_results"]) == sum(
        len(case["turns"]) for case in evaluate.load_dataset(DATASET_PATH)["cases"]
    )
    assert all("evidence" in result for result in report["turn_results"])


def test_malformed_response_is_visible_and_never_counted_as_a_success() -> None:
    document = _perfect_document()
    document["responses"][0]["communicated_findings"] = "not-a-list"

    report = evaluate.evaluate_responses(evaluate.load_dataset(DATASET_PATH), document)

    assert report["evaluation_failures"] == [
        {
            "case_id": "corrosion-and-indexing-acceptance-flow",
            "turn_order": 1,
            "error": "communicated_findings must be a list of non-empty strings",
        }
    ]
    failed = report["turn_results"][0]
    assert failed["status"] == "evaluation_error"
    assert failed["metrics"]["required_finding_communication_rate"] is False


def test_executor_runs_ordered_turns_with_prior_turn_context() -> None:
    document = _perfect_document()
    by_key = {
        (response["case_id"], response["turn_order"]): response
        for response in document["responses"]
    }
    requests: list[evaluate.EvaluationRequest] = []

    def executor(request: evaluate.EvaluationRequest) -> dict[str, object]:
        requests.append(request)
        return by_key[(request.case_id, request.turn_order)]

    report = evaluate.run_dataset(
        evaluate.load_dataset(DATASET_PATH), executor, configuration=_configuration()
    )

    acceptance_second_turn = next(
        request
        for request in requests
        if request.case_id == "corrosion-and-indexing-acceptance-flow"
        and request.turn_order == 2
    )
    assert len(acceptance_second_turn.prior_responses) == 1
    assert report["evaluation_failures"] == []


def test_confidence_calibration_is_scored_only_when_a_reviewed_label_exists() -> None:
    dataset = evaluate.load_dataset(DATASET_PATH)
    dataset["cases"][0]["turns"][1]["expected"]["acceptable_confidences"] = [
        "medium"
    ]
    document = _perfect_document()
    document["responses"][1]["confidence"] = "medium"

    report = evaluate.evaluate_responses(dataset, document)

    assert report["metrics"]["confidence_calibration"] == {
        "value": 1.0,
        "numerator": 1,
        "denominator": 1,
    }


@pytest.mark.parametrize(
    ("metric", "change"),
    [
        (
            "premature_report_rate",
            lambda responses: responses[0].update(
                report={
                    "completion_outcome": "diagnosis_supported",
                    "finding_ids": ["chain_corrosion"],
                }
            ),
        ),
        (
            "causal_overreach_rate",
            lambda responses: responses[0].update(causal_assertions=["chain_corrosion"]),
        ),
        (
            "safety_critical_miss_rate",
            lambda responses: next(
                response
                for response in responses
                if response["case_id"] == "dramatic-tire-crack-unrelated-to-shifting"
            ).update(safety={"must_raise_safety_flag": False, "requires_in_person_assessment": False}),
        ),
    ],
)
def test_blocking_regressions_fail_promotion_while_other_changes_stay_visible(
    metric: str, change: object
) -> None:
    baseline_report = evaluate.evaluate_responses(
        evaluate.load_dataset(DATASET_PATH), _perfect_document()
    )
    baseline = evaluate.make_baseline(baseline_report, reviewed_by="reviewer-001")

    regressed = _perfect_document()
    assert callable(change)
    change(regressed["responses"])
    report = evaluate.evaluate_responses(evaluate.load_dataset(DATASET_PATH), regressed)
    comparison = evaluate.compare_baseline(baseline, report)

    assert comparison["promotion_allowed"] is False
    assert comparison["blocking_regressions"] == [metric]

    non_blocking = _perfect_document()
    straightforward = next(
        response
        for response in non_blocking["responses"]
        if response["case_id"] == "straightforward-loose-pedal-one-turn"
    )
    straightforward["follow_up_request"] = "unneeded_extra_question"
    non_blocking_report = evaluate.evaluate_responses(
        evaluate.load_dataset(DATASET_PATH), non_blocking
    )
    visible = evaluate.compare_baseline(baseline, non_blocking_report)
    assert visible["promotion_allowed"] is True
    assert "unnecessary_follow_up_rate" in visible["non_blocking_changes"]


def test_baseline_comparison_is_deterministic_and_requires_configuration_metadata() -> None:
    report = evaluate.evaluate_responses(evaluate.load_dataset(DATASET_PATH), _perfect_document())
    baseline = evaluate.make_baseline(report, reviewed_by="reviewer-001")

    assert baseline["evaluated_configuration"] == _configuration()
    assert evaluate.compare_baseline(baseline, report) == evaluate.compare_baseline(
        baseline, report
    )

    missing_config = _perfect_document()
    del missing_config["evaluated_configuration"]["diagnostic_model_version"]
    with pytest.raises(evaluate.EvaluationInputError, match="evaluated_configuration"):
        evaluate.evaluate_responses(evaluate.load_dataset(DATASET_PATH), missing_config)
