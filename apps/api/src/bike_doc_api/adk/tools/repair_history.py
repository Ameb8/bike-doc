"""Repair history ADK tool boundary."""

from __future__ import annotations

from collections.abc import Mapping
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


class LookupRepairHistoryInput(BaseModel):
    """Internal input schema for lookup_repair_history."""

    model_config = ConfigDict(extra="forbid")

    repair_session_id: str = Field(min_length=1)
    component_terms: list[str] = Field(default_factory=list)
    limit: int = Field(default=5, ge=1, le=20)

    @field_validator("component_terms")
    @classmethod
    def validate_component_terms(cls, terms: list[str]) -> list[str]:
        """Trim and reject malformed component search terms."""

        normalized = [term.strip() for term in terms]
        if any(not term for term in normalized):
            msg = "component_terms must not contain blank terms"
            raise ValueError(msg)
        return normalized


class RepairHistoryEntryData(BaseModel):
    """Repair-history entry fields exposed to the diagnostic agent."""

    model_config = ConfigDict(extra="ignore", from_attributes=True)

    id: str
    bike_id: str
    repair_session_id: str | None = None
    title: str
    summary: str
    components: list[str]
    parts_used: list[str]
    tools_used: list[str]
    mileage: int | None = None
    service_date: str | None = None
    created_at: str


class RepairHistoryResultProtocol(Protocol):
    """Service result shape required by this tool."""

    entries: list[Any]


class RepairHistoryServiceProtocol(Protocol):
    """Service boundary used by lookup_repair_history."""

    async def lookup_repair_history(
        self,
        *,
        current_user: Any,
        repair_session_id: str,
        diagnostic_session_id: str,
        component_terms: list[str],
        limit: int,
    ) -> RepairHistoryResultProtocol:
        """Return repair history for the active diagnostic session bike."""


class LookupRepairHistoryTool:
    """Thin ADK wrapper for diagnostic repair-history lookup."""

    def __init__(self, service: RepairHistoryServiceProtocol) -> None:
        self._service = service

    async def run(
        self,
        tool_input: LookupRepairHistoryInput | Mapping[str, Any],
        context: DiagnosticToolContext,
    ) -> dict[str, Any]:
        """Run lookup_repair_history and return the common tool envelope."""

        async def call() -> dict[str, Any]:
            parsed: LookupRepairHistoryInput = parse_tool_input(
                LookupRepairHistoryInput,
                tool_input,
            )
            validate_tool_context(
                repair_session_id=parsed.repair_session_id,
                context=context,
            )
            result = await self._service.lookup_repair_history(
                current_user=current_tool_user(context),
                repair_session_id=parsed.repair_session_id,
                diagnostic_session_id=context.diagnostic_session_id,
                component_terms=parsed.component_terms,
                limit=parsed.limit,
            )
            entries = [
                RepairHistoryEntryData.model_validate(entry).model_dump(mode="json")
                for entry in result.entries
            ]
            return tool_success({"entries": entries})

        return await normalize_tool_errors(call)


async def lookup_repair_history(
    tool_input: LookupRepairHistoryInput | Mapping[str, Any],
    *,
    context: DiagnosticToolContext,
    service: RepairHistoryServiceProtocol,
) -> dict[str, Any]:
    """Function-style entrypoint for lookup_repair_history."""

    return await LookupRepairHistoryTool(service).run(tool_input, context)
