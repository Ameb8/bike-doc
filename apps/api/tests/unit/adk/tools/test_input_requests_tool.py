"""request_diagnostic_input ADK tool tests."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

from bike_doc_api.adk.tools.common import DiagnosticToolContext
from bike_doc_api.adk.tools.input_requests import RequestDiagnosticInputTool
from bike_doc_api.core.errors import (
    NotFoundError,
    SessionStateConflictError,
    StaleSessionError,
)
from bike_doc_api.schemas.repair_session import InputRequest


class _InputRequestService:
    """Fake input-request service for tool tests."""

    def __init__(self, *, error: Exception | None = None) -> None:
        self.error = error
        self.calls: list[dict[str, Any]] = []

    async def request_diagnostic_input(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        input_request = InputRequest(
            id="req_1",
            type=kwargs["request_type"],
            prompt=kwargs["prompt"],
            required=kwargs["required"],
            accepted_media_types=kwargs["accepted_media_types"],
            choices=kwargs["choices"],
            min_artifacts=kwargs["min_artifacts"],
            max_artifacts=kwargs["max_artifacts"],
            created_at=datetime(2026, 6, 21, 17, 4, tzinfo=UTC),
        )
        return SimpleNamespace(
            input_request=input_request,
            event_id="evt_1",
            event_sequence=12,
        )


def _context() -> DiagnosticToolContext:
    return DiagnosticToolContext(
        user_id="usr_tool",
        repair_session_id="rs_tool",
        diagnostic_session_id="phs_tool",
    )


async def test_request_diagnostic_input_returns_persisted_request_and_event() -> None:
    service = _InputRequestService()

    result = await RequestDiagnosticInputTool(service).run(
        {
            "repair_session_id": "rs_tool",
            "type": "photo",
            "prompt": "Upload a clear drivetrain photo.",
            "required": True,
            "accepted_media_types": ["image/jpeg", "image/png"],
            "min_artifacts": 1,
            "max_artifacts": 3,
        },
        _context(),
    )

    assert result["ok"] is True
    assert result["data"]["input_request"]["id"] == "req_1"
    assert result["data"]["event_id"] == "evt_1"
    assert result["data"]["event_sequence"] == 12
    assert service.calls[0]["diagnostic_session_id"] == "phs_tool"


async def test_request_diagnostic_input_validates_request_contract() -> None:
    service = _InputRequestService()
    tool = RequestDiagnosticInputTool(service)

    decision = await tool.run(
        {
            "repair_session_id": "rs_tool",
            "type": "decision",
            "prompt": "Choose repair path.",
        },
        _context(),
    )
    missing_media = await tool.run(
        {
            "repair_session_id": "rs_tool",
            "type": "photo",
            "prompt": "Upload a photo.",
        },
        _context(),
    )
    missing_choices = await tool.run(
        {
            "repair_session_id": "rs_tool",
            "type": "multiple_choice",
            "prompt": "Pick one.",
            "choices": [{"value": "a", "label": "A"}],
        },
        _context(),
    )
    bad_bounds = await tool.run(
        {
            "repair_session_id": "rs_tool",
            "type": "photo",
            "prompt": "Upload a photo.",
            "accepted_media_types": ["image/jpeg"],
            "min_artifacts": 3,
            "max_artifacts": 1,
        },
        _context(),
    )

    assert decision["error"]["code"] == "validation_error"
    assert missing_media["error"]["code"] == "validation_error"
    assert missing_choices["error"]["code"] == "validation_error"
    assert bad_bounds["error"]["code"] == "validation_error"
    assert service.calls == []


async def test_request_diagnostic_input_rejects_context_mismatch() -> None:
    service = _InputRequestService()

    result = await RequestDiagnosticInputTool(service).run(
        {"repair_session_id": "rs_other", "type": "none"},
        _context(),
    )

    assert result["error"]["code"] == "validation_error"
    assert service.calls == []


async def test_request_diagnostic_input_maps_domain_errors() -> None:
    missing = await RequestDiagnosticInputTool(
        _InputRequestService(error=NotFoundError()),
    ).run({"repair_session_id": "rs_tool", "type": "none"}, _context())
    invalid_phase = await RequestDiagnosticInputTool(
        _InputRequestService(error=SessionStateConflictError()),
    ).run({"repair_session_id": "rs_tool", "type": "none"}, _context())
    stale = await RequestDiagnosticInputTool(
        _InputRequestService(error=StaleSessionError()),
    ).run({"repair_session_id": "rs_tool", "type": "none"}, _context())

    assert missing["error"]["code"] == "not_found"
    assert invalid_phase["error"]["code"] == "invalid_phase"
    assert stale["error"]["code"] == "stale_session"
