"""Shared descriptions for the public HTTP error envelope."""

from typing import Any

from bike_doc_api.schemas.common import ErrorResponse

_DESCRIPTIONS = {
    401: "Authentication is required or invalid",
    404: "Resource not found",
    409: "Request conflicts with current session state",
    413: "Uploaded media is too large",
    422: "Request validation failed",
}


def error_responses(*statuses: int) -> dict[int | str, dict[str, Any]]:
    """Describe errors handled by the centralized public exception handlers."""
    return {
        status: {"model": ErrorResponse, "description": _DESCRIPTIONS[status]}
        for status in statuses
    }
