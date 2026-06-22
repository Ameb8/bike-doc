"""FastAPI application entrypoint."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from bike_doc_api.api.router import router as api_router
from bike_doc_api.core.config import Settings, get_settings
from bike_doc_api.core.errors import install_exception_handlers
from bike_doc_api.core.logging import configure_logging


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """Application lifespan hook reserved for shared resources."""
    yield


def create_app(settings: Settings | None = None) -> FastAPI:
    """Create the FastAPI application shell."""
    settings = settings or get_settings()
    configure_logging(
        environment=settings.environment,
        log_level=settings.log_level,
        log_format=settings.log_format,
    )

    app = FastAPI(
        title=settings.app_name,
        version="0.1.0",
        debug=settings.debug,
        lifespan=lifespan,
    )
    if settings.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_origins,
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )
    install_exception_handlers(app)
    app.include_router(api_router)
    return app


app = create_app()
