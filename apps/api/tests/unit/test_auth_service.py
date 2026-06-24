"""Authentication service and security tests."""

from datetime import UTC, datetime

import pytest

from bike_doc_api.core.config import Settings
from bike_doc_api.core.errors import UserMappingRequiredError
from bike_doc_api.core.security import AuthIdentity, validate_bearer_authorization
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
