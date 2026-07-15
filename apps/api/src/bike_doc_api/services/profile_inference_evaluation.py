"""Reusable, provider-independent evaluation of profile inference outcomes.

The evaluation runner supplies recorded extractor responses. This module validates
those responses with the production output schema and sends valid claims through
the production deterministic resolver, while keeping dataset and report concerns
out of the runtime inference service.
"""

from __future__ import annotations

import asyncio
import json
from collections import Counter, defaultdict
from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from bike_doc_api.models.bike import BikeFactClaim, BikeFieldResolution, BikeProfile
from bike_doc_api.schemas.profile_inference import (
    BRAKE_INFERENCE_FIELD_PATHS,
    ProfileInferenceOutput,
)
from bike_doc_api.services.profile_inference_resolution import (
    ProfileInferenceResolver,
    ProfileResolverPolicy,
)
from bike_doc_api.services.profile_registry import (
    FIELD_REGISTRY_VERSION,
    FieldRegistryValidationError,
    get_canonical_field,
    normalize_canonical_value,
)
from bike_doc_api.services.profile_resolution import empty_technical_projection

EVALUATION_SCHEMA_VERSION = "bike_profile_inference_eval.v2"
REPORT_SCHEMA_VERSION = "bike_profile_inference_eval_report.v1"
BASELINE_SCHEMA_VERSION = "bike_profile_inference_eval_baseline.v1"


class EvaluationInputError(ValueError):
    """The dataset or recorded extractor responses are not evaluable."""


@dataclass(frozen=True, slots=True)
class _CaseResult:
    case_id: str
    predicted_claims: list[dict[str, Any]]
    expected_claims: list[dict[str, Any]]
    expected_abstentions: list[dict[str, Any]]
    expected_outcomes: list[dict[str, Any]]
    actions: dict[str, str]
    dispositions: dict[str, str]
    extraction_valid: bool
    extraction_error: str | None
    profile_corruption_failures: list[str]
    telemetry: dict[str, float]
    scene: dict[str, Any] | None


class _EvaluationRepository:
    """Small in-memory repository implementing the resolver's public seam."""

    def __init__(self, bike: BikeProfile) -> None:
        self.bike = bike
        self.claims: dict[str, BikeFactClaim] = {}
        self.resolutions: dict[str, BikeFieldResolution] = {}

    async def get_resolution(
        self, *, bike_id: str, field_path: str
    ) -> BikeFieldResolution | None:
        del bike_id
        return self.resolutions.get(field_path)

    async def save_resolution(
        self, resolution: BikeFieldResolution
    ) -> BikeFieldResolution:
        self.resolutions[resolution.field_path] = resolution
        return resolution

    async def add_claim(self, claim: BikeFactClaim) -> BikeFactClaim:
        self.claims[claim.id] = claim
        return claim

    async def get_claim(self, claim_id: str) -> BikeFactClaim | None:
        return self.claims.get(claim_id)

    async def save(self, bike: BikeProfile) -> BikeProfile:
        self.bike = bike
        return bike


