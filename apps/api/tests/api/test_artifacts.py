"""Diagnostic artifact upload API tests."""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from conftest import ApiTestUser, assert_error_response, assert_no_private_fields

OWNED_SESSION_ID = "rs_owned_contract"
NOT_OWNED_SESSION_ID = "rs_other_user"
JPEG_BYTES = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x01\x00H\x00H\x00\x00\xff\xd9"
PNG_BYTES = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR"
    b"\x00\x00\x00\x01\x00\x00\x00\x01\x08\x02\x00\x00\x00"
    b"\x90wS\xde\x00\x00\x00\x00IEND\xaeB`\x82"
)

pytestmark = pytest.mark.xfail(
    reason="Stage 5 diagnostic API tests are red until route behavior is implemented.",
)


def _artifact_form(**overrides: Any) -> dict[str, Any]:
    form = {
        "purpose": "diagnostic_photo",
        "repair_session_id": OWNED_SESSION_ID,
        "client_artifact_id": "client-artifact-001",
    }
    form.update(overrides)
    return form


async def _upload_artifact(
    api_client: httpx.AsyncClient,
    auth_headers: dict[str, str],
    *,
    data: dict[str, Any] | None = None,
    file_bytes: bytes | None = JPEG_BYTES,
    filename: str = "derailleur.jpg",
    mime_type: str = "image/jpeg",
) -> httpx.Response:
    files = None
    if file_bytes is not None:
        files = {"file": (filename, file_bytes, mime_type)}
    return await api_client.post(
        "/v1/artifacts",
        headers=auth_headers,
        data=data if data is not None else _artifact_form(),
        files=files,
    )


def _assert_diagnostic_artifact_shape(
    body: dict[str, Any],
    *,
    test_user: ApiTestUser,
    session_id: str,
    mime_type: str,
) -> None:
    artifact = body["artifact"]
    assert artifact["id"].startswith("art_") or artifact["id"]
    assert artifact["user_id"] == test_user.id
    assert artifact["repair_session_id"] == session_id
    assert artifact["bike_id"] is None
    assert artifact["purpose"] == "diagnostic_photo"
    assert artifact["media_type"] == "image"
    assert artifact["mime_type"] == mime_type
    assert artifact["duration_seconds"] is None
    assert artifact["status"] == "ready"
    assert artifact["rejection_reason"] is None
    assert_no_private_fields(body)


async def test_successful_jpeg_upload_returns_created_artifact(
    api_client: httpx.AsyncClient,
    auth_headers: dict[str, str],
    test_user: ApiTestUser,
) -> None:
    response = await _upload_artifact(api_client, auth_headers)

    assert response.status_code == 201
    _assert_diagnostic_artifact_shape(
        response.json(),
        test_user=test_user,
        session_id=OWNED_SESSION_ID,
        mime_type="image/jpeg",
    )


async def test_successful_png_upload_returns_created_artifact(
    api_client: httpx.AsyncClient,
    auth_headers: dict[str, str],
    test_user: ApiTestUser,
) -> None:
    response = await _upload_artifact(
        api_client,
        auth_headers,
        data=_artifact_form(client_artifact_id="client-artifact-png"),
        file_bytes=PNG_BYTES,
        filename="derailleur.png",
        mime_type="image/png",
    )

    assert response.status_code == 201
    _assert_diagnostic_artifact_shape(
        response.json(),
        test_user=test_user,
        session_id=OWNED_SESSION_ID,
        mime_type="image/png",
    )


async def test_diagnostic_photo_requires_repair_session_id(
    api_client: httpx.AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    response = await _upload_artifact(
        api_client,
        auth_headers,
        data={"purpose": "diagnostic_photo", "client_artifact_id": "missing-session"},
    )

    assert_error_response(response, status_code=422, error_code="validation_error")


async def test_diagnostic_photo_rejects_bike_id(
    api_client: httpx.AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    response = await _upload_artifact(
        api_client,
        auth_headers,
        data=_artifact_form(bike_id="bike_owned_contract"),
    )

    assert_error_response(response, status_code=422, error_code="validation_error")


async def test_upload_for_unknown_or_not_owned_session_returns_404(
    api_client: httpx.AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    for session_id in ["rs_missing", NOT_OWNED_SESSION_ID]:
        response = await _upload_artifact(
            api_client,
            auth_headers,
            data=_artifact_form(repair_session_id=session_id),
        )
        assert_error_response(response, status_code=404, error_code="not_found")


async def test_unsupported_mime_type_returns_422(
    api_client: httpx.AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    response = await _upload_artifact(
        api_client,
        auth_headers,
        file_bytes=b"not an image",
        filename="notes.txt",
        mime_type="text/plain",
    )

    assert_error_response(response, status_code=422, error_code="validation_error")


async def test_missing_file_returns_422(
    api_client: httpx.AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    response = await _upload_artifact(api_client, auth_headers, file_bytes=None)

    assert_error_response(response, status_code=422, error_code="validation_error")


async def test_empty_file_returns_422(
    api_client: httpx.AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    response = await _upload_artifact(api_client, auth_headers, file_bytes=b"")

    assert_error_response(response, status_code=422, error_code="validation_error")


async def test_oversize_file_returns_413(
    api_client: httpx.AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    response = await _upload_artifact(
        api_client,
        auth_headers,
        file_bytes=b"x" * (10 * 1024 * 1024 + 1),
    )

    assert_error_response(response, status_code=413, error_code="payload_too_large")


async def test_repeating_client_artifact_id_returns_original_artifact(
    api_client: httpx.AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    data = _artifact_form(client_artifact_id="client-artifact-repeat")

    first = await _upload_artifact(api_client, auth_headers, data=data)
    retry = await _upload_artifact(api_client, auth_headers, data=data)

    assert first.status_code == 201
    assert retry.status_code == 201
    assert retry.json() == first.json()


async def test_reusing_client_artifact_id_with_different_content_returns_409(
    api_client: httpx.AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    data = _artifact_form(client_artifact_id="client-artifact-conflict")

    await _upload_artifact(api_client, auth_headers, data=data)
    response = await _upload_artifact(
        api_client,
        auth_headers,
        data=data,
        file_bytes=PNG_BYTES,
        filename="derailleur.png",
        mime_type="image/png",
    )

    assert_error_response(
        response,
        status_code=409,
        error_code="idempotency_conflict",
    )


async def test_non_diagnostic_purposes_return_422_until_supported(
    api_client: httpx.AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    for purpose in [
        "verification_photo",
        "bike_profile_photo",
        "repair_reference",
        "other",
    ]:
        response = await _upload_artifact(
            api_client,
            auth_headers,
            data=_artifact_form(
                purpose=purpose,
                client_artifact_id=f"client-{purpose}",
            ),
        )
        assert_error_response(
            response,
            status_code=422,
            error_code="validation_error",
        )
