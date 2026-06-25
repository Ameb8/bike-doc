"""get_bike_profile ADK tool tests."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from bike_doc_api.adk.tools.bike_profile import GetBikeProfileTool
from bike_doc_api.adk.tools.common import DiagnosticToolContext
from bike_doc_api.core.errors import (
    NotFoundError,
    SessionStateConflictError,
    StaleSessionError,
)


class _BikeProfileService:
    """Fake bike-profile service for tool tests."""

    def __init__(self, *, error: Exception | None = None) -> None:
        self.error = error
        self.calls: list[dict[str, Any]] = []

    async def get_diagnostic_bike_profile(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return SimpleNamespace(
            bike_profile=SimpleNamespace(
                id="bike_active",
                display_name="Commuter",
                make="Surly",
                model="Straggler",
                model_year=2021,
                bike_type="gravel",
                frame_material="steel",
                drivetrain="Shimano 2x10",
                brake_type="mechanical_disc",
                wheel_size="700c",
                tire_size="700x38",
                notes="Rear rack.",
                user_id="usr_hidden",
            ),
            user_skill_level="beginner",
        )


def _context() -> DiagnosticToolContext:
    return DiagnosticToolContext(
        user_id="usr_tool",
        user_skill_level="beginner",
        repair_session_id="rs_tool",
        diagnostic_session_id="phs_tool",
    )


async def test_get_bike_profile_returns_success_shape_and_active_profile() -> None:
    service = _BikeProfileService()

    result = await GetBikeProfileTool(service).run(
        {"repair_session_id": "rs_tool"},
        _context(),
    )

    assert result["ok"] is True
    assert result["data"]["bike_profile"]["id"] == "bike_active"
    assert result["data"]["bike_profile"]["display_name"] == "Commuter"
    assert "user_id" not in result["data"]["bike_profile"]
    assert result["data"]["user_skill_level"] == "beginner"
    assert service.calls[0]["current_user"].id == "usr_tool"
    assert service.calls[0]["diagnostic_session_id"] == "phs_tool"


async def test_get_bike_profile_rejects_repair_session_id_mismatch() -> None:
    service = _BikeProfileService()

    result = await GetBikeProfileTool(service).run(
        {"repair_session_id": "rs_other"},
        _context(),
    )

    assert result["ok"] is False
    assert result["error"]["code"] == "validation_error"
    assert service.calls == []


async def test_get_bike_profile_maps_missing_session_to_not_found() -> None:
    result = await GetBikeProfileTool(_BikeProfileService(error=NotFoundError())).run(
        {"repair_session_id": "rs_tool"},
        _context(),
    )

    assert result["ok"] is False
    assert result["error"]["code"] == "not_found"


async def test_get_bike_profile_maps_non_diagnostic_phase_to_invalid_phase() -> None:
    result = await GetBikeProfileTool(
        _BikeProfileService(error=SessionStateConflictError()),
    ).run({"repair_session_id": "rs_tool"}, _context())

    assert result["ok"] is False
    assert result["error"]["code"] == "invalid_phase"


async def test_get_bike_profile_maps_stale_phase_session() -> None:
    result = await GetBikeProfileTool(
        _BikeProfileService(error=StaleSessionError()),
    ).run({"repair_session_id": "rs_tool"}, _context())

    assert result["ok"] is False
    assert result["error"]["code"] == "stale_session"
