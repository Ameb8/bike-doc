"""OpenAPI contract tests for the diagnostic API slice."""

from __future__ import annotations

import json
from copy import deepcopy
from functools import cache
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


@cache
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


def _resolve(value: dict[str, Any], document: dict[str, Any]) -> dict[str, Any]:
    """Resolve local references, including response and parameter components."""
    while "$ref" in value:
        target = document
        for part in value["$ref"].removeprefix("#/").split("/"):
            target = target[part]
        value = {
            **target,
            **{key: item for key, item in value.items() if key != "$ref"},
        }
    return value


def _public_schema(value: Any, document: dict[str, Any]) -> Any:
    """Normalize OpenAPI 3.0/3.1 representation without dropping constraints."""
    if isinstance(value, list):
        return [_public_schema(item, document) for item in value]
    if not isinstance(value, dict):
        return value
    value = _resolve(value, document)
    normalized = {
        key: (
            {name: _public_schema(schema, document) for name, schema in item.items()}
            if key == "properties"
            else _public_schema(item, document)
        )
        for key, item in value.items()
        if key
        not in {"title", "description", "example", "examples", "default", "nullable"}
    }
    if normalized.get("contentMediaType") == "application/octet-stream":
        normalized.pop("contentMediaType")
        normalized["format"] = "binary"
    if "const" in normalized:
        normalized["enum"] = [normalized.pop("const")]
    for key in ("required", "enum"):
        if key in normalized:
            normalized[key] = sorted(normalized[key])
    if list(normalized) == ["allOf"] and len(normalized["allOf"]) == 1:
        normalized = normalized["allOf"][0]
    if normalized.get("enum") == [None]:
        normalized = {"type": "null"}
    elif value.get("nullable"):
        normalized = {"anyOf": [normalized, {"type": "null"}]}
    for key in ("anyOf", "oneOf"):
        if key in normalized:
            normalized[key] = sorted(
                normalized[key], key=lambda item: json.dumps(item, sort_keys=True)
            )
    return normalized


def _content(value: dict[str, Any], document: dict[str, Any]) -> dict[str, Any]:
    value = _resolve(value, document)
    return {
        media: _public_schema(body.get("schema", {}), document)
        for media, body in value.get("content", {}).items()
    }


def _diagnostic_contract(openapi: dict[str, Any]) -> dict[str, Any]:
    contract: dict[str, Any] = {}
    for path, methods in DIAGNOSTIC_OPERATIONS.items():
        path_item = openapi["paths"][path]
        contract[path] = {}
        for method in methods:
            operation = path_item[method]
            parameters = {}
            for raw in [
                *path_item.get("parameters", []),
                *operation.get("parameters", []),
            ]:
                parameter = _resolve(raw, openapi)
                schema = parameter["schema"]
                if parameter["name"] == "Authorization":
                    # Auth uses a header dependency; canonical auth uses a
                    # security scheme instead of an ordinary parameter.
                    continue
                public_schema = _public_schema(schema, openapi)
                if not parameter.get("required", False) and "anyOf" in public_schema:
                    branches = [
                        branch
                        for branch in public_schema["anyOf"]
                        if branch != {"type": "null"}
                    ]
                    if len(branches) == 1:
                        public_schema = branches[0]
                parameters[(parameter["in"], parameter["name"])] = {
                    "required": parameter.get("required", False),
                    "schema": public_schema,
                    "default": schema.get("default"),
                }
            body = operation.get("requestBody", {})
            contract[path][method] = {
                "operationId": operation["operationId"],
                "parameters": parameters,
                "request_required": body.get("required", False),
                "request_content": _content(body, openapi),
                "responses": {
                    status: _content(response, openapi)
                    for status, response in operation["responses"].items()
                },
                "sse_events": sorted(operation.get("x-sse-events", [])),
            }
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


# Existing listing/read routes additionally reject invalid pagination/path input.
# Listing is owner-scoped to a required bike, as implemented by the session service.
ADDITIONAL_ERRORS = {
    ("/v1/repair-sessions", "get"): {"404", "422"},
    ("/v1/repair-sessions/{sessionId}", "get"): {"422"},
    ("/v1/repair-sessions/{sessionId}/reports", "get"): {"422"},
    ("/v1/repair-sessions/{sessionId}/reports/{reportId}", "get"): {"422"},
}


def _assert_public_schema(actual: Any, canonical: Any, location: str) -> None:
    """Check declared shapes and canonical constraints, permitting stricter guarantees.

    App models forbid extra properties and reject blank identifiers/negative costs.
    These existing refinements do not excuse dropping a canonical constraint, a
    field, an enum value, or a required field. Optional JSON idempotency keys also
    accept null as omission; HTTP behavior is covered by the route tests.
    """
    if isinstance(actual, dict) and isinstance(canonical, dict):
        if (
            "anyOf" in actual
            and "anyOf" not in canonical
            and location.endswith(("client_session_id", "client_artifact_id"))
        ):
            actual = next(
                branch for branch in actual["anyOf"] if branch != {"type": "null"}
            )
        extra = actual.keys() - canonical.keys()
        for key in extra:
            refinements = {"additionalProperties": False, "minLength": 1, "minimum": 0}
            assert key in refinements and actual[key] == refinements[key], (
                f"{location}: unexpected {key}"
            )
        for key, value in canonical.items():
            assert key in actual, f"{location}: missing {key}"
            _assert_public_schema(actual[key], value, f"{location}/{key}")
    elif (
        isinstance(actual, list)
        and isinstance(canonical, list)
        and location.endswith(("/anyOf", "/oneOf"))
    ):
        assert len(actual) == len(canonical), location
        unmatched = list(actual)
        for branch in canonical:
            for index, candidate in enumerate(unmatched):
                try:
                    _assert_public_schema(candidate, branch, location + "/branch")
                except AssertionError:
                    continue
                unmatched.pop(index)
                break
            else:
                raise AssertionError(f"{location}: no matching branch for {branch}")
    else:
        assert actual == canonical, f"{location}: {actual!r} != {canonical!r}"


