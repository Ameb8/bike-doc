"""FastAPI dependencies."""

from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends, Header
from sqlalchemy.ext.asyncio import AsyncSession

from bike_doc_api.core.config import Settings, get_settings
from bike_doc_api.core.security import validate_bearer_authorization
from bike_doc_api.db.session import get_session_for_database_url
from bike_doc_api.models.user import User
from bike_doc_api.repositories.users import UserRepository
from bike_doc_api.services.auth import AuthService


async def get_db_session(
    settings: Annotated[Settings, Depends(get_settings)],
) -> AsyncIterator[AsyncSession]:
    """Yield a request-scoped database session."""

    async for session in get_session_for_database_url(settings.database_url):
        yield session


async def get_current_user(
    session: Annotated[AsyncSession, Depends(get_db_session)],
    settings: Annotated[Settings, Depends(get_settings)],
    authorization: Annotated[str | None, Header(alias="Authorization")] = None,
) -> User:
    """Resolve the authenticated app user for the current request."""

    identity = validate_bearer_authorization(authorization, settings=settings)
    return await AuthService(
        UserRepository(session),
        rollback=session.rollback,
    ).resolve_current_user(identity)