def load_json(path: Path) -> dict[str, Any]:
    """Load one JSON object and give CLI callers a stable error type."""

    try:
        value = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise EvaluationInputError(f"Could not read JSON file {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise EvaluationInputError(f"Expected a JSON object in {path}")
    return value


def validate_dataset(dataset: dict[str, Any]) -> None:
    """Validate the versioned, extensible evaluation label shape."""

    if dataset.get("schema_version") != EVALUATION_SCHEMA_VERSION:
        raise EvaluationInputError(
            f"dataset schema_version must be {EVALUATION_SCHEMA_VERSION!r}"
        )
    if not isinstance(dataset.get("dataset_version"), str):
        raise EvaluationInputError("dataset_version is required")
    cases = dataset.get("cases")
    if not isinstance(cases, list) or not cases:
        raise EvaluationInputError("dataset cases must be a non-empty list")
    ids: set[str] = set()
    for case in cases:
        if not isinstance(case, dict) or not isinstance(case.get("id"), str):
            raise EvaluationInputError("each case requires a string id")
        case_id = case["id"]
        if case_id in ids:
            raise EvaluationInputError(f"duplicate case id: {case_id}")
        ids.add(case_id)
        for key in ("expected_claims", "expected_abstentions", "expected_outcomes"):
            if not isinstance(case.get(key), list):
                raise EvaluationInputError(f"{case_id}.{key} must be a list")
        for item in [*case["expected_claims"], *case["expected_abstentions"]]:
            _validate_label_field(item, case_id)
        for item in case["expected_outcomes"]:
            if not isinstance(item, dict) or not isinstance(
                item.get("field_path"), str
            ):
                raise EvaluationInputError(
                    f"{case_id}.expected_outcomes requires field_path"
                )
            if item.get("expected_action") not in {
                "applied",
                "supporting",
                "pending",
                "conflict",
                "rejected",
                "abstained",
            }:
                raise EvaluationInputError(
                    f"{case_id}.expected_outcomes has invalid expected_action"
                )


def _validate_label_field(item: Any, case_id: str) -> None:
    if not isinstance(item, dict) or not isinstance(item.get("field_path"), str):
        raise EvaluationInputError(f"{case_id} labels require field_path")
    try:
        value = item.get("value")
        if value is not None:
            normalize_canonical_value(item["field_path"], value)
    except (FieldRegistryValidationError, KeyError) as exc:
        raise EvaluationInputError(f"{case_id} has invalid field label: {exc}") from exc
    for key in ("position", "installedness", "evidence_class"):
        if not isinstance(item.get(key), str):
            raise EvaluationInputError(f"{case_id} labels require {key}")


async def evaluate_dataset(
    dataset: dict[str, Any],
    predictions: dict[str, dict[str, Any]],
    *,
    policy: ProfileResolverPolicy | None = None,
) -> dict[str, Any]:
    """Evaluate recorded extractor outputs through schema validation and resolution."""

    validate_dataset(dataset)
    expected_ids = {case["id"] for case in dataset["cases"]}
    if set(predictions) != expected_ids:
        missing = sorted(expected_ids - set(predictions))
        extra = sorted(set(predictions) - expected_ids)
        raise EvaluationInputError(
            "prediction case IDs do not match dataset "
            f"(missing={missing}, extra={extra})"
        )

    case_results = [
        await _evaluate_case(
            case,
            predictions[case["id"]],
            policy or ProfileResolverPolicy.bootstrap_v1(),
        )
        for case in dataset["cases"]
    ]
    report = _build_report(dataset, case_results)
    report["cases"] = [
        {
            "case_id": result.case_id,
            "extraction_valid": result.extraction_valid,
            "extraction_error": result.extraction_error,
            "actions": result.actions,
            "dispositions": result.dispositions,
            "profile_corruption_failures": result.profile_corruption_failures,
        }
        for result in case_results
    ]
    return report


async def _evaluate_case(
    case: dict[str, Any],
    prediction: dict[str, Any],
    policy: ProfileResolverPolicy,
) -> _CaseResult:
    output_raw = prediction.get("extraction", prediction.get("output"))
    if not isinstance(output_raw, dict):
        return _invalid_case(case, "prediction requires extraction object", prediction)

    try:
        output = ProfileInferenceOutput.model_validate(output_raw)
        claims = _validated_claims(case, output)
    except (ValidationError, FieldRegistryValidationError, ValueError) as exc:
        return _invalid_case(case, f"{type(exc).__name__}: {exc}", prediction)

    repository, bike = _initial_state(case)
    persisted: list[BikeFactClaim] = []
    observed_at = _observed_at(case)
    for index, claim in enumerate(claims):
        persisted_claim = BikeFactClaim(
            id=f"bfc_eval_{case['id']}_{index}",
            bike_id=bike.id,
            field_path=claim.field_path,
            value=claim.value,
            source_type="image_inference",
            source_ref={"type": "evaluation_case", "id": case["id"]},
            evidence_refs=[
                {"type": "artifact", "id": artifact_id}
                for artifact_id in claim.artifact_ids
            ],
            scope_assumption=claim.subject_relation,
            observed_at=observed_at,
            evidence_basis=claim.evidence_basis,
            visibility=claim.visibility,
            model_score=claim.confidence_score,
            evidence_cues=claim.evidence_cues,
            disposition="pending",
        )
        await repository.add_claim(persisted_claim)
        persisted.append(persisted_claim)

    resolver = ProfileInferenceResolver(bikes=repository, policy=policy)
    await resolver.resolve(bike=bike, claims=persisted)
    actions = {claim.field_path: claim.disposition for claim in persisted}
    dispositions = {
        claim.field_path: claim.disposition_reason or "" for claim in persisted
    }
    expected_outcomes = case["expected_outcomes"]
    corruption = _corruption_failures(expected_outcomes, actions)
    return _CaseResult(
        case_id=case["id"],
        predicted_claims=[_claim_dict(claim) for claim in claims],
        expected_claims=case["expected_claims"],
        expected_abstentions=case["expected_abstentions"],
        expected_outcomes=expected_outcomes,
        actions=actions,
        dispositions=dispositions,
        extraction_valid=True,
        extraction_error=None,
        profile_corruption_failures=corruption,
        telemetry=_telemetry(prediction),
        scene=output.scene.model_dump(mode="json"),
    )


def _invalid_case(
    case: dict[str, Any], error: str, prediction: dict[str, Any]
) -> _CaseResult:
    return _CaseResult(
        case_id=case["id"],
        predicted_claims=[],
        expected_claims=case["expected_claims"],
        expected_abstentions=case["expected_abstentions"],
        expected_outcomes=case["expected_outcomes"],
        actions={},
        dispositions={},
        extraction_valid=False,
        extraction_error=error,
        profile_corruption_failures=[],
        telemetry=_telemetry(prediction),
        scene=None,
    )


def _validated_claims(
    case: dict[str, Any], output: ProfileInferenceOutput
) -> list[Any]:
    allowed = set(case.get("allowed_field_paths", BRAKE_INFERENCE_FIELD_PATHS))
    abstentions = {item.field_path for item in output.abstentions}
    if output.claims and (
        not output.scene.contains_bicycle
        or output.scene.multiple_bicycles
        or output.scene.target_relation
        not in {
            "installed_on_target_bike",
            "likely_installed_on_target_bike",
            "loose_component",
            "packaging_or_reference",
        }
    ):
        raise ValueError("scene cannot support installed target-bike claims")
    seen: set[str] = set()
    for claim in output.claims:
        if claim.field_path not in allowed:
            raise ValueError(
                f"claim is outside allowed field paths: {claim.field_path}"
            )
        if claim.field_path in seen or claim.field_path in abstentions:
            raise ValueError(
                f"claim field path is repeated or abstained: {claim.field_path}"
            )
        if claim.subject_relation != output.scene.target_relation:
            raise ValueError(f"claim relation does not match scene: {claim.field_path}")
        seen.add(claim.field_path)
        claim.value = normalize_canonical_value(claim.field_path, claim.value)
        field = get_canonical_field(claim.field_path, claim.value)
        if claim.evidence_basis not in field.permitted_evidence_bases:
            raise ValueError(f"evidence basis is not allowed: {claim.field_path}")
        input_ids = set(case.get("input", {}).get("artifact_ids", ["image"]))
        if not set(claim.artifact_ids).issubset(input_ids):
            raise ValueError(
                f"claim references an artifact outside the case: {claim.field_path}"
            )
    return output.claims


def _initial_state(case: dict[str, Any]) -> tuple[_EvaluationRepository, BikeProfile]:
    now = _observed_at(case)
    bike = BikeProfile(
        id="bike_eval",
        user_id="user_eval",
        display_name="Evaluation bike",
        bike_type="unknown",
        frame_material="unknown",
        brake_type="unknown",
        technical_profile=empty_technical_projection(),
        profile_revision=0,
        created_at=now,
        updated_at=now,
    )
    repository = _EvaluationRepository(bike)
    for index, item in enumerate(case.get("initial_claims", [])):
        claim = BikeFactClaim(
            id=f"bfc_initial_{case['id']}_{index}",
            bike_id=bike.id,
            field_path=item["field_path"],
            value=normalize_canonical_value(item["field_path"], item["value"]),
            source_type=item["source_type"],
            source_ref={"type": "evaluation_initial_state"},
            evidence_refs=[],
            observed_at=datetime.fromisoformat(
                item["observed_at"].replace("Z", "+00:00")
            ),
            disposition="applied",
        )
        repository.claims[claim.id] = claim
        repository.resolutions[claim.field_path] = BikeFieldResolution(
            bike_id=bike.id,
            field_path=claim.field_path,
            current_value=claim.value,
            resolution_state="resolved",
            current_claim_id=claim.id,
            effective_confidence="high",
            source_type=claim.source_type,
            observed_at=claim.observed_at,
            resolved_at=claim.observed_at,
        )
        bike.technical_profile = _set_technical_value(
            bike.technical_profile, claim.field_path, claim.value
        )
    return repository, bike


def _set_technical_value(
    projection: dict[str, Any], path: str, value: Any
) -> dict[str, Any]:
    result: dict[str, Any] = deepcopy(projection)
    cursor = result
    parts = path.split(".")
    for part in parts[:-1]:
        cursor = cursor.setdefault(part, {})
    cursor[parts[-1]] = value
    return result


def _observed_at(case: dict[str, Any]) -> datetime:
    value = case.get("observed_at", "2026-07-11T12:00:00Z")
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)


