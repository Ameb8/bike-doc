"""Domain errors and FastAPI exception handler registration."""

from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse


class AppError(Exception):
    """Expected application error with a public API representation."""

    def __init__(
        self,
        *,
        status_code: int,
        code: str,
        message: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        self.status_code = status_code
        self.code = code
        self.message = message
        self.details = details


class AuthenticationError(AppError):
    """Authentication is missing or invalid."""

    def __init__(self) -> None:
        super().__init__(
            status_code=401,
            code="unauthorized",
            message="Authentication is required.",
        )


class UserMappingRequiredError(AppError):
    """A validated identity cannot be mapped to an app user."""

    def __init__(self) -> None:
        super().__init__(
            status_code=401,
            code="user_mapping_required",
            message="Authenticated identity cannot be mapped to a user.",
        )


async def app_error_handler(_request: Request, exc: Exception) -> JSONResponse:
    """Return the public ErrorResponse envelope for expected failures."""

    if not isinstance(exc, AppError):
        raise exc

    return JSONResponse(
        status_code=exc.status_code,
        content={
            "error": {
                "code": exc.code,
                "message": exc.message,
                "details": exc.details,
            },
        },
    )


def install_exception_handlers(app: FastAPI) -> None:
    """Register application exception handlers."""

    app.add_exception_handler(AppError, app_error_handler)
