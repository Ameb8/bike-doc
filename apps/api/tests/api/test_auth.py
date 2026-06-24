"""Auth route behavior tests."""

from conftest import ApiTestUser, assert_error_response
from fastapi import FastAPI
from httpx import AsyncClient

from bike_doc_api.api.deps import get_current_user


async def test_get_me_returns_overridden_current_user(
    api_client: AsyncClient,
    auth_headers: dict[str, str],
    test_user: ApiTestUser,
) -> None:
    response = await api_client.get("/v1/me", headers=auth_headers)

    assert response.status_code == 200
    assert response.json() == {
        "id": test_user.id,
        "email": test_user.email,
        "display_name": test_user.display_name,
        "skill_level": test_user.skill_level,
        "created_at": "2026-01-01T00:00:00Z",
    }


async def test_get_me_rejects_missing_auth(
    app: FastAPI,
    api_client: AsyncClient,
) -> None:
    app.dependency_overrides.pop(get_current_user, None)

    response = await api_client.get("/v1/me")

    assert_error_response(response, status_code=401, error_code="unauthorized")


async def test_get_me_rejects_invalid_auth(
    app: FastAPI,
    api_client: AsyncClient,
) -> None:
    app.dependency_overrides.pop(get_current_user, None)

    response = await api_client.get(
        "/v1/me",
        headers={"Authorization": "Bearer invalid-token"},
    )

    assert_error_response(response, status_code=401, error_code="unauthorized")
