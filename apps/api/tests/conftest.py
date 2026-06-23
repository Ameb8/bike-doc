"""Shared API test fixtures and assertions."""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI

from bike_doc_api.core.config import Settings
from bike_doc_api.main import create_app


@dataclass(frozen=True)
class ApiTestUser:
    """Deterministic authenticated user for API contract tests."""

    id: str = "usr_contract_user"
    auth_subject: str = "auth|contract-user"
    email: str = "contract-user@example.com"
    display_name: str = "Contract User"
    skill_level: str = "unknown"


@pytest.fixture
def settings() -> Settings:
    """Return test settings for the in-process API app."""

    return Settings(
        environment="test",
        database_url="postgresql+asyncpg://bikedoc:bikedoc@localhost:5432/bikedoc_test",
        log_level="WARNING",
        log_format="console",
    )


@pytest.fixture
def app(settings: Settings) -> FastAPI:
    """Create an isolated FastAPI app for API tests."""

    app = create_app(settings)
    yield app
    app.dependency_overrides.clear()


@pytest_asyncio.fixture
async def api_client(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    """Yield an HTTPX client bound to the FastAPI ASGI app."""

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://testserver",
    ) as client:
        yield client


@pytest.fixture
def test_user() -> ApiTestUser:
    """Return the default authenticated user identity."""

    return ApiTestUser()


@pytest.fixture
def auth_headers() -> dict[str, str]:
    """Return deterministic auth headers for secured route tests."""

    return {"Authorization": "Bearer test-token"}


def assert_error_response(
    response: httpx.Response,
    *,
    status_code: int,
    error_code: str,
) -> None:
    """Assert the public ErrorResponse envelope."""

    assert response.status_code == status_code
    body = response.json()
    assert "error" in body
    assert body["error"]["code"] == error_code
    assert body["error"]["message"]


def assert_no_private_fields(payload: Any) -> None:
    """Assert public responses do not leak internal diagnostic implementation data."""

    serialized = str(payload).lower()
    forbidden_fragments = [
        "adk",
        "prompt",
        "runner",
        "tool",
        "gcs",
        "bucket",
        "storage_path",
        "storage_provider",
        "signed_url",
        "object_name",
        "model_name",
        "private_evidence",
        "raw_event",
    ]
    for fragment in forbidden_fragments:
        assert fragment not in serialized


def assert_repair_session_shape(
    session: dict[str, Any],
    *,
    user_id: str,
    bike_id: str,
) -> None:
    """Assert the public RepairSession shape used across diagnostic API tests."""

    assert session["id"].startswith("rs_") or session["id"]
    assert session["user_id"] == user_id
    assert session["bike_id"] == bike_id
    assert session["phase"] == "diagnostic"
    assert session["safety_state"] in {
        "ok",
        "caution",
        "shop_recommended",
        "blocked",
    }
    assert "current_input_request" in session
    assert "execution_progress" in session
    assert set(session["latest_reports"]) == {
        "diagnostic_report_id",
        "plan_report_id",
        "execution_report_id",
        "shop_referral_report_id",
    }
    assert "latest_event_id" in session
    assert "created_at" in session
    assert "updated_at" in session
    assert_no_private_fields(session)
