"""lookup_repair_history ADK tool tests."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from bike_doc_api.adk.tools.common import DiagnosticToolContext
from bike_doc_api.adk.tools.repair_history import LookupRepairHistoryTool
from bike_doc_api.core.errors import NotFoundError, SessionStateConflictError


class _RepairHistoryService:
    """Fake repair-history service for tool tests."""

    def __init__(
        self,
        *,
        entries: list[Any] | None = None,
        error: Exception | None = None,
    ) -> None:
        self.entries = entries if entries is not None else []
        self.error = error
        self.calls: list[dict[str, Any]] = []

    async def lookup_repair_history(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return SimpleNamespace(entries=self.entries)


def _context() -> DiagnosticToolContext:
    return DiagnosticToolContext(
        user_id="usr_tool",
        repair_session_id="rs_tool",
        diagnostic_session_id="phs_tool",
    )


async def test_lookup_repair_history_returns_success_shape_and_entries() -> None:
    service = _RepairHistoryService(
        entries=[
            SimpleNamespace(
                id="hist_1",
                bike_id="bike_active",
                repair_session_id="rs_previous",
                title="Rear shifting adjustment",
                summary="Indexed rear derailleur.",
                components=["rear derailleur"],
                parts_used=["shift cable"],
                tools_used=["hex key"],
                mileage=None,
                service_date="2026-05-12",
                created_at="2026-05-12T18:30:00Z",
            ),
        ],
    )

    result = await LookupRepairHistoryTool(service).run(
        {
            "repair_session_id": "rs_tool",
            "component_terms": [" rear derailleur "],
        },
        _context(),
    )

    assert result["ok"] is True
    assert result["data"]["entries"][0]["bike_id"] == "bike_active"
    assert service.calls[0]["component_terms"] == ["rear derailleur"]
    assert service.calls[0]["limit"] == 5


async def test_lookup_repair_history_can_return_empty_service_backed_list() -> None:
    result = await LookupRepairHistoryTool(_RepairHistoryService()).run(
        {"repair_session_id": "rs_tool"},
        _context(),
    )

    assert result == {"ok": True, "data": {"entries": []}}


async def test_lookup_repair_history_validates_limit_and_terms() -> None:
    service = _RepairHistoryService()

    bad_limit = await LookupRepairHistoryTool(service).run(
        {"repair_session_id": "rs_tool", "limit": 21},
        _context(),
    )
    bad_terms = await LookupRepairHistoryTool(service).run(
        {"repair_session_id": "rs_tool", "component_terms": [" "]},
        _context(),
    )

    assert bad_limit["error"]["code"] == "validation_error"
    assert bad_terms["error"]["code"] == "validation_error"
    assert service.calls == []


async def test_lookup_repair_history_maps_domain_errors() -> None:
    missing = await LookupRepairHistoryTool(
        _RepairHistoryService(error=NotFoundError()),
    ).run({"repair_session_id": "rs_tool"}, _context())
    invalid_phase = await LookupRepairHistoryTool(
        _RepairHistoryService(error=SessionStateConflictError()),
    ).run({"repair_session_id": "rs_tool"}, _context())

    assert missing["error"]["code"] == "not_found"
    assert invalid_phase["error"]["code"] == "invalid_phase"
