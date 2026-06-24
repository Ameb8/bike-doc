"""Authentication and authorization primitives."""

from dataclasses import dataclass

from bike_doc_api.core.config import Settings
from bike_doc_api.core.errors import AuthenticationError


@dataclass(frozen=True, slots=True)
class AuthIdentity:
    """Normalized identity derived from a validated bearer token."""

    subject: str
    email: str | None = None
    display_name: str | None = None


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
) -> AuthIdentity:
    """Validate a bearer header and return a normalized auth identity."""

    token = extract_bearer_token(authorization)
    if settings.auth_mode == "dev":
        return _validate_dev_token(token, settings=settings)

    raise AuthenticationError()


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
