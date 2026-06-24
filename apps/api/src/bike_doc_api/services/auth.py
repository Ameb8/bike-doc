"""Authentication service boundary."""

from collections.abc import Awaitable, Callable
from typing import Protocol

from sqlalchemy.exc import IntegrityError

from bike_doc_api.core.errors import AuthenticationError, UserMappingRequiredError
from bike_doc_api.core.security import AuthIdentity
from bike_doc_api.models.user import User
from bike_doc_api.schemas.common import UserSkillLevel


class UserRepositoryProtocol(Protocol):
    """Persistence operations required for auth user resolution."""

    async def add(self, user: User) -> User:
        """Add a user to the current transaction."""

    async def get_by_auth_subject(self, auth_subject: str) -> User | None:
        """Return a user by external auth subject."""


class AuthService:
    """Resolve validated auth identities into app-owned users."""

    def __init__(
        self,
        users: UserRepositoryProtocol,
        *,
        rollback: Callable[[], Awaitable[None]] | None = None,
    ) -> None:
        self._users = users
        self._rollback = rollback

    async def resolve_current_user(self, identity: AuthIdentity) -> User:
        """Return the app user for a validated identity, creating it if needed."""

        subject = identity.subject.strip()
        if not subject:
            raise AuthenticationError()

        existing = await self._users.get_by_auth_subject(subject)
        if existing is not None:
            return existing

        user = User(
            auth_subject=subject,
            email=_required_email(identity.email),
            display_name=_display_name_for_identity(identity),
            skill_level=UserSkillLevel.UNKNOWN.value,
        )
        try:
            return await self._users.add(user)
        except IntegrityError:
            if self._rollback is not None:
                await self._rollback()
            raced_existing = await self._users.get_by_auth_subject(subject)
            if raced_existing is not None:
                return raced_existing
            raise


def _required_email(email: str | None) -> str:
    """Return a usable email or raise the public mapping error."""

    normalized = email.strip() if email is not None else ""
    if not normalized:
        raise UserMappingRequiredError()
    return normalized


def _display_name_for_identity(identity: AuthIdentity) -> str:
    """Return a non-empty display name derived from identity claims."""

    if identity.display_name is not None:
        display_name = identity.display_name.strip()
        if display_name:
            return display_name

    email = _required_email(identity.email)
    local_part = email.partition("@")[0].strip()
    if local_part:
        return local_part
    raise UserMappingRequiredError()
