"""Authentication and authorization primitives."""

from __future__ import annotations

import base64
import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol, cast

from google.auth.transport.requests import Request
from google.oauth2.id_token import verify_firebase_token as google_verify_firebase_token

from bike_doc_api.core.config import Settings
from bike_doc_api.core.errors import AuthenticationError


@dataclass(frozen=True, slots=True)
class AuthIdentity:
    """Normalized identity derived from a validated bearer token."""

    subject: str
    email: str | None = None
    display_name: str | None = None


class FirebaseTokenVerifier(Protocol):
    """Protocol for validating a Firebase bearer token."""

    def __call__(self, token: str, *, settings: Settings) -> AuthIdentity:
        """Return the normalized identity for a validated Firebase token."""


def extract_bearer_token(authorization: str | None) -> str:
    """Return the bearer token from an Authorization header."""

    if authorization is None:
        raise AuthenticationError()

    scheme, separator, token = authorization.partition(" ")
    if separator != " " or scheme.lower() != "bearer" or not token.strip():
        raise AuthenticationError()

    return token.strip()


def validate_bearer_authorization(
    authorization: str | None,
    *,
    settings: Settings,
    firebase_verifier: FirebaseTokenVerifier | None = None,
) -> AuthIdentity:
    """Validate a bearer header and return a normalized auth identity."""

    token = extract_bearer_token(authorization)
    if settings.auth_mode == "dev":
        return _validate_dev_token(token, settings=settings)
    if settings.auth_mode == "local_unsigned_jwt":
        return _validate_local_unsigned_jwt(token)

    verifier = firebase_verifier or verify_firebase_bearer_token
    return verifier(token, settings=settings)


def verify_firebase_bearer_token(token: str, *, settings: Settings) -> AuthIdentity:
    """Validate a Firebase ID token for the configured project."""

    project_id = settings.firebase_project_id
    if project_id is None:
        raise AuthenticationError()

    try:
        claims = google_verify_firebase_token(
            token,
            Request(),
            audience=project_id,
        )  # type: ignore[no-untyped-call]
    except Exception as exc:  # pragma: no cover - exercised via tests with fakes
        raise AuthenticationError() from exc

    if not isinstance(claims, Mapping):
        raise AuthenticationError()

    issuer = _normalized_optional_string(claims.get("iss"))
    expected_issuer = f"https://securetoken.google.com/{project_id}"
    if issuer != expected_issuer:
        raise AuthenticationError()

    audience = _normalized_optional_string(claims.get("aud"))
    if audience != project_id:
        raise AuthenticationError()

    subject = _normalized_optional_string(claims.get("sub"))
    if subject is None:
        raise AuthenticationError()

    return AuthIdentity(
        subject=subject,
        email=_normalized_optional_string(claims.get("email")),
        display_name=_first_non_empty_string(
            claims.get("name"),
            claims.get("display_name"),
        ),
    )


def _validate_dev_token(token: str, *, settings: Settings) -> AuthIdentity:
    """Validate the configured fixed local development token."""

    if token != settings.dev_auth_token:
        raise AuthenticationError()

    subject = settings.dev_auth_subject.strip()
    if not subject:
        raise AuthenticationError()

    return AuthIdentity(
        subject=subject,
        email=settings.dev_auth_email.strip() or None,
        display_name=settings.dev_auth_display_name.strip() or None,
    )


def _validate_local_unsigned_jwt(token: str) -> AuthIdentity:
    """Validate an unsigned local JWT fixture token."""

    header_segment, payload_segment, signature_segment = _split_jwt(token)
    if signature_segment:
        raise AuthenticationError()

    header = _decode_jwt_segment(header_segment)
    if _normalized_optional_string(header.get("alg")) != "none":
        raise AuthenticationError()

    payload = _decode_jwt_segment(payload_segment)
    _validate_local_jwt_expiry(payload)

    subject = _normalized_optional_string(payload.get("sub"))
    email = _normalized_optional_string(payload.get("email"))
    if subject is None or email is None:
        raise AuthenticationError()

    return AuthIdentity(
        subject=subject,
        email=email,
        display_name=_first_non_empty_string(
            payload.get("name"),
            payload.get("display_name"),
        ),
    )


def _split_jwt(token: str) -> tuple[str, str, str]:
    """Split a compact JWT into its three segments."""

    parts = token.split(".")
    if len(parts) != 3 or any(part == "" for part in parts[:2]):
        raise AuthenticationError()
    return parts[0], parts[1], parts[2]


def _decode_jwt_segment(segment: str) -> dict[str, Any]:
    """Decode one base64url-encoded JWT segment into an object."""

    padding = "=" * (-len(segment) % 4)
    try:
        decoded = base64.urlsafe_b64decode(segment + padding)
        value = json.loads(decoded)
    except Exception as exc:
        raise AuthenticationError() from exc

    if not isinstance(value, dict):
        raise AuthenticationError()
    return cast(dict[str, Any], value)


def _validate_local_jwt_expiry(payload: Mapping[str, Any]) -> None:
    """Reject expired local unsigned JWT fixtures when they include exp."""

    exp = payload.get("exp")
    if exp is None:
        return

    if not isinstance(exp, (int, float)):
        raise AuthenticationError()
    if datetime.fromtimestamp(exp, tz=UTC) <= datetime.now(tz=UTC):
        raise AuthenticationError()


def _normalized_optional_string(value: object) -> str | None:
    """Return a trimmed non-empty string value when available."""

    if not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized or None


def _first_non_empty_string(*values: object) -> str | None:
    """Return the first trimmed non-empty string from the provided values."""

    for value in values:
        normalized = _normalized_optional_string(value)
        if normalized is not None:
            return normalized
    return None