def _claim_dict(claim: Any) -> dict[str, Any]:
    return {
        "field_path": claim.field_path,
        "value": claim.value,
        "position": _position(claim.field_path),
        "installedness": _installedness(claim.subject_relation),
        "evidence_class": claim.evidence_basis,
        "visibility": claim.visibility,
    }


def _position(field_path: str) -> str:
    parts = field_path.split(".")
    return (
        parts[1] if len(parts) > 1 and parts[1] in {"front", "rear"} else "whole_bike"
    )


def _installedness(subject_relation: str) -> str:
    return {
        "installed_on_target_bike": "installed",
        "likely_installed_on_target_bike": "ambiguous",
        "loose_component": "loose",
        "packaging_or_reference": "reference",
        "other_bike": "other_bike",
        "ambiguous": "ambiguous",
    }[subject_relation]


def _telemetry(prediction: dict[str, Any]) -> dict[str, float]:
    telemetry = prediction.get("telemetry", {})
    if not isinstance(telemetry, dict):
        return {}
    return {
        key: float(telemetry[key])
        for key in ("latency_ms", "input_tokens", "output_tokens", "cost_usd")
        if isinstance(telemetry.get(key), (int, float))
    }


def _corruption_failures(
    expected_outcomes: list[dict[str, Any]], actions: dict[str, str]
) -> list[str]:
    failures: list[str] = []
    for outcome in expected_outcomes:
        actual = actions.get(outcome["field_path"], "abstained")
        if actual == "applied" and outcome["expected_action"] != "applied":
            failures.extend(outcome.get("profile_corruption_failures", []))
    return sorted(set(failures))


