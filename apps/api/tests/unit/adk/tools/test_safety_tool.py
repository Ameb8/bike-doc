"""raise_safety_flag ADK tool tests."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from bike_doc_api.adk.tools.common import DiagnosticToolContext
from bike_doc_api.adk.tools.safety import RaiseSafetyFlagTool
from bike_doc_api.core.errors import (
    NotFoundError,
    SafetyPolicyViolationError,
    SessionStateConflictError,
    ValidationAppError,
)
from bike_doc_api.schemas.report import SafetyFlag


class _SafetyService:
    """Fake safety service for tool tests."""

    def __init__(self, *, error: Exception | None = None) -> None:
        self.error = error
        self.calls: list[dict[str, Any]] = []

    async def raise_safety_flag(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return SimpleNamespace(
            safety_state="blocked",
            active_safety_flags=[
                SafetyFlag(
                    code="brake_failure_suspected",
                    severity="blocking",
                    phase="diagnostic",
                    message="Do not ride until the brake is inspected.",
                    blocks_repair_instructions=True,
                ),
            ],
            event_id="evt_safety",
            event_sequence=13,
        )


def _context() -> DiagnosticToolContext:
    return DiagnosticToolContext(
        user_id="usr_tool",
        repair_session_id="rs_tool",
        diagnostic_session_id="phs_tool",
    )


def _flag(**overrides: Any) -> dict[str, Any]:
    flag: dict[str, Any] = {
        "code": "brake_failure_suspected",
        "severity": "blocking",
        "phase": "diagnostic",
        "message": "Do not ride until the brake is inspected.",
        "blocks_repair_instructions": True,
    }
    flag.update(overrides)
    return flag


async def test_raise_safety_flag_returns_state_flags_and_event() -> None:
    service = _SafetyService()

    result = await RaiseSafetyFlagTool(service).run(
        {"repair_session_id": "rs_tool", "safety_flag": _flag()},
        _context(),
    )

    assert result["ok"] is True
    assert result["data"]["safety_state"] == "blocked"
    assert result["data"]["active_safety_flags"][0]["severity"] == "blocking"
    assert result["data"]["event_id"] == "evt_safety"
    assert service.calls[0]["current_user"].id == "usr_tool"


async def test_raise_safety_flag_rejects_context_mismatch() -> None:
    service = _SafetyService()

    result = await RaiseSafetyFlagTool(service).run(
        {"repair_session_id": "rs_other", "safety_flag": _flag()},
        _context(),
    )

    assert result["error"]["code"] == "validation_error"
    assert service.calls == []


async def test_raise_safety_flag_maps_policy_and_validation_errors() -> None:
    policy = await RaiseSafetyFlagTool(
        _SafetyService(error=SafetyPolicyViolationError()),
    ).run({"repair_session_id": "rs_tool", "safety_flag": _flag()}, _context())
    validation = await RaiseSafetyFlagTool(
        _SafetyService(error=ValidationAppError()),
    ).run({"repair_session_id": "rs_tool", "safety_flag": _flag()}, _context())

    assert policy["error"]["code"] == "safety_policy_violation"
    assert validation["error"]["code"] == "validation_error"


async def test_raise_safety_flag_maps_session_errors() -> None:
    missing = await RaiseSafetyFlagTool(_SafetyService(error=NotFoundError())).run(
        {"repair_session_id": "rs_tool", "safety_flag": _flag()},
        _context(),
    )
    invalid_phase = await RaiseSafetyFlagTool(
        _SafetyService(error=SessionStateConflictError()),
    ).run({"repair_session_id": "rs_tool", "safety_flag": _flag()}, _context())

    assert missing["error"]["code"] == "not_found"
    assert invalid_phase["error"]["code"] == "invalid_phase"
