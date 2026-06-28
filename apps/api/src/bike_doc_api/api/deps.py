"""FastAPI dependencies."""

from collections.abc import AsyncIterator
from functools import lru_cache
from typing import Annotated

from fastapi import Depends, Header
from google.adk.sessions import InMemorySessionService
from sqlalchemy.ext.asyncio import AsyncSession

from bike_doc_api.adk.runner import DiagnosticRunner
from bike_doc_api.adk.sessions import DiagnosticADKSessionClient
from bike_doc_api.core.config import (
    Settings,
    get_settings,
    validate_diagnostic_runtime_configuration,
)
from bike_doc_api.core.security import validate_bearer_authorization
from bike_doc_api.db.session import get_session_for_database_url
from bike_doc_api.models.user import User
from bike_doc_api.providers.storage import (
    GCSStorageProvider,
    LocalStorageProvider,
    StorageProvider,
)
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


def get_storage_provider(
    settings: Annotated[Settings, Depends(get_settings)],
) -> StorageProvider:
    """Build the configured artifact storage provider."""

    if settings.artifact_storage_provider == "local":
        return LocalStorageProvider(settings.artifact_local_storage_root)
    assert settings.artifact_gcs_bucket is not None
    return GCSStorageProvider(bucket_name=settings.artifact_gcs_bucket)


@lru_cache
def get_adk_session_service() -> InMemorySessionService:
    """Return the process-lifetime ADK in-memory session service."""

    return InMemorySessionService()  # type: ignore[no-untyped-call]


def get_diagnostic_adk_session_client(
    session_service: Annotated[
        InMemorySessionService,
        Depends(get_adk_session_service),
    ],
    settings: Annotated[Settings, Depends(get_settings)],
) -> DiagnosticADKSessionClient:
    """Build the diagnostic ADK session client around the shared service."""

    validate_diagnostic_runtime_configuration(settings)
    return DiagnosticADKSessionClient(session_service)


def get_diagnostic_runner(
    session_service: Annotated[
        InMemorySessionService,
        Depends(get_adk_session_service),
    ],
    settings: Annotated[Settings, Depends(get_settings)],
) -> DiagnosticRunner:
    """Build the diagnostic runner with the shared ADK session service."""

    validate_diagnostic_runtime_configuration(settings)
    return DiagnosticRunner(session_service=session_service)
