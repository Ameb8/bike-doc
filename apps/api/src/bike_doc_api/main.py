"""FastAPI application entrypoint."""

from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from bike_doc_api.api.middleware import install_request_logging
from bike_doc_api.api.router import router as api_router
from bike_doc_api.core.config import (
    Settings,
    get_settings,
    validate_artifact_storage_runtime_configuration,
)
from bike_doc_api.core.errors import install_exception_handlers
from bike_doc_api.core.logging import configure_logging
from bike_doc_api.core.telemetry import TelemetryRuntime, initialize_telemetry

logger = structlog.get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Start telemetry before routes can schedule background work."""

    runtime = app.state.telemetry_initializer(app.state.settings)
    app.state.telemetry_runtime = runtime
    try:
        yield
    finally:
        await runtime.shutdown()


def create_app(
    settings: Settings | None = None,
    *,
    telemetry_initializer: Callable[
        [Settings], TelemetryRuntime
    ] = initialize_telemetry,
) -> FastAPI:
    """Create the FastAPI application shell."""
    settings = settings or get_settings()
    validate_artifact_storage_runtime_configuration(settings)
    configure_logging(
        environment=settings.environment,
        log_level=settings.log_level,
        log_format=settings.log_format,
        diagnostic_log_level=settings.diagnostic_log_level,
    )
    logger.info(
        "diagnostic_report_version_configured",
        version=settings.diagnostic_report_version,
    )

    app = FastAPI(
        title=settings.app_name,
        version="0.1.0",
        debug=settings.debug,
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.telemetry_initializer = telemetry_initializer
    app.dependency_overrides[get_settings] = lambda: settings
    if settings.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_origins,
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )
    install_request_logging(app)
    install_exception_handlers(app)
    app.include_router(api_router)
    return app


app = create_app()
