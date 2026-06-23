"""OpenAPI contract tests for the diagnostic API slice."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml
from fastapi import FastAPI

DIAGNOSTIC_OPERATIONS = {
    "/v1/repair-sessions": {"post"},
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


@pytest.mark.xfail(
    reason="Stage 5 diagnostic routes are specified before implementation.",
)
def test_app_openapi_contains_diagnostic_slice_paths(app: FastAPI) -> None:
    openapi = app.openapi()

    for path, methods in DIAGNOSTIC_OPERATIONS.items():
        assert path in openapi["paths"]
        for method in methods:
            assert method in openapi["paths"][path]


@pytest.mark.xfail(
    reason="Stage 5 diagnostic routes are specified before implementation.",
)
def test_diagnostic_openapi_operations_match_canonical_contract(
    app: FastAPI,
) -> None:
    canonical = _diagnostic_contract(_load_canonical_openapi())
    actual = _diagnostic_contract(app.openapi())

    assert actual == canonical
