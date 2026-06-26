"""Diagnostic artifact ADK tool boundary."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field, field_validator

from bike_doc_api.adk.tools.common import (
    DiagnosticToolContext,
    current_tool_user,
    normalize_tool_errors,
    parse_tool_input,
    tool_success,
    validate_tool_context,
)
from bike_doc_api.core.errors import ValidationAppError
from bike_doc_api.schemas.common import ArtifactPurpose


class ListDiagnosticArtifactsInput(BaseModel):
    """Internal input schema for list_diagnostic_artifacts."""

    model_config = ConfigDict(extra="forbid")

    repair_session_id: str = Field(min_length=1)
    purpose: str = ArtifactPurpose.DIAGNOSTIC_PHOTO.value

    @field_validator("purpose")
    @classmethod
    def validate_purpose(cls, purpose: str) -> str:
        """Diagnostic V1 supports diagnostic photos only."""

        normalized = purpose.strip()
        if normalized != ArtifactPurpose.DIAGNOSTIC_PHOTO.value:
            raise ValidationAppError("Only diagnostic photos are supported.")
        return normalized


class DiagnosticArtifactData(BaseModel):
    """Safe artifact metadata exposed to the diagnostic agent."""

    model_config = ConfigDict(extra="ignore", from_attributes=True)

    id: str
    purpose: str
    media_type: str
    mime_type: str
    filename: str
    byte_size: int
    status: str
    width: int | None = None
    height: int | None = None
    duration_seconds: float | None = None
    rejection_reason: str | None = None
    created_at: datetime


class ArtifactServiceProtocol(Protocol):
    """Service boundary used by list_diagnostic_artifacts."""

    async def list_diagnostic_artifacts(
        self,
        *,
        current_user: Any,
        repair_session_id: str,
        purpose: ArtifactPurpose,
        limit: int = 50,
    ) -> list[Any]:
        """Return diagnostic artifact metadata."""


class ListDiagnosticArtifactsTool:
    """Thin ADK wrapper for diagnostic artifact listing."""

    def __init__(self, service: ArtifactServiceProtocol) -> None:
        self._service = service

    async def run(
        self,
        tool_input: ListDiagnosticArtifactsInput | Mapping[str, Any],
        context: DiagnosticToolContext,
    ) -> dict[str, Any]:
        """Run list_diagnostic_artifacts and return the common tool envelope."""

        async def call() -> dict[str, Any]:
            parsed: ListDiagnosticArtifactsInput = parse_tool_input(
                ListDiagnosticArtifactsInput,
                tool_input,
            )
            validate_tool_context(
                repair_session_id=parsed.repair_session_id,
                context=context,
            )
            artifacts = await self._service.list_diagnostic_artifacts(
                current_user=current_tool_user(context),
                repair_session_id=parsed.repair_session_id,
                purpose=ArtifactPurpose(parsed.purpose),
            )
            safe_artifacts = [
                DiagnosticArtifactData.model_validate(artifact).model_dump(mode="json")
                for artifact in artifacts
            ]
            return tool_success({"artifacts": safe_artifacts})

        return await normalize_tool_errors(call)


async def list_diagnostic_artifacts(
    tool_input: ListDiagnosticArtifactsInput | Mapping[str, Any],
    *,
    context: DiagnosticToolContext,
    service: ArtifactServiceProtocol,
) -> dict[str, Any]:
    """Function-style entrypoint for list_diagnostic_artifacts."""

    return await ListDiagnosticArtifactsTool(service).run(tool_input, context)
