"""User repository."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bike_doc_api.models.user import User


class UserRepository:
    """Persistence operations for app users."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, user: User) -> User:
        """Add a user to the current transaction."""
        self._session.add(user)
        await self._session.flush()
        return user

    async def get(self, user_id: str) -> User | None:
        """Return a user by app ID."""
        return await self._session.get(User, user_id)

    async def get_by_auth_subject(self, auth_subject: str) -> User | None:
        """Return a user by external auth subject."""
        result = await self._session.execute(
            select(User).where(User.auth_subject == auth_subject),
        )
        return result.scalar_one_or_none()

    async def list_by_email(self, email: str, *, limit: int = 50) -> list[User]:
        """Return users matching an email address."""
        result = await self._session.execute(
            select(User).where(User.email == email).order_by(User.id).limit(limit),
        )
        return list(result.scalars().all())
