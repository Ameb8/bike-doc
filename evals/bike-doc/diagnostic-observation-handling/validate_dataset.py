#!/usr/bin/env python3
"""Validate the inspectable multi-turn diagnostic-observation dataset."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

DATASET_SCHEMA_VERSION = "bike_doc_diagnostic_observation_eval.v1"
REQUIRED_SCENARIO_TAGS = {
    "corroded_chain_primary_cause",
    "corroded_chain_incidental_to_cassette_wear",
    "corrosion_or_stiff_links_and_indexing_contribute",
    "clean_drivetrain_measurement_confirmed_chain_wear",
    "dramatic_condition_does_not_match_symptom",
    "related_symptoms_one_complaint_cluster",
    "unrelated_clusters_presented_initially",
    "unrelated_cluster_introduced_later",
    "later_measurement_contradicts_working_hypothesis",
    "two_independent_conditions_contribute_to_one_symptom",
    "straightforward_single_cause_one_turn",
    "user_declines_further_evidence",
    "requested_input_unavailable",
    "in_person_assessment_required",
    "section_13_4_initial_acceptance",
    "section_13_4_later_acceptance",
}
REQUIRED_EXPECTED_LABELS = {
    "findings_to_communicate",
    "forbidden_causal_assertions",
    "acceptable_primary_diagnoses",
    "expected_contributing_factors",
    "meaningful_alternate_hypotheses",
    "acceptable_follow_up_requests",
    "save_diagnostic_report_permitted",
    "required_safety_behavior",
    "findings_retained_in_report",
    "allowed_completion_outcomes",
}
LABEL_CATALOGS = {
    "findings_to_communicate": "findings",
    "forbidden_causal_assertions": "findings",
    "findings_retained_in_report": "findings",
    "acceptable_primary_diagnoses": "diagnoses",
    "expected_contributing_factors": "contributors",
    "meaningful_alternate_hypotheses": "alternates",
    "acceptable_follow_up_requests": "follow_up_requests",
}
COMPLETION_OUTCOMES = {
    "diagnosis_supported",
    "user_declined_more_input",
    "requested_input_unavailable",
    "in_person_assessment_required",
}
REQUIRED_LIMITED_COMPLETION_OUTCOMES = COMPLETION_OUTCOMES - {"diagnosis_supported"}
EVIDENCE_SOURCES = {
    "image",
    "user_report",
    "measurement",
    "functional_check",
    "repair_history",
    "other",
}


class DatasetValidationError(ValueError):
    """Raised when a fixture cannot be evaluated safely or consistently."""


def load_dataset(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise DatasetValidationError(f"could not load dataset {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise DatasetValidationError("dataset root must be an object")
    return payload


def validate_dataset(dataset: dict[str, Any]) -> list[dict[str, Any]]:
    if dataset.get("schema_version") != DATASET_SCHEMA_VERSION:
        raise DatasetValidationError(
            f"schema_version must be {DATASET_SCHEMA_VERSION!r}"
        )
    if (
        not isinstance(dataset.get("dataset_version"), str)
        or not dataset["dataset_version"]
    ):
        raise DatasetValidationError("dataset_version is required")
    cases = dataset.get("cases")
    if not isinstance(cases, list) or not cases:
        raise DatasetValidationError("cases must be a non-empty list")

    seen_ids: set[str] = set()
    seen_scenarios: set[str] = set()
    seen_outcomes: set[str] = set()
    for case in cases:
        if not isinstance(case, dict):
            raise DatasetValidationError("each case must be an object")
        case_id = case.get("id")
        if not isinstance(case_id, str) or not case_id:
            raise DatasetValidationError("each case requires a non-empty id")
        if case_id in seen_ids:
            raise DatasetValidationError(f"duplicate case id: {case_id}")
        seen_ids.add(case_id)
        seen_scenarios.update(_string_list(case, "scenario_tags", case_id))
        seen_outcomes.update(_validate_case(case, case_id))

    missing = REQUIRED_SCENARIO_TAGS - seen_scenarios
    if missing:
        raise DatasetValidationError(
            "dataset lacks required scenario coverage: " + ", ".join(sorted(missing))
        )
    missing_outcomes = REQUIRED_LIMITED_COMPLETION_OUTCOMES - seen_outcomes
    if missing_outcomes:
        raise DatasetValidationError(
            "dataset lacks required completion outcome coverage: "
            + ", ".join(sorted(missing_outcomes))
        )
    return cases


def _validate_case(case: dict[str, Any], case_id: str) -> set[str]:
    cluster = case.get("complaint_cluster")
    if not isinstance(cluster, dict) or not isinstance(cluster.get("id"), str):
        raise DatasetValidationError(f"{case_id} requires a complaint_cluster id")
    if not _string_list(cluster, "symptoms", case_id):
        raise DatasetValidationError(f"{case_id} complaint_cluster requires symptoms")
    catalogs = _validate_label_catalogs(case, case_id)
    turns = case.get("turns")
    if not isinstance(turns, list) or not turns:
        raise DatasetValidationError(f"{case_id} requires one or more turns")
    outcomes: set[str] = set()
    for expected_order, turn in enumerate(turns, start=1):
        if not isinstance(turn, dict) or turn.get("order") != expected_order:
            raise DatasetValidationError(
                f"{case_id} turn order must be consecutive starting at 1"
            )
        user_input = turn.get("user_input")
        if not isinstance(user_input, dict) or not isinstance(
            user_input.get("message"), str
        ):
            raise DatasetValidationError(
                f"{case_id} turn {expected_order} requires user_input.message"
            )
        expected = turn.get("expected")
        if not isinstance(expected, dict):
            raise DatasetValidationError(
                f"{case_id} turn {expected_order} requires expected labels"
            )
        outcomes.update(_validate_expected(expected, catalogs, case_id, expected_order))
    return outcomes


def _validate_label_catalogs(case: dict[str, Any], case_id: str) -> dict[str, set[str]]:
    labels = case.get("labels")
    if not isinstance(labels, dict):
        raise DatasetValidationError(f"{case_id} requires labels")
    catalogs: dict[str, set[str]] = {}
    for name in set(LABEL_CATALOGS.values()):
        values = labels.get(name)
        if not isinstance(values, list):
            raise DatasetValidationError(f"{case_id} labels.{name} must be a list")
        ids: set[str] = set()
        for value in values:
            label_id = value.get("id") if isinstance(value, dict) else value
            if not isinstance(label_id, str) or not label_id:
                raise DatasetValidationError(
                    f"{case_id} labels.{name} needs non-empty IDs"
                )
            if label_id in ids:
                raise DatasetValidationError(
                    f"{case_id} duplicate {name} label: {label_id}"
                )
            ids.add(label_id)
        catalogs[name] = ids
    findings = labels["findings"]
    if any(
        not isinstance(finding, dict) or finding.get("source") not in EVIDENCE_SOURCES
        for finding in findings
    ):
        raise DatasetValidationError(
            f"{case_id} finding label has an invalid evidence source"
        )
    return catalogs


def _validate_expected(
    expected: dict[str, Any],
    catalogs: dict[str, set[str]],
    case_id: str,
    order: int,
) -> set[str]:
    missing = REQUIRED_EXPECTED_LABELS - expected.keys()
    if missing:
        raise DatasetValidationError(
            f"{case_id} turn {order} missing required labels: "
            + ", ".join(sorted(missing))
        )
    for field, catalog in LABEL_CATALOGS.items():
        for label_id in _string_list(expected, field, f"{case_id} turn {order}"):
            if label_id not in catalogs[catalog]:
                singular = catalog.removesuffix("s")
                raise DatasetValidationError(
                    f"{case_id} turn {order} references unknown {singular} label: "
                    + label_id
                )
    permitted = expected["save_diagnostic_report_permitted"]
    if not isinstance(permitted, bool):
        raise DatasetValidationError(
            f"{case_id} turn {order} save permission must be boolean"
        )
    outcomes = _string_list(
        expected, "allowed_completion_outcomes", f"{case_id} turn {order}"
    )
    if any(outcome not in COMPLETION_OUTCOMES for outcome in outcomes):
        raise DatasetValidationError(
            f"{case_id} turn {order} has an invalid completion outcome"
        )
    if permitted != bool(outcomes):
        raise DatasetValidationError(
            f"{case_id} turn {order} save permission and completion outcomes disagree"
        )
    safety = expected["required_safety_behavior"]
    if not isinstance(safety, dict) or not all(
        isinstance(safety.get(field), bool)
        for field in ("must_raise_safety_flag", "requires_in_person_assessment")
    ):
        raise DatasetValidationError(
            f"{case_id} turn {order} has invalid safety labels"
        )
    if (
        safety["requires_in_person_assessment"]
        and "in_person_assessment_required" not in outcomes
    ):
        raise DatasetValidationError(
            f"{case_id} turn {order} in-person safety requires referral completion"
        )
    if (
        "in_person_assessment_required" in outcomes
        and not safety["requires_in_person_assessment"]
    ):
        raise DatasetValidationError(
            f"{case_id} turn {order} referral completion requires safety label"
        )
    return set(outcomes)


def _string_list(value: dict[str, Any], field: str, context: str) -> list[str]:
    items = value.get(field)
    if not isinstance(items, list) or any(
        not isinstance(item, str) or not item for item in items
    ):
        raise DatasetValidationError(
            f"{context} {field} must be a list of non-empty strings"
        )
    if len(items) != len(set(items)):
        raise DatasetValidationError(f"{context} {field} must not contain duplicates")
    return items


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dataset", type=Path)
    args = parser.parse_args()
    cases = validate_dataset(load_dataset(args.dataset))
    print(f"Validated {len(cases)} diagnostic-observation cases")  # noqa: T201
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
