#!/usr/bin/env python3
"""Evaluate observable diagnostic-observation behavior against labeled turns.

Responses are adapter-normalized public tool/assistant outcomes.  This harness
does not inspect private reasoning or claim that a causal assertion is proven;
it compares only the stable labels supplied by the reviewed scenario dataset.
"""

from __future__ import annotations

import argparse
import importlib
import json
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

MODULE_ROOT = Path(__file__).resolve().parent
if str(MODULE_ROOT) not in sys.path:
    sys.path.insert(0, str(MODULE_ROOT))

from validate_dataset import (  # noqa: E402
    DATASET_SCHEMA_VERSION,
    DatasetValidationError,
    load_dataset as _load_dataset,
    validate_dataset,
)

RESPONSE_SCHEMA_VERSION = "bike_doc_diagnostic_observation_response.v1"
RESULT_SCHEMA_VERSION = "bike_doc_diagnostic_observation_result.v1"
BASELINE_SCHEMA_VERSION = "bike_doc_diagnostic_observation_baseline.v1"
REQUIRED_CONFIGURATION_KEYS = (
    "diagnostic_prompt_version",
    "diagnostic_model_version",
    "report_schema_version",
    "tool_contract_version",
)
REQUIRED_METRICS = (
    "premature_report_rate",
    "causal_overreach_rate",
    "required_finding_communication_rate",
    "contributing_factor_recall",
    "alternate_hypothesis_correctness",
    "follow_up_usefulness",
    "report_finding_retention_rate",
    "safety_critical_miss_rate",
    "unnecessary_follow_up_rate",
    "confidence_calibration",
)
PROMOTION_BLOCKING_METRICS = {
    "premature_report_rate",
    "causal_overreach_rate",
    "safety_critical_miss_rate",
}
FAILURE_RATE_METRICS = {
    "premature_report_rate",
    "causal_overreach_rate",
    "safety_critical_miss_rate",
    "unnecessary_follow_up_rate",
}


class EvaluationInputError(ValueError):
    """A response artifact, baseline, or executor result cannot be evaluated."""


@dataclass(frozen=True, slots=True)
class EvaluationRequest:
    """One ordered scenario turn passed to a production-equivalent adapter."""

    case_id: str
    turn_order: int
    user_input: dict[str, Any]
    prior_responses: tuple[dict[str, Any], ...]


Executor = Callable[[EvaluationRequest], dict[str, Any]]


def load_dataset(path: Path) -> dict[str, Any]:
    """Load and validate the reviewed scenario dataset before any execution."""
    try:
        dataset = _load_dataset(path)
        validate_dataset(dataset)
    except DatasetValidationError as exc:
        raise EvaluationInputError(str(exc)) from exc
    return dataset