def _assert_diagnostic_contract(
    actual_document: dict[str, Any], canonical_document: dict[str, Any]
) -> None:
    canonical = _diagnostic_contract(canonical_document)
    actual = _diagnostic_contract(actual_document)
    error = _public_schema(
        canonical_document["components"]["schemas"]["ErrorResponse"], canonical_document
    )
    for path, operations in canonical.items():
        for method, expected in operations.items():
            observed = actual[path][method]
            assert observed["operationId"] == expected["operationId"]
            assert observed["sse_events"] == expected["sse_events"]
            expected_responses = deepcopy(expected["responses"])
            for status in ADDITIONAL_ERRORS.get((path, method), set()):
                expected_responses[status] = {"application/json": error}
            assert observed["responses"].keys() == expected_responses.keys()
            _assert_public_schema(
                observed["responses"], expected_responses, f"{method} {path}/responses"
            )
            expected_parameters = deepcopy(expected["parameters"])
            if (path, method) == ("/v1/repair-sessions", "get"):
                expected_parameters[("query", "bike_id")]["required"] = True
            _assert_public_schema(
                observed["parameters"],
                expected_parameters,
                f"{method} {path}/parameters",
            )
            assert observed["request_required"] == expected["request_required"]
            _assert_public_schema(
                observed["request_content"],
                expected["request_content"],
                f"{method} {path}/request",
            )


def test_diagnostic_openapi_operations_match_canonical_contract(app: FastAPI) -> None:
    _assert_diagnostic_contract(app.openapi(), _load_canonical_openapi())


@pytest.mark.parametrize(
    "mutation",
    [
        "operation_id",
        "sse_media",
        "sse_schema",
        "missing_error",
        "error_shape",
        "acceptance_field",
        "required_field",
        "request_enum",
        "artifact_limit",
        "timeout_default",
        "timeout_bound",
        "event_vocabulary",
        "report_schema",
    ],
)
def test_diagnostic_contract_gate_rejects_public_drift(
    app: FastAPI,
    mutation: str,
) -> None:
    document = deepcopy(app.openapi())
    turns = document["paths"]["/v1/repair-sessions/{sessionId}/turns"]["post"]
    events = document["paths"]["/v1/repair-sessions/{sessionId}/events"]["get"]
    schemas = document["components"]["schemas"]
    if mutation == "operation_id":
        document["paths"]["/v1/artifacts"]["post"]["operationId"] = "uploadChanged"
    elif mutation == "sse_media":
        content = events["responses"]["200"]["content"]
        content["application/json"] = content.pop("text/event-stream")
    elif mutation == "sse_schema":
        events["responses"]["200"]["content"]["text/event-stream"]["schema"] = {
            "type": "object"
        }
    elif mutation == "missing_error":
        turns["responses"].pop("409")
    elif mutation == "error_shape":
        schemas["ErrorBody"]["properties"]["code"] = {"type": "integer"}
    elif mutation == "acceptance_field":
        schemas["TurnAccepted"]["properties"]["start_event_id"] = {"type": "integer"}
    elif mutation == "required_field":
        schemas["TurnAccepted"]["required"].remove("event_stream_url")
    elif mutation == "request_enum":
        schemas["TurnCreate"]["properties"]["schema_version"]["const"] = "ai_turn.v2"
    elif mutation == "artifact_limit":
        schemas["UserTurnMessage"]["properties"]["artifact_ids"]["maxItems"] = 4
    elif mutation in {"timeout_default", "timeout_bound"}:
        timeout = next(
            parameter
            for parameter in events["parameters"]
            if parameter["name"] == "timeout_seconds"
        )
        timeout["schema"]["default" if mutation == "timeout_default" else "maximum"] = (
            121
        )
    elif mutation == "event_vocabulary":
        events["x-sse-events"].append("internal.job.started")
    elif mutation == "report_schema":
        schemas["PhaseReportEnvelope"]["properties"]["payload"] = {"type": "object"}
    with pytest.raises(AssertionError):
        _assert_diagnostic_contract(document, _load_canonical_openapi())


def test_diagnostic_contract_gate_accepts_inline_response_references(
    app: FastAPI,
) -> None:
    document = deepcopy(app.openapi())
    operation = document["paths"]["/v1/repair-sessions/{sessionId}/turns"]["post"]
    operation["responses"]["401"] = {
        "description": "Authentication required",
        "content": {
            "application/json": {
                "schema": _public_schema(
                    document["components"]["schemas"]["ErrorResponse"],
                    document,
                )
            }
        },
    }
    _assert_diagnostic_contract(document, _load_canonical_openapi())
