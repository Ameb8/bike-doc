"""Diagnostic report API tests."""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from conftest import assert_error_response, assert_no_private_fields

OWNED_SESSION_ID = "rs_owned_contract"
NOT_OWNED_SESSION_ID = "rs_other_user"
OWNED_REPORT_ID = "rpt_owned_contract"

pytestmark = pytest.mark.xfail(
    reason="Stage 5 diagnostic API tests are red until route behavior is implemented.",
)


def _assert_diagnostic_report_envelope(report: dict[str, Any]) -> None:
    assert report["id"].startswith("rpt_") or report["id"]
    assert report["repair_session_id"] == OWNED_SESSION_ID
    assert report["type"] == "diagnostic"
    assert report["schema_version"] == "diagnostic_report.v1"
    assert report["phase"] == "diagnostic"
    assert report["summary"]
    assert "safety_flags" in report
    assert "source_artifact_ids" in report
    assert "created_at" in report
    payload = report["payload"]
    assert payload["schema_version"] == "diagnostic_report.v1"
    assert payload["primary_diagnosis"]
    assert "alternate_hypotheses" in payload
    assert payload["evidence_summary"]
    assert "key_artifact_ids" in payload
    assert payload["user_skill_level"] in {
        "unknown",
        "beginner",
        "intermediate",
        "advanced",
    }
    assert "safety_flags" in payload
    assert payload["diagnostic_session_id"]
    assert report["safety_flags"] == payload["safety_flags"]
    assert_no_private_fields(report)


async def test_list_reports_for_owned_session_returns_diagnostic_report_items(
    api_client: httpx.AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    response = await api_client.get(
        f"/v1/repair-sessions/{OWNED_SESSION_ID}/reports",
        headers=auth_headers,
    )

    assert response.status_code == 200
    body = response.json()
    assert "items" in body
    assert "next_cursor" in body
    assert body["items"]
    _assert_diagnostic_report_envelope(body["items"][0])


async def test_get_report_by_id_returns_same_public_envelope_shape(
    api_client: httpx.AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    response = await api_client.get(
        f"/v1/repair-sessions/{OWNED_SESSION_ID}/reports/{OWNED_REPORT_ID}",
        headers=auth_headers,
    )

    assert response.status_code == 200
    _assert_diagnostic_report_envelope(response.json())


async def test_list_reports_for_unknown_or_not_owned_session_returns_404(
    api_client: httpx.AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    for session_id in ["rs_missing", NOT_OWNED_SESSION_ID]:
        response = await api_client.get(
            f"/v1/repair-sessions/{session_id}/reports",
            headers=auth_headers,
        )
        assert_error_response(response, status_code=404, error_code="not_found")


async def test_get_unknown_or_wrong_session_report_returns_404(
    api_client: httpx.AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    for session_id, report_id in [
        (OWNED_SESSION_ID, "rpt_missing"),
        (OWNED_SESSION_ID, "rpt_other_session"),
        (NOT_OWNED_SESSION_ID, OWNED_REPORT_ID),
    ]:
        response = await api_client.get(
            f"/v1/repair-sessions/{session_id}/reports/{report_id}",
            headers=auth_headers,
        )
        assert_error_response(response, status_code=404, error_code="not_found")


async def test_invalid_report_pagination_parameters_return_422(
    api_client: httpx.AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    for params in [{"limit": 0}, {"limit": 101}, {"cursor": ""}]:
        response = await api_client.get(
            f"/v1/repair-sessions/{OWNED_SESSION_ID}/reports",
            headers=auth_headers,
            params=params,
        )
        assert_error_response(
            response,
            status_code=422,
            error_code="validation_error",
        )
