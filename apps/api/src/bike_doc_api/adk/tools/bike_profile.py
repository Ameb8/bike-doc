"""Bike profile ADK tool boundary."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field

from bike_doc_api.adk.tools.common import (
    DiagnosticToolContext,
    current_tool_user,
    normalize_tool_errors,
    parse_tool_input,
    tool_success,
    validate_tool_context,
)


class GetBikeProfileInput(BaseModel):
    """Internal input schema for get_bike_profile."""

    model_config = ConfigDict(extra="forbid")

    repair_session_id: str = Field(min_length=1)


class BikeProfileToolData(BaseModel):
    """Bike profile fields exposed to the diagnostic agent."""

    model_config = ConfigDict(extra="ignore", from_attributes=True)

    id: str
    display_name: str
    make: str | None = None
    model: str | None = None
    model_year: int | None = None
    bike_type: str
    frame_material: str | None = None
    drivetrain: str | None = None
    brake_type: str | None = None
    wheel_size: str | None = None
    tire_size: str | None = None
    notes: str | None = None


class BikeProfileResultProtocol(Protocol):
    """Service result shape required by this tool."""

    bike_profile: Any
    user_skill_level: str


class BikeProfileServiceProtocol(Protocol):
    """Service boundary used by get_bike_profile."""

    async def get_diagnostic_bike_profile(
        self,
        *,
        current_user: Any,
        repair_session_id: str,
        diagnostic_session_id: str,
    ) -> BikeProfileResultProtocol:
        """Return bike context for the active diagnostic session."""


class GetBikeProfileTool:
    """Thin ADK wrapper for diagnostic bike profile lookup."""

    def __init__(self, service: BikeProfileServiceProtocol) -> None:
        self._service = service

    async def run(
        self,
        tool_input: GetBikeProfileInput | Mapping[str, Any],
        context: DiagnosticToolContext,
    ) -> dict[str, Any]:
        """Run get_bike_profile and return the common tool envelope."""

        async def call() -> dict[str, Any]:
            parsed = parse_tool_input(GetBikeProfileInput, tool_input)
            validate_tool_context(
                repair_session_id=parsed.repair_session_id,
                context=context,
            )
            result = await self._service.get_diagnostic_bike_profile(
                current_user=current_tool_user(context),
                repair_session_id=parsed.repair_session_id,
                diagnostic_session_id=context.diagnostic_session_id,
            )
            bike_profile = BikeProfileToolData.model_validate(result.bike_profile)
            return tool_success(
                {
                    "bike_profile": bike_profile.model_dump(mode="json"),
                    "user_skill_level": result.user_skill_level,
                },
            )

        return await normalize_tool_errors(call)


async def get_bike_profile(
    tool_input: GetBikeProfileInput | Mapping[str, Any],
    *,
    context: DiagnosticToolContext,
    service: BikeProfileServiceProtocol,
) -> dict[str, Any]:
    """Function-style entrypoint for get_bike_profile."""

    return await GetBikeProfileTool(service).run(tool_input, context)
