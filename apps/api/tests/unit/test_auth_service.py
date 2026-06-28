"""Authentication service and security tests."""

from __future__ import annotations

import base64
import json
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.exc import IntegrityError

from bike_doc_api.core.config import Settings
from bike_doc_api.core.errors import AuthenticationError, UserMappingRequiredError
from bike_doc_api.core.security import (
    AuthIdentity,
    validate_bearer_authorization,
    verify_firebase_bearer_token,
)
from bike_doc_api.models.user import User
from bike_doc_api.services.auth import AuthService


class FakeUserRepository:
    """In-memory user repository for auth service tests."""

    def __init__(self) -> None:
        self.users_by_subject: dict[str, User] = {}

    async def add(self, user: User) -> User:
        user.id = user.id or f"usr_{len(self.users_by_subject) + 1}"
        user.created_at = datetime(2026, 1, 1, tzinfo=UTC)
        self.users_by_subject[user.auth_subject] = user
        return user

    async def get_by_auth_subject(self, auth_subject: str) -> User | None:
        return self.users_by_subject.get(auth_subject)


class RacingUserRepository(FakeUserRepository):
    """Repository fake that simulates a concurrent first-request create race."""

    def __init__(self) -> None:
        super().__init__()
        self.add_calls = 0

    async def add(self, user: User) -> User:
        self.add_calls += 1
        if self.add_calls == 1:
            raced_user = User(
                id="usr_raced",
                auth_subject=user.auth_subject,
                email=user.email,
                display_name=user.display_name,
                skill_level="unknown",
                created_at=datetime(2026, 1, 1, tzinfo=UTC),
            )
            self.users_by_subject[user.auth_subject] = raced_user
            raise IntegrityError("insert", {}, Exception("duplicate auth_subject"))
        return await super().add(user)


def test_fixed_dev_token_resolves_configured_identity() -> None:
    settings = Settings(
        environment="local",
        auth_mode="dev",
        dev_auth_token="test-token",
        dev_auth_subject="auth|test-user",
        dev_auth_email="test@example.com",
        dev_auth_display_name="Test User",
    )

    identity = validate_bearer_authorization(
        "Bearer test-token",
        settings=settings,
    )

    assert identity == AuthIdentity(
        subject="auth|test-user",
        email="test@example.com",
        display_name="Test User",
    )


def test_local_unsigned_jwt_resolves_identity_without_provider() -> None:
    settings = Settings(
        environment="local",
        auth_mode="local_unsigned_jwt",
    )

    identity = validate_bearer_authorization(
        f"Bearer {make_unsigned_jwt(sub='local-user', email='local@example.com')}",
        settings=settings,
    )

    assert identity == AuthIdentity(
        subject="local-user",
        email="local@example.com",
        display_name=None,
    )


def test_local_unsigned_jwt_rejects_expired_token() -> None:
    settings = Settings(
        environment="test",
        auth_mode="local_unsigned_jwt",
    )

    expired = make_unsigned_jwt(
        sub="local-user",
        email="local@example.com",
        exp=datetime.now(tz=UTC) - timedelta(minutes=5),
    )

    with pytest.raises(AuthenticationError):
        validate_bearer_authorization(f"Bearer {expired}", settings=settings)


def test_firebase_token_is_normalized_from_verified_claims(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings(
        environment="production",
        auth_mode="firebase",
        firebase_project_id="bike-doc-prod",
    )

    def fake_verify_firebase_token(
        _token: str,
        _request: object,
        *,
        audience: str | None = None,
        clock_skew_in_seconds: int = 0,
    ) -> dict[str, str | int]:
        assert audience == "bike-doc-prod"
        assert clock_skew_in_seconds == 0
        return {
            "sub": "firebase-user",
            "aud": "bike-doc-prod",
            "iss": "https://securetoken.google.com/bike-doc-prod",
            "email": "firebase@example.com",
            "name": "Firebase User",
        }

    monkeypatch.setattr(
        "bike_doc_api.core.security.google_verify_firebase_token",
        fake_verify_firebase_token,
    )

    identity = verify_firebase_bearer_token("firebase-token", settings=settings)

    assert identity == AuthIdentity(
        subject="firebase-user",
        email="firebase@example.com",
        display_name="Firebase User",
    )


def test_firebase_token_rejects_mismatched_project(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings(
        environment="production",
        auth_mode="firebase",
        firebase_project_id="bike-doc-prod",
    )

    monkeypatch.setattr(
        "bike_doc_api.core.security.google_verify_firebase_token",
        lambda *_args, **_kwargs: {
            "sub": "firebase-user",
            "aud": "other-project",
            "iss": "https://securetoken.google.com/other-project",
        },
    )

    with pytest.raises(AuthenticationError):
        verify_firebase_bearer_token("firebase-token", settings=settings)


async def test_auth_service_resolves_existing_user() -> None:
    users = FakeUserRepository()
    existing = User(
        id="usr_existing",
        auth_subject="auth|existing",
        email="existing@example.com",
        display_name="Existing User",
        skill_level="advanced",
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    users.users_by_subject[existing.auth_subject] = existing
    service = AuthService(users)

    resolved = await service.resolve_current_user(
        AuthIdentity(
            subject="auth|existing",
            email="changed@example.com",
            display_name="Changed Name",
        ),
    )

    assert resolved is existing
    assert resolved.email == "existing@example.com"
    assert resolved.display_name == "Existing User"


async def test_auth_service_auto_creates_user_from_identity() -> None:
    users = FakeUserRepository()
    service = AuthService(users)

    user = await service.resolve_current_user(
        AuthIdentity(
            subject="auth|new-user",
            email="new-user@example.com",
            display_name=None,
        ),
    )

    assert user.auth_subject == "auth|new-user"
    assert user.email == "new-user@example.com"
    assert user.display_name == "new-user"
    assert user.skill_level == "unknown"
    assert await users.get_by_auth_subject("auth|new-user") is user


async def test_auth_service_rejects_identity_without_email() -> None:
    service = AuthService(FakeUserRepository())

    with pytest.raises(UserMappingRequiredError):
        await service.resolve_current_user(
            AuthIdentity(subject="auth|missing-email", email=None),
        )


async def test_auth_service_handles_concurrent_first_request_creation() -> None:
    users = RacingUserRepository()
    rollbacks = 0

    async def rollback() -> None:
        nonlocal rollbacks
        rollbacks += 1

    service = AuthService(users, rollback=rollback)

    user = await service.resolve_current_user(
        AuthIdentity(
            subject="auth|race-user",
            email="race-user@example.com",
            display_name="Race User",
        ),
    )

    assert user.id == "usr_raced"
    assert user.auth_subject == "auth|race-user"
    assert rollbacks == 1


def make_unsigned_jwt(
    *,
    sub: str,
    email: str,
    exp: datetime | None = None,
) -> str:
    """Return an unsigned fixture JWT for local auth tests."""

    header = {"alg": "none", "typ": "JWT"}
    payload: dict[str, str | int] = {
        "sub": sub,
        "email": email,
    }
    if exp is not None:
        payload["exp"] = int(exp.timestamp())

    return ".".join(
        (
            _encode_segment(header),
            _encode_segment(payload),
            "",
        ),
    )


def _encode_segment(value: dict[str, str | int]) -> str:
    raw = json.dumps(value, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")
