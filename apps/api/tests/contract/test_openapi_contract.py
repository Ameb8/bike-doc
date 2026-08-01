"""OpenAPI contract tests for the diagnostic API slice."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest
import yaml
from fastapi import FastAPI
from jsonschema import Draft7Validator, RefResolver

DIAGNOSTIC_OPERATIONS = {
    "/v1/repair-sessions": {"get", "post"},
    "/v1/repair-sessions/{sessionId}": {"get"},
    "/v1/repair-sessions/{sessionId}/turns": {"post"},
    "/v1/repair-sessions/{sessionId}/events": {"get"},
    "/v1/artifacts": {"post"},
    "/v1/repair-sessions/{sessionId}/reports": {"get"},
    "/v1/repair-sessions/{sessionId}/reports/{reportId}": {"get"},
}

OPENAPI_PATH = Path(__file__).resolve().parents[4] / "docs/specs/openapi.yaml"


def _load_canonical_openapi() -> dict[str, Any]:
    return yaml.safe_load(OPENAPI_PATH.read_text())


def _json_schema_with_openapi_nullability(value: Any) -> Any:
    """Translate the OpenAPI 3.0 nullable extension for schema validation."""

    if isinstance(value, list):
        return [_json_schema_with_openapi_nullability(item) for item in value]
    if not isinstance(value, dict):
        return value

    normalized = {
        key: _json_schema_with_openapi_nullability(item)
        for key, item in value.items()
        if key != "nullable"
    }
    if value.get("nullable"):
        return {"anyOf": [normalized, {"type": "null"}]}
    return normalized


def _validate_canonical_schema(schema_name: str, value: object) -> list[str]:
    openapi = _json_schema_with_openapi_nullability(deepcopy(_load_canonical_openapi()))
    schema = {"$ref": f"#/components/schemas/{schema_name}"}
    validator = Draft7Validator(schema, resolver=RefResolver.from_schema(openapi))
    return sorted(error.message for error in validator.iter_errors(value))


def _schema_refs(value: Any) -> list[str]:
    refs: list[str] = []
    if isinstance(value, dict):
        ref = value.get("$ref")
        if isinstance(ref, str):
            refs.append(ref)
        for child in value.values():
            refs.extend(_schema_refs(child))
    elif isinstance(value, list):
        for child in value:
            refs.extend(_schema_refs(child))
    return sorted(set(refs))


def _operation_summary(operation: dict[str, Any]) -> dict[str, Any]:
    request_body = operation.get("requestBody", {})
    responses = operation.get("responses", {})
    return {
        "operationId": operation.get("operationId"),
        "status_codes": sorted(responses),
        "request_schema_refs": _schema_refs(request_body),
        "response_schema_refs": {
            status: _schema_refs(response) for status, response in responses.items()
        },
    }


def _diagnostic_contract(openapi: dict[str, Any]) -> dict[str, Any]:
    paths = openapi["paths"]
    contract: dict[str, Any] = {}
    for path, methods in DIAGNOSTIC_OPERATIONS.items():
        contract[path] = {}
        for method in methods:
            if path not in paths or method not in paths[path]:
                contract[path][method] = {"missing": True}
                continue
            contract[path][method] = _operation_summary(paths[path][method])
    return contract


def test_app_can_produce_openapi_document(app: FastAPI) -> None:
    openapi = app.openapi()

    assert openapi["openapi"].startswith("3.")
    assert openapi["info"]["title"]
    assert isinstance(openapi["paths"], dict)


def test_app_openapi_contains_diagnostic_slice_paths(app: FastAPI) -> None:
    openapi = app.openapi()

    for path, methods in DIAGNOSTIC_OPERATIONS.items():
        assert path in openapi["paths"]
        for method in methods:
            assert method in openapi["paths"][path]


def test_user_turn_artifact_id_constraints_match_canonical_contract(
    app: FastAPI,
) -> None:
    canonical = _load_canonical_openapi()
    canonical_property = canonical["components"]["schemas"]["UserTurnMessage"][
        "properties"
    ]["artifact_ids"]
    actual_property = app.openapi()["components"]["schemas"]["UserTurnMessage"][
        "properties"
    ]["artifact_ids"]

    assert actual_property["type"] == canonical_property["type"] == "array"
    assert actual_property["items"] == canonical_property["items"] == {"type": "string"}
    assert actual_property["maxItems"] == canonical_property["maxItems"] == 3
    assert actual_property["uniqueItems"] is canonical_property["uniqueItems"] is True


def test_diagnostic_report_v2_contract_rejects_mixed_fields() -> None:
    """V2 examples validate while V1-only fields cannot leak into V2."""

    openapi = _load_canonical_openapi()
    schemas = openapi["components"]["schemas"]
    mapping = schemas["PhaseReportEnvelope"]["properties"]["payload"]["discriminator"][
        "mapping"
    ]

    assert mapping["diagnostic_report.v1"] == "#/components/schemas/DiagnosticReportV1"
    assert mapping["diagnostic_report.v2"] == "#/components/schemas/DiagnosticReportV2"

    v2 = schemas["DiagnosticReportV2"]
    assert v2["additionalProperties"] is False
    assert set(v2["required"]) == {
        "schema_version",
        "diagnostic_outcome",
        "reported_symptoms",
        "primary_diagnosis",
        "contributing_factors",
        "observed_findings",
        "alternate_hypotheses",
        "unresolved_uncertainties",
        "evidence_summary",
        "key_artifact_ids",
        "user_skill_level",
        "safety_flags",
        "diagnostic_session_id",
    }
    assert "server-owned" in schemas["DiagnosticOutcome"]["description"].lower()
    assert schemas["DiagnosticOutcome"]["enum"] == [
        "diagnosis_supported",
        "user_declined_more_input",
        "requested_input_unavailable",
        "in_person_assessment_required",
    ]

    examples = openapi["components"]["examples"]
    supported = examples["DiagnosticReportV2Supported"]["value"]
    limited = examples["DiagnosticReportV2LimitedReferral"]["value"]

    assert _validate_canonical_schema("DiagnosticReportV2", supported) == []
    assert _validate_canonical_schema("DiagnosticReportV2", limited) == []

    mixed = deepcopy(supported)
    mixed["repair_estimate"] = {"difficulty": "easy"}
    mixed["cost_estimate"] = None
    mixed["alternate_hypotheses"][0]["ruled_out_by"] = "Not applicable in V2."

    errors = _validate_canonical_schema("DiagnosticReportV2", mixed)
    assert errors
    assert any("repair_estimate" in error for error in errors)
    assert any("cost_estimate" in error for error in errors)
    assert any("ruled_out_by" in error for error in errors)

    wrong_version = deepcopy(supported)
    wrong_version["schema_version"] = "diagnostic_report.v1"
    assert _validate_canonical_schema("DiagnosticReportV2", wrong_version)

    unsupported_null_primary = deepcopy(supported)
    unsupported_null_primary["primary_diagnosis"] = None
    assert _validate_canonical_schema("DiagnosticReportV2", unsupported_null_primary)

    image_without_artifact = deepcopy(supported)
    image_without_artifact["observed_findings"][-1]["artifact_ids"] = []
    assert _validate_canonical_schema("DiagnosticReportV2", image_without_artifact)

    non_image_with_artifact = deepcopy(supported)
    non_image_with_artifact["observed_findings"][0]["artifact_ids"] = ["art_chain_1"]
    assert _validate_canonical_schema("DiagnosticReportV2", non_image_with_artifact)


@pytest.mark.xfail(
    reason="Stage 5 diagnostic routes are specified before implementation.",
)
def test_diagnostic_openapi_operations_match_canonical_contract(
    app: FastAPI,
) -> None:
    canonical = _diagnostic_contract(_load_canonical_openapi())
    actual = _diagnostic_contract(app.openapi())

    assert actual == canonical
