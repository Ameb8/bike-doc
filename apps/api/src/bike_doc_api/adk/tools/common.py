"""Shared internal ADK diagnostic tool contracts."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any, Literal, TypeVar, overload

from pydantic import BaseModel, ConfigDict, ValidationError

from bike_doc_api.core.errors import (
    AppError,
    NotFoundError,
    SafetyPolicyViolationError,
    ServerError,
    SessionStateConflictError,
    StaleSessionError,
    ValidationAppError,
)
from bike_doc_api.schemas.common import RepairSessionPhase

ToolErrorCode = Literal[
    "not_found",
    "invalid_phase",
    "stale_session",
    "validation_error",
    "artifact_not_found",
    "report_validation_failed",
    "safety_policy_violation",
    "internal_error",
]
InputT = TypeVar("InputT", bound=BaseModel)


class DiagnosticToolContext(BaseModel):
    """Server-owned context provided by orchestration to diagnostic tools."""

    model_config = ConfigDict(extra="forbid")

    user_id: str
    user_skill_level: str = "unknown"
    repair_session_id: str
    active_phase: RepairSessionPhase = RepairSessionPhase.DIAGNOSTIC
    diagnostic_session_id: str
    turn_id: str | None = None


@dataclass(frozen=True, slots=True)
class ToolCurrentUser:
    """Minimal authenticated user object passed to service boundaries."""

    id: str
    skill_level: str = "unknown"


def current_tool_user(context: DiagnosticToolContext) -> ToolCurrentUser:
    """Return a minimal authenticated user object for service calls."""

    return ToolCurrentUser(
        id=context.user_id,
        skill_level=context.user_skill_level,
    )


class ToolDomainError(Exception):
    """Expected domain failure that maps directly to a tool error code."""

    def __init__(self, code: ToolErrorCode, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(message)


class StaleDiagnosticSessionError(ToolDomainError):
    """The app-owned phase session no longer matches tool context."""

    def __init__(self) -> None:
        super().__init__(
            "stale_session",
            "Diagnostic session context is stale.",
        )


class ArtifactToolNotFoundError(ToolDomainError):
    """Artifact is missing, not owned, or not attached to the session."""

    def __init__(self) -> None:
        super().__init__(
            "artifact_not_found",
            "Artifact was not found for this repair session.",
        )


class ReportValidationToolError(ToolDomainError):
    """Generated diagnostic report cannot be mapped to the public schema."""

    def __init__(self) -> None:
        super().__init__(
            "report_validation_failed",
            "Diagnostic report validation failed.",
        )


def tool_success(data: Mapping[str, Any]) -> dict[str, Any]:
    """Return the common successful tool envelope."""

    return {"ok": True, "data": dict(data)}


def tool_error(code: ToolErrorCode, message: str) -> dict[str, Any]:
    """Return the common failed tool envelope."""

    return {"ok": False, "error": {"code": code, "message": message}}


@overload
def parse_tool_input(  # noqa: UP047
    model: type[InputT],
    raw_input: InputT,
) -> InputT: ...


@overload
def parse_tool_input(  # noqa: UP047
    model: type[InputT],
    raw_input: Mapping[str, Any],
) -> InputT: ...


def parse_tool_input(  # noqa: UP047
    model: type[InputT],
    raw_input: InputT | Mapping[str, Any],
) -> InputT:
    """Validate a raw ADK tool input mapping against an internal schema."""

    if isinstance(raw_input, model):
        return raw_input
    return model.model_validate(raw_input)


def validate_tool_context(
    *,
    repair_session_id: str,
    context: DiagnosticToolContext,
) -> None:
    """Validate server-owned diagnostic context before calling services."""

    if repair_session_id != context.repair_session_id:
        raise ValidationAppError("repair_session_id does not match tool context.")
    if context.active_phase is not RepairSessionPhase.DIAGNOSTIC:
        raise SessionStateConflictError()


async def normalize_tool_errors(
    call: Callable[[], Awaitable[dict[str, Any]]],
    *,
    validation_error_code: ToolErrorCode = "validation_error",
    not_found_code: ToolErrorCode = "not_found",
) -> dict[str, Any]:
    """Map expected backend failures into the common ADK tool result shape."""

    try:
        return await call()
    except ValidationError:
        return tool_error(
            validation_error_code, _default_message(validation_error_code)
        )
    except ToolDomainError as exc:
        return tool_error(exc.code, exc.message)
    except NotFoundError:
        return tool_error(not_found_code, _default_message(not_found_code))
    except SessionStateConflictError:
        return tool_error("invalid_phase", _default_message("invalid_phase"))
    except StaleSessionError:
        return tool_error("stale_session", _default_message("stale_session"))
    except SafetyPolicyViolationError:
        return tool_error(
            "safety_policy_violation",
            _default_message("safety_policy_violation"),
        )
    except ValidationAppError:
        return tool_error(
            validation_error_code, _default_message(validation_error_code)
        )
    except ServerError:
        return tool_error("internal_error", _default_message("internal_error"))
    except AppError:
        return tool_error("internal_error", _default_message("internal_error"))
    except Exception:
        return tool_error("internal_error", _default_message("internal_error"))


def _default_message(code: ToolErrorCode) -> str:
    """Return stable, non-leaky messages for tool errors."""

    messages: dict[ToolErrorCode, str] = {
        "not_found": "Repair session was not found.",
        "invalid_phase": "Repair session is not in the diagnostic phase.",
        "stale_session": "Diagnostic session context is stale.",
        "validation_error": "Tool input validation failed.",
        "artifact_not_found": "Artifact was not found for this repair session.",
        "report_validation_failed": "Diagnostic report validation failed.",
        "safety_policy_violation": "Safety policy violation.",
        "internal_error": "Internal tool error.",
    }
    return messages[code]