def _load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise EvaluationInputError(f"could not read JSON {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise EvaluationInputError(f"{path} must contain a JSON object")
    return value


def _validate_configuration(value: Any) -> dict[str, str]:
    if not isinstance(value, dict) or any(
        not isinstance(value.get(key), str) or not value[key].strip()
        for key in REQUIRED_CONFIGURATION_KEYS
    ):
        raise EvaluationInputError(
            "evaluated_configuration requires prompt, model, report schema, and tool "
            "contract versions"
        )
    return {key: value[key] for key in REQUIRED_CONFIGURATION_KEYS}


def _string_list(value: Any, field: str) -> list[str]:
    if not isinstance(value, list) or any(
        not isinstance(item, str) or not item for item in value
    ):
        raise EvaluationInputError(f"{field} must be a list of non-empty strings")
    if len(value) != len(set(value)):
        raise EvaluationInputError(f"{field} must not contain duplicates")
    return value


def _parse_response(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise EvaluationInputError("response must be an object")
    case_id, turn_order = value.get("case_id"), value.get("turn_order")
    if not isinstance(case_id, str) or not case_id:
        raise EvaluationInputError("case_id must be a non-empty string")
    if not isinstance(turn_order, int) or turn_order < 1:
        raise EvaluationInputError("turn_order must be a positive integer")
    parsed = {
        "case_id": case_id,
        "turn_order": turn_order,
        "communicated_findings": _string_list(
            value.get("communicated_findings"), "communicated_findings"
        ),
        "causal_assertions": _string_list(
            value.get("causal_assertions"), "causal_assertions"
        ),
        "contributing_factors": _string_list(
            value.get("contributing_factors"), "contributing_factors"
        ),
        "alternate_hypotheses": _string_list(
            value.get("alternate_hypotheses"), "alternate_hypotheses"
        ),
    }
    primary, follow_up = value.get("primary_diagnosis"), value.get("follow_up_request")
    if primary is not None and (not isinstance(primary, str) or not primary):
        raise EvaluationInputError("primary_diagnosis must be a non-empty string or null")
    if follow_up is not None and (not isinstance(follow_up, str) or not follow_up):
        raise EvaluationInputError("follow_up_request must be a non-empty string or null")
    parsed["primary_diagnosis"] = primary
    parsed["follow_up_request"] = follow_up
    confidence = value.get("confidence")
    if confidence is not None and confidence not in {"low", "medium", "high"}:
        raise EvaluationInputError("confidence must be low, medium, high, or null")
    parsed["confidence"] = confidence
    safety = value.get("safety")
    if not isinstance(safety, dict) or not all(
        isinstance(safety.get(field), bool)
        for field in ("must_raise_safety_flag", "requires_in_person_assessment")
    ):
        raise EvaluationInputError("safety requires boolean behavior fields")
    parsed["safety"] = safety
    report = value.get("report")
    if report is not None:
        if not isinstance(report, dict):
            raise EvaluationInputError("report must be an object or null")
        outcome = report.get("completion_outcome")
        if not isinstance(outcome, str) or not outcome:
            raise EvaluationInputError("report completion_outcome must be a non-empty string")
        parsed["report"] = {
            "completion_outcome": outcome,
            "finding_ids": _string_list(report.get("finding_ids"), "report finding_ids"),
        }
    else:
        parsed["report"] = None
    return parsed


def _metric(value: float | None, numerator: int, denominator: int) -> dict[str, Any]:
    return {"value": value, "numerator": numerator, "denominator": denominator}


def _rate(numerator: int, denominator: int) -> float | None:
    return round(numerator / denominator, 6) if denominator else None


def _expected_turns(dataset: dict[str, Any]) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    return [(case, turn) for case in dataset["cases"] for turn in case["turns"]]


def evaluate_responses(dataset: dict[str, Any], document: dict[str, Any]) -> dict[str, Any]:
    """Parse a fixed response set and retain evidence for every scored turn."""
    validate_dataset(dataset)
    if document.get("response_schema_version") != RESPONSE_SCHEMA_VERSION:
        raise EvaluationInputError(f"response_schema_version must be {RESPONSE_SCHEMA_VERSION!r}")
    configuration = _validate_configuration(document.get("evaluated_configuration"))
    raw_responses = document.get("responses")
    if not isinstance(raw_responses, list):
        raise EvaluationInputError("responses must be a list")
    by_key: dict[tuple[str, int], Any] = {}
    for response in raw_responses:
        if isinstance(response, dict) and isinstance(response.get("case_id"), str) and isinstance(response.get("turn_order"), int):
            key = (response["case_id"], response["turn_order"])
            if key in by_key:
                raise EvaluationInputError(f"duplicate response for {key[0]} turn {key[1]}")
            by_key[key] = response
        else:
            raise EvaluationInputError("each response requires case_id and turn_order")

    counts = {name: [0, 0] for name in REQUIRED_METRICS}
    turn_results: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    for case, turn in _expected_turns(dataset):
        expected = turn["expected"]
        case_id, order = case["id"], turn["order"]
        raw = by_key.pop((case_id, order), None)
        try:
            if raw is None:
                raise EvaluationInputError("response is missing")
            response = _parse_response(raw)
        except EvaluationInputError as exc:
            failures.append({"case_id": case_id, "turn_order": order, "error": str(exc)})
            turn_results.append(_failed_turn_result(case_id, order, expected, str(exc)))
            continue
        result = _score_turn(case, turn, response)
        turn_results.append(result)
        for name, passed in result["metrics"].items():
            if passed is not None:
                counts[name][1] += 1
                counts[name][0] += int(not passed) if name in FAILURE_RATE_METRICS else int(passed)
    if by_key:
        unknown = sorted(f"{case_id} turn {order}" for case_id, order in by_key)
        raise EvaluationInputError("responses reference unknown dataset turns: " + ", ".join(unknown))
    metrics = {
        name: _metric(_rate(*counts[name]), *counts[name]) for name in REQUIRED_METRICS
    }
    return {
        "result_schema_version": RESULT_SCHEMA_VERSION,
        "dataset_schema_version": DATASET_SCHEMA_VERSION,
        "dataset_version": dataset["dataset_version"],
        "evaluated_configuration": configuration,
        "metrics": metrics,
        "turn_results": turn_results,
        "evaluation_failures": failures,
    }


def _failed_turn_result(case_id: str, order: int, expected: dict[str, Any], error: str) -> dict[str, Any]:
    metrics = {name: None for name in REQUIRED_METRICS}
    for name in (
        "required_finding_communication_rate",
        "contributing_factor_recall",
        "alternate_hypothesis_correctness",
        "follow_up_usefulness",
        "report_finding_retention_rate",
    ):
        metrics[name] = False
    if not expected["save_diagnostic_report_permitted"]:
        metrics["premature_report_rate"] = False
    if expected["forbidden_causal_assertions"]:
        metrics["causal_overreach_rate"] = False
    if any(expected["required_safety_behavior"].values()):
        metrics["safety_critical_miss_rate"] = False
    if "straightforward_single_cause_one_turn" in expected.get("scenario_tags", []):
        metrics["unnecessary_follow_up_rate"] = False
    return {
        "case_id": case_id,
        "turn_order": order,
        "status": "evaluation_error",
        "error": error,
        "evidence": {"expected": expected, "response": None},
        "metrics": metrics,
    }


def _score_turn(case: dict[str, Any], turn: dict[str, Any], response: dict[str, Any]) -> dict[str, Any]:
    expected = turn["expected"]
    required_findings = set(expected["findings_to_communicate"])
    forbidden = set(expected["forbidden_causal_assertions"])
    expected_contributors = set(expected["expected_contributing_factors"])
    expected_alternates = set(expected["meaningful_alternate_hypotheses"])
    report = response["report"]
    metrics: dict[str, bool | None] = {name: None for name in REQUIRED_METRICS}
    if not expected["save_diagnostic_report_permitted"]:
        metrics["premature_report_rate"] = report is None
    if forbidden:
        metrics["causal_overreach_rate"] = not bool(forbidden & set(response["causal_assertions"]))
    metrics["required_finding_communication_rate"] = required_findings <= set(response["communicated_findings"])
    if expected_contributors:
        metrics["contributing_factor_recall"] = expected_contributors <= set(response["contributing_factors"])
    metrics["alternate_hypothesis_correctness"] = set(response["alternate_hypotheses"]) == expected_alternates
    if expected["acceptable_follow_up_requests"]:
        metrics["follow_up_usefulness"] = response["follow_up_request"] in set(expected["acceptable_follow_up_requests"])
    if expected["save_diagnostic_report_permitted"]:
        retained = set(report["finding_ids"]) if report else set()
        metrics["report_finding_retention_rate"] = set(expected["findings_retained_in_report"]) <= retained
    safety_expected = expected["required_safety_behavior"]
    if any(safety_expected.values()):
        metrics["safety_critical_miss_rate"] = all(
            not required or response["safety"][field]
            for field, required in safety_expected.items()
        )
    if "straightforward_single_cause_one_turn" in case["scenario_tags"]:
        metrics["unnecessary_follow_up_rate"] = response["follow_up_request"] is None
    acceptable_confidences = expected.get("acceptable_confidences")
    if acceptable_confidences is not None:
        if not isinstance(acceptable_confidences, list) or any(
            confidence not in {"low", "medium", "high"}
            for confidence in acceptable_confidences
        ):
            raise EvaluationInputError(
                f"{case['id']} turn {turn['order']} has invalid acceptable_confidences"
            )
        metrics["confidence_calibration"] = response["confidence"] in set(
            acceptable_confidences
        )
    return {
        "case_id": case["id"],
        "turn_order": turn["order"],
        "status": "scored",
        "evidence": {"expected": expected, "response": response},
        "metrics": metrics,
    }


def run_dataset(dataset: dict[str, Any], executor: Executor, *, configuration: dict[str, str]) -> dict[str, Any]:
    """Execute ordered turns via an injected production-equivalent adapter."""
    responses: list[dict[str, Any]] = []
    for case in dataset["cases"]:
        prior: list[dict[str, Any]] = []
        for turn in case["turns"]:
            response = executor(EvaluationRequest(case["id"], turn["order"], turn["user_input"], tuple(prior)))
            prior.append(response)
            responses.append(response)
    return evaluate_responses(dataset, {"response_schema_version": RESPONSE_SCHEMA_VERSION, "evaluated_configuration": configuration, "responses": responses})


def make_baseline(report: dict[str, Any], *, reviewed_by: str) -> dict[str, Any]:
    if not reviewed_by.strip():
        raise EvaluationInputError("an accepted baseline requires a named reviewer")
    return {
        "baseline_schema_version": BASELINE_SCHEMA_VERSION,
        "dataset_version": report["dataset_version"],
        "evaluated_configuration": report["evaluated_configuration"],
        "metrics": report["metrics"],
        "accepted_review": {"reviewed_by": reviewed_by, "status": "accepted"},
    }


def compare_baseline(previous: dict[str, Any] | None, report: dict[str, Any]) -> dict[str, Any]:
    if previous is None:
        return {"status": "missing_prior_baseline", "promotion_allowed": False, "blocking_regressions": [], "non_blocking_changes": []}
    if previous.get("baseline_schema_version") != BASELINE_SCHEMA_VERSION:
        raise EvaluationInputError("baseline uses an unsupported schema version")
    _validate_configuration(previous.get("evaluated_configuration"))
    blocking: list[str] = []
    non_blocking: list[str] = []
    lower_is_better = {"premature_report_rate", "causal_overreach_rate", "safety_critical_miss_rate", "unnecessary_follow_up_rate"}
    for name in REQUIRED_METRICS:
        before = previous.get("metrics", {}).get(name, {}).get("value")
        after = report["metrics"][name]["value"]
        if before == after:
            continue
        if isinstance(before, (int, float)) and isinstance(after, (int, float)):
            regression = after > before if name in lower_is_better else after < before
            if regression and name in PROMOTION_BLOCKING_METRICS:
                blocking.append(name)
            else:
                non_blocking.append(name)
        else:
            non_blocking.append(name)
    return {
        "status": "promotion_blocked" if blocking else "review_required",
        "promotion_allowed": not blocking,
        "blocking_regressions": sorted(blocking),
        "non_blocking_changes": sorted(non_blocking),
        "configuration_changed": previous.get("evaluated_configuration") != report["evaluated_configuration"],
    }


def _executor(reference: str) -> Executor:
    module, separator, attribute = reference.partition(":")
    if not separator:
        raise EvaluationInputError("executor must be module:function")
    callback = getattr(importlib.import_module(module), attribute, None)
    if not callable(callback):
        raise EvaluationInputError("executor must resolve to a callable")
    return callback


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, required=True)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--responses", type=Path)
    source.add_argument("--executor", help="module:function receiving EvaluationRequest")
    parser.add_argument("--configuration", type=Path, help="required with --executor")
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--accept-baseline", action="store_true")
    parser.add_argument("--reviewed-by", default="")
    args = parser.parse_args()
    try:
        dataset = load_dataset(args.dataset)
        if args.responses:
            report = evaluate_responses(dataset, _load_json(args.responses))
        else:
            if args.configuration is None:
                raise EvaluationInputError("--configuration is required with --executor")
            report = run_dataset(dataset, _executor(args.executor), configuration=_load_json(args.configuration))
        previous = _load_json(args.baseline) if args.baseline.exists() else None
        report["baseline_comparison"] = compare_baseline(previous, report)
        args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
        if args.accept_baseline:
            if report["baseline_comparison"]["blocking_regressions"]:
                raise EvaluationInputError("cannot accept a baseline with promotion-blocking regressions")
            args.baseline.write_text(json.dumps(make_baseline(report, reviewed_by=args.reviewed_by), indent=2, sort_keys=True) + "\n")
    except EvaluationInputError as exc:
        parser.error(str(exc))
    print(f"Evaluated {len(report['turn_results'])} turns")
    print(f"Promotion check: {report['baseline_comparison']['status']}")
    return 1 if report["baseline_comparison"]["blocking_regressions"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
