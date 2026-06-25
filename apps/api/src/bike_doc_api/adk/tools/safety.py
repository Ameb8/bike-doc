"""Diagnostic safety ADK tool boundary."""

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
from bike_doc_api.schemas.report import SafetyFlag


class RaiseSafetyFlagInput(BaseModel):
    """Internal input schema for raise_safety_flag."""

    model_config = ConfigDict(extra="forbid")

    repair_session_id: str = Field(min_length=1)
    safety_flag: dict[str, Any]


class RaisedSafetyFlagResultProtocol(Protocol):
    """Service result shape required by this tool."""

    safety_state: str
    active_safety_flags: list[SafetyFlag]
    event_id: str | None
    event_sequence: int | None


class SafetyServiceProtocol(Protocol):
    """Service boundary used by raise_safety_flag."""

    async def raise_safety_flag(
        self,
        *,
        current_user: Any,
        repair_session_id: str,
        safety_flag: Any,
    ) -> RaisedSafetyFlagResultProtocol:
        """Persist a diagnostic safety flag through server safety rules."""


class RaiseSafetyFlagTool:
    """Thin ADK wrapper for raising diagnostic safety flags."""

    def __init__(self, service: SafetyServiceProtocol) -> None:
        self._service = service

    async def run(
        self,
        tool_input: RaiseSafetyFlagInput | Mapping[str, Any],
        context: DiagnosticToolContext,
    ) -> dict[str, Any]:
        """Run raise_safety_flag and return the common tool envelope."""

        async def call() -> dict[str, Any]:
            parsed = parse_tool_input(RaiseSafetyFlagInput, tool_input)
            validate_tool_context(
                repair_session_id=parsed.repair_session_id,
                context=context,
            )
            result = await self._service.raise_safety_flag(
                current_user=current_tool_user(context),
                repair_session_id=parsed.repair_session_id,
                safety_flag=parsed.safety_flag,
            )
            data: dict[str, Any] = {
                "safety_state": result.safety_state,
                "active_safety_flags": [
                    flag.model_dump(mode="json") for flag in result.active_safety_flags
                ],
            }
            if result.event_id is not None:
                data["event_id"] = result.event_id
                data["event_sequence"] = result.event_sequence
            return tool_success(data)

        return await normalize_tool_errors(call)


async def raise_safety_flag(
    tool_input: RaiseSafetyFlagInput | Mapping[str, Any],
    *,
    context: DiagnosticToolContext,
    service: SafetyServiceProtocol,
) -> dict[str, Any]:
    """Function-style entrypoint for raise_safety_flag."""

    return await RaiseSafetyFlagTool(service).run(tool_input, context)
