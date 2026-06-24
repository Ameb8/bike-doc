"""Diagnostic input-request ADK tool boundary."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from bike_doc_api.adk.tools.common import (
    DiagnosticToolContext,
    current_tool_user,
    normalize_tool_errors,
    parse_tool_input,
    tool_success,
    validate_tool_context,
)
from bike_doc_api.schemas.repair_session import InputChoice, InputRequestType

_DIAGNOSTIC_REQUEST_TYPES = {
    InputRequestType.TEXT,
    InputRequestType.PHOTO,
    InputRequestType.MULTIPLE_CHOICE,
    InputRequestType.CONFIRMATION,
    InputRequestType.NONE,
}


class RequestDiagnosticInputInput(BaseModel):
    """Internal input schema for request_diagnostic_input."""

    model_config = ConfigDict(extra="forbid")

    repair_session_id: str = Field(min_length=1)
    type: InputRequestType
    prompt: str = ""
    required: bool = True
    accepted_media_types: list[str] = Field(default_factory=list)
    choices: list[InputChoice] = Field(default_factory=list)
    min_artifacts: int | None = Field(default=None, ge=0)
    max_artifacts: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def validate_diagnostic_request(self) -> Self:
        """Apply diagnostic V1 input-request rules."""

        if self.type not in _DIAGNOSTIC_REQUEST_TYPES:
            msg = "unsupported diagnostic input request type"
            raise ValueError(msg)
        self.prompt = self.prompt.strip()
        self.accepted_media_types = [
            media_type.strip().lower() for media_type in self.accepted_media_types
        ]
        if self.type is not InputRequestType.NONE and not self.prompt:
            msg = "prompt is required"
            raise ValueError(msg)
        if any(not media_type for media_type in self.accepted_media_types):
            msg = "accepted_media_types must not contain blanks"
            raise ValueError(msg)
        if self.type is InputRequestType.PHOTO and (
            not self.accepted_media_types
            or not all(
                media_type.startswith("image/")
                for media_type in self.accepted_media_types
            )
        ):
            msg = "photo requests require accepted image media types"
            raise ValueError(msg)
        if self.type is not InputRequestType.PHOTO and self.accepted_media_types:
            msg = "accepted_media_types are only supported for photo requests"
            raise ValueError(msg)
        if self.type is InputRequestType.MULTIPLE_CHOICE and len(self.choices) < 2:
            msg = "multiple_choice requests require at least two choices"
            raise ValueError(msg)
        if self.type is not InputRequestType.MULTIPLE_CHOICE and self.choices:
            msg = "choices are only supported for multiple_choice requests"
            raise ValueError(msg)
        if (
            self.min_artifacts is not None
            and self.max_artifacts is not None
            and self.min_artifacts > self.max_artifacts
        ):
            msg = "min_artifacts must be less than or equal to max_artifacts"
            raise ValueError(msg)
        if self.type is not InputRequestType.PHOTO and (
            self.min_artifacts is not None or self.max_artifacts is not None
        ):
            msg = "artifact bounds are only supported for photo requests"
            raise ValueError(msg)
        return self


class DiagnosticInputRequestResultProtocol(Protocol):
    """Service result shape required by this tool."""

    input_request: Any
    event_id: str
    event_sequence: int


class DiagnosticInputRequestServiceProtocol(Protocol):
    """Service boundary used by request_diagnostic_input."""

    async def request_diagnostic_input(
        self,
        *,
        current_user: Any,
        repair_session_id: str,
        diagnostic_session_id: str,
        request_type: InputRequestType,
        prompt: str,
        required: bool,
        accepted_media_types: list[str],
        choices: list[InputChoice],
        min_artifacts: int | None,
        max_artifacts: int | None,
    ) -> DiagnosticInputRequestResultProtocol:
        """Persist a diagnostic input request and event."""


class RequestDiagnosticInputTool:
    """Thin ADK wrapper for diagnostic input requests."""

    def __init__(self, service: DiagnosticInputRequestServiceProtocol) -> None:
        self._service = service

    async def run(
        self,
        tool_input: RequestDiagnosticInputInput | Mapping[str, Any],
        context: DiagnosticToolContext,
    ) -> dict[str, Any]:
        """Run request_diagnostic_input and return the common tool envelope."""

        async def call() -> dict[str, Any]:
            parsed = parse_tool_input(RequestDiagnosticInputInput, tool_input)
            validate_tool_context(
                repair_session_id=parsed.repair_session_id,
                context=context,
            )
            result = await self._service.request_diagnostic_input(
                current_user=current_tool_user(context),
                repair_session_id=parsed.repair_session_id,
                diagnostic_session_id=context.diagnostic_session_id,
                request_type=parsed.type,
                prompt=parsed.prompt,
                required=parsed.required,
                accepted_media_types=parsed.accepted_media_types,
                choices=parsed.choices,
                min_artifacts=parsed.min_artifacts,
                max_artifacts=parsed.max_artifacts,
            )
            input_request = result.input_request.model_dump(mode="json")
            return tool_success(
                {
                    "input_request": input_request,
                    "event_id": result.event_id,
                    "event_sequence": result.event_sequence,
                },
            )

        return await normalize_tool_errors(call)


async def request_diagnostic_input(
    tool_input: RequestDiagnosticInputInput | Mapping[str, Any],
    *,
    context: DiagnosticToolContext,
    service: DiagnosticInputRequestServiceProtocol,
) -> dict[str, Any]:
    """Function-style entrypoint for request_diagnostic_input."""

    return await RequestDiagnosticInputTool(service).run(tool_input, context)
