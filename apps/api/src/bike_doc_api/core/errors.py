"""Domain errors and FastAPI exception handler registration."""

from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
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


class NotFoundError(AppError):
    """An owner-scoped resource is missing or unavailable to this user."""

    def __init__(self, message: str = "Resource not found.") -> None:
        super().__init__(
            status_code=404,
            code="not_found",
            message=message,
        )


class ValidationAppError(AppError):
    """A public request or cursor failed validation."""

    def __init__(self, message: str = "Request validation failed.") -> None:
        super().__init__(
            status_code=422,
            code="validation_error",
            message=message,
        )


class IdempotencyConflictError(AppError):
    """A client idempotency key was reused with a different payload."""

    def __init__(self) -> None:
        super().__init__(
            status_code=409,
            code="idempotency_conflict",
            message="Idempotency key was reused with a different request payload.",
        )


class PayloadTooLargeError(AppError):
    """An upload exceeded the configured public payload limit."""

    def __init__(self) -> None:
        super().__init__(
            status_code=413,
            code="payload_too_large",
            message="Uploaded payload is too large.",
        )


class ServerError(AppError):
    """Unexpected server-side failure with a generic public response."""

    def __init__(self) -> None:
        super().__init__(
            status_code=500,
            code="server_error",
            message="Internal server error.",
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


async def request_validation_error_handler(
    _request: Request,
    exc: Exception,
) -> JSONResponse:
    """Return the public ErrorResponse envelope for request validation failures."""

    if not isinstance(exc, RequestValidationError):
        raise exc

    return JSONResponse(
        status_code=422,
        content={
            "error": {
                "code": "validation_error",
                "message": "Request validation failed.",
                "details": None,
            },
        },
    )


def install_exception_handlers(app: FastAPI) -> None:
    """Register application exception handlers."""

    app.add_exception_handler(AppError, app_error_handler)
    app.add_exception_handler(RequestValidationError, request_validation_error_handler)