def _build_report(
    dataset: dict[str, Any], results: list[_CaseResult]
) -> dict[str, Any]:
    field_groups: dict[tuple[str, str], Counter[str]] = defaultdict(Counter)
    for result in results:
        expected_by_key = {
            (item["field_path"], item["evidence_class"]): item
            for item in result.expected_claims
        }
        predicted_by_key = {
            (item["field_path"], item["evidence_class"]): item
            for item in result.predicted_claims
        }
        for key in set(expected_by_key) | set(predicted_by_key):
            expected = expected_by_key.get(key)
            predicted = predicted_by_key.get(key)
            match = bool(expected and predicted and _same_claim(expected, predicted))
            field_groups[key]["tp"] += int(match)
            field_groups[key]["fp"] += int(predicted is not None and not match)
            field_groups[key]["fn"] += int(expected is not None and not match)

    field_metrics = []
    for (field_path, evidence_class), counts in sorted(field_groups.items()):
        field_metrics.append(
            {
                "field_path": field_path,
                "evidence_class": evidence_class,
                **counts,
                "precision": _ratio(counts["tp"], counts["tp"] + counts["fp"]),
                "recall": _ratio(counts["tp"], counts["tp"] + counts["fn"]),
            }
        )

    abstention_total = abstention_correct = 0
    position_total = position_correct = 0
    installed_total = installed_correct = 0
    fill: Counter[str] = Counter()
    overwrite: Counter[str] = Counter()
    conflicts: Counter[str] = Counter()
    false_positive: Counter[str] = Counter()
    corruption: Counter[str] = Counter()
    correction: Counter[str] = Counter()
    telemetry_totals: defaultdict[str, float] = defaultdict(float)
    expected_corruption: Counter[str] = Counter()
    for result, case in zip(results, dataset["cases"], strict=True):
        predicted_fields = {item["field_path"] for item in result.predicted_claims}
        for item in [*result.expected_claims, *result.expected_abstentions]:
            expected_present = item in result.expected_claims
            abstention_total += 1
            abstention_correct += int(
                (item["field_path"] in predicted_fields) == expected_present
            )

        expected_position = case.get("expected_scene", {}).get("position")
        if expected_position and result.predicted_claims:
            position_total += len(result.predicted_claims)
            position_correct += sum(
                _position(item["field_path"]) == expected_position
                for item in result.predicted_claims
            )

        expected_installedness = case.get("expected_scene", {}).get("installedness")
        if expected_installedness and result.scene:
            installed_total += 1
            installed_correct += int(
                _installedness(result.scene["target_relation"])
                == expected_installedness
            )

        expected_by_path = {
            item["field_path"]: item for item in result.expected_outcomes
        }
        initial_paths = {item["field_path"] for item in case.get("initial_claims", [])}
        for path in set(expected_by_path) | set(result.actions):
            actual = result.actions.get(path, "abstained")
            expected = expected_by_path.get(path)
            expected_action = expected["expected_action"] if expected else "abstained"
            metric = overwrite if path in initial_paths else fill
            metric["expected"] += int(expected_action == "applied")
            metric["actual"] += int(actual == "applied")
            metric["correct"] += int(
                actual == "applied" and expected_action == "applied"
            )
            if expected is not None and "conflict" in expected:
                conflicts["total"] += 1
                conflicts["correct"] += int(
                    (actual == "conflict") == expected["conflict"]
                )

        expected_claims = result.expected_claims
        false_positive["predicted"] += len(result.predicted_claims)
        false_positive["false"] += sum(
            not any(_same_claim(expected, predicted) for expected in expected_claims)
            for predicted in result.predicted_claims
        )
        for failure in result.profile_corruption_failures:
            corruption[failure] += 1
        for expected in result.expected_outcomes:
            for failure in expected.get("profile_corruption_failures", []):
                expected_corruption[failure] += 1
        expected_correction = bool(case.get("expected_later_correction", False))
        observed_correction = bool(case.get("observed_later_correction", False))
        correction["expected"] += int(expected_correction)
        correction["observed"] += int(observed_correction)
        correction["correct"] += int(expected_correction == observed_correction)
        for telemetry_key, telemetry_value in result.telemetry.items():
            telemetry_totals[telemetry_key] += telemetry_value

    versions = dict(dataset.get("versions", {}))
    versions.setdefault("registry_version", FIELD_REGISTRY_VERSION)
    report = {
        "report_schema_version": REPORT_SCHEMA_VERSION,
        "evaluation_schema_version": dataset["schema_version"],
        "dataset_version": dataset["dataset_version"],
        "versions": versions,
        "policy": dataset.get("policy", "bootstrap-v1"),
        "case_count": len(results),
        "field_evidence_metrics": field_metrics,
        "metrics": {
            "abstention_correctness": _ratio(abstention_correct, abstention_total),
            "front_rear_position_accuracy": _ratio(position_correct, position_total),
            "installedness_accuracy": _ratio(installed_correct, installed_total),
            "auto_fill_precision": _ratio(fill["correct"], fill["actual"]),
            "auto_fill_coverage": _ratio(fill["correct"], fill["expected"]),
            "auto_overwrite_precision": _ratio(
                overwrite["correct"], overwrite["actual"]
            ),
            "auto_overwrite_coverage": _ratio(
                overwrite["correct"], overwrite["expected"]
            ),
            "conflict_accuracy": _ratio(conflicts["correct"], conflicts["total"]),
            "false_positive_rate": _ratio(
                false_positive["false"], false_positive["predicted"]
            ),
            "later_correction_rate": _ratio(
                correction["observed"], correction["expected"]
            ),
            "later_correction_accuracy": _ratio(correction["correct"], len(results)),
            "profile_corruption_failures": dict(sorted(corruption.items())),
            "expected_profile_corruption_failures": dict(
                sorted(expected_corruption.items())
            ),
            "latency_ms_mean": _ratio(telemetry_totals["latency_ms"], len(results)),
            "input_tokens_mean": _ratio(telemetry_totals["input_tokens"], len(results)),
            "output_tokens_mean": _ratio(
                telemetry_totals["output_tokens"], len(results)
            ),
            "cost_usd_total": telemetry_totals["cost_usd"],
        },
    }
    return report


def _same_claim(expected: dict[str, Any], predicted: dict[str, Any]) -> bool:
    return bool(
        normalize_canonical_value(expected["field_path"], expected["value"])
        == normalize_canonical_value(predicted["field_path"], predicted["value"])
        and expected["position"] == predicted["position"]
        and expected["installedness"] == predicted["installedness"]
        and expected["evidence_class"] == predicted["evidence_class"]
    )


def _ratio(numerator: float, denominator: float) -> float:
    return round(numerator / denominator, 6) if denominator else 0.0


def load_predictions(value: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Load the recorded response envelope keyed by evaluation case ID."""

    cases = value.get("cases")
    if not isinstance(cases, list):
        raise EvaluationInputError("predictions cases must be a list")
    result: dict[str, dict[str, Any]] = {}
    for item in cases:
        if not isinstance(item, dict) or not isinstance(item.get("case_id"), str):
            raise EvaluationInputError("each prediction requires case_id")
        if item["case_id"] in result:
            raise EvaluationInputError(
                f"duplicate prediction case id: {item['case_id']}"
            )
        result[item["case_id"]] = item
    return result


def compare_reports(
    previous: dict[str, Any] | None, current: dict[str, Any]
) -> dict[str, Any]:
    """Compare candidate metrics and explicitly report missing baselines."""

    if previous is None:
        return {
            "status": "missing_prior_baseline",
            "required": True,
            "message": "No accepted baseline exists; this is not a regression pass.",
        }
    previous_versions = previous.get("versions", {})
    current_versions = current.get("versions", {})
    changed = (
        previous.get("dataset_version") != current.get("dataset_version")
        or previous_versions != current_versions
    )
    previous_metrics = previous.get("metrics", {})
    current_metrics = current.get("metrics", {})
    lower_is_better = {
        "false_positive_rate",
        "later_correction_rate",
        "latency_ms_mean",
        "input_tokens_mean",
        "output_tokens_mean",
        "cost_usd_total",
    }
    deltas: dict[str, float] = {}
    regressions: list[str] = []
    for key, previous_value in previous_metrics.items():
        current_value = current_metrics.get(key)
        if isinstance(previous_value, (int, float)) and isinstance(
            current_value, (int, float)
        ):
            deltas[key] = round(current_value - previous_value, 6)
            regressed = (
                current_value > previous_value
                if key in lower_is_better
                else current_value < previous_value
            )
            if regressed:
                regressions.append(key)
    return {
        "status": "regression_detected"
        if regressions
        else ("comparison_passed" if changed else "unchanged"),
        "required": changed,
        "version_changed": changed,
        "metric_deltas": deltas,
        "regressions": regressions,
    }


def make_baseline(report: dict[str, Any], *, accepted_at: str) -> dict[str, Any]:
    """Wrap a report in the durable accepted-baseline envelope."""

    return {
        "baseline_schema_version": BASELINE_SCHEMA_VERSION,
        "accepted_at": accepted_at,
        "dataset_version": report["dataset_version"],
        "versions": report["versions"],
        "policy": report["policy"],
        "metrics": report["metrics"],
        "field_evidence_metrics": report["field_evidence_metrics"],
        "case_count": report["case_count"],
    }


def run_evaluation(
    dataset_path: Path,
    predictions_path: Path,
    *,
    baseline_path: Path | None = None,
    output_path: Path | None = None,
    accept: bool = False,
    accept_initial_baseline: bool = False,
) -> tuple[dict[str, Any], int]:
    """Run the CLI workflow, including comparison and guarded baseline writes."""

    dataset = load_json(dataset_path)
    predictions = load_predictions(load_json(predictions_path))
    report = asyncio.run(evaluate_dataset(dataset, predictions))
    previous = (
        load_json(baseline_path) if baseline_path and baseline_path.exists() else None
    )
    if (
        previous is not None
        and previous.get("baseline_schema_version") != BASELINE_SCHEMA_VERSION
    ):
        raise EvaluationInputError(
            f"accepted baseline must use {BASELINE_SCHEMA_VERSION!r}"
        )
    comparison = compare_reports(previous, report)
    report["baseline_comparison"] = comparison
    exit_code = 0
    if comparison["status"] == "missing_prior_baseline":
        exit_code = 2
    elif comparison["status"] == "regression_detected":
        exit_code = 3
    if accept:
        can_accept = (
            comparison["status"] != "missing_prior_baseline" or accept_initial_baseline
        )
        can_accept = can_accept and comparison["status"] != "regression_detected"
        if not can_accept:
            exit_code = max(exit_code, 4)
        elif baseline_path:
            baseline_path.parent.mkdir(parents=True, exist_ok=True)
            baseline_path.write_text(
                json.dumps(
                    make_baseline(report, accepted_at=datetime.now(UTC).isoformat()),
                    indent=2,
                    sort_keys=True,
                )
                + "\n"
            )
    if output_path:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    return report, exit_code
