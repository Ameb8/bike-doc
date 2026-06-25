"""save_diagnostic_report ADK tool tests."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

from bike_doc_api.adk.tools.common import (
    ArtifactToolNotFoundError,
    DiagnosticToolContext,
)
from bike_doc_api.adk.tools.reports import SaveDiagnosticReportTool
from bike_doc_api.core.errors import (
    NotFoundError,
    SafetyPolicyViolationError,
    SessionStateConflictError,
    StaleSessionError,
    ValidationAppError,
)
from bike_doc_api.schemas.common import (
    PhaseReportType,
    RepairSessionPhase,
    SafetySeverity,
)
from bike_doc_api.schemas.report import (
    DiagnosticReportV1,
    PhaseReportEnvelope,
    SafetyFlag,
)


class _ReportService:
    """Fake report service for tool tests."""

    def __init__(self, *, error: Exception | None = None) -> None:
        self.error = error
        self.calls: list[dict[str, Any]] = []

    async def persist_diagnostic_report_from_tool(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        payload = DiagnosticReportV1.model_validate(kwargs["payload"])
        report = PhaseReportEnvelope(
            id="rpt_1",
            repair_session_id=kwargs["repair_session_id"],
            type=PhaseReportType.DIAGNOSTIC,
            schema_version="diagnostic_report.v1",
            phase=RepairSessionPhase.DIAGNOSTIC,
            summary=kwargs["summary"],
            safety_flags=payload.safety_flags,
            source_artifact_ids=payload.key_artifact_ids,
            created_at=datetime(2026, 6, 21, 17, 5, tzinfo=UTC),
            payload=payload,
        )
        return SimpleNamespace(
            report=report,
            events=SimpleNamespace(
                phase_report_created=SimpleNamespace(id="evt_report", sequence=19),
                phase_transitioned=None,
            ),
            safety_state="ok",
            active_safety_flags=[],
        )


def _context() -> DiagnosticToolContext:
    return DiagnosticToolContext(
        user_id="usr_tool",
        user_skill_level="beginner",
        repair_session_id="rs_tool",
        diagnostic_session_id="phs_tool",
    )


def _report_payload(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "schema_version": "diagnostic_report.v1",
        "primary_diagnosis": {
            "component": "rear derailleur",
            "issue": "Cable tension appears low.",
            "confidence": "medium",
            "diy_suitability": "reasonable",
        },
        "alternate_hypotheses": [],
        "evidence_summary": "The symptom pattern points to rear indexing.",
        "key_artifact_ids": ["art_1"],
        "user_skill_level": "beginner",
        "safety_flags": [],
    }
    payload.update(overrides)
    return payload


async def test_save_diagnostic_report_injects_server_owned_session_id() -> None:
    service = _ReportService()

    result = await SaveDiagnosticReportTool(service).run(
        {
            "repair_session_id": "rs_tool",
            "summary": "Likely indexing issue.",
            "report": _report_payload(),
        },
        _context(),
    )

    assert result["ok"] is True
    assert result["data"]["report_id"] == "rpt_1"
    assert result["data"]["diagnostic_session_id"] == "phs_tool"
    assert result["data"]["phase_report_created_event_id"] == "evt_report"
    assert service.calls[0]["payload"]["diagnostic_session_id"] == "phs_tool"
    assert service.calls[0]["current_user"].id == "usr_tool"


async def test_save_diagnostic_report_rejects_agent_selected_session_id() -> None:
    service = _ReportService()

    result = await SaveDiagnosticReportTool(service).run(
        {
            "repair_session_id": "rs_tool",
            "summary": "Likely indexing issue.",
            "report": _report_payload(diagnostic_session_id="phs_agent_chosen"),
        },
        _context(),
    )

    assert result["ok"] is False
    assert result["error"]["code"] == "report_validation_failed"
    assert service.calls == []


async def test_save_diagnostic_report_rejects_context_mismatch() -> None:
    service = _ReportService()

    result = await SaveDiagnosticReportTool(service).run(
        {
            "repair_session_id": "rs_other",
            "summary": "Likely indexing issue.",
            "report": _report_payload(),
        },
        _context(),
    )

    assert result["error"]["code"] == "validation_error"
    assert service.calls == []


async def test_save_diagnostic_report_maps_domain_errors() -> None:
    cases = [
        (ValidationAppError(), "report_validation_failed"),
        (ArtifactToolNotFoundError(), "artifact_not_found"),
        (SafetyPolicyViolationError(), "safety_policy_violation"),
        (NotFoundError(), "not_found"),
        (SessionStateConflictError(), "invalid_phase"),
        (StaleSessionError(), "stale_session"),
    ]

    for error, expected_code in cases:
        result = await SaveDiagnosticReportTool(_ReportService(error=error)).run(
            {
                "repair_session_id": "rs_tool",
                "summary": "Likely indexing issue.",
                "report": _report_payload(),
            },
            _context(),
        )
        assert result["ok"] is False
        assert result["error"]["code"] == expected_code


async def test_save_diagnostic_report_returns_active_safety_flags() -> None:
    class _SafetyReportService(_ReportService):
        async def persist_diagnostic_report_from_tool(self, **kwargs: Any) -> Any:
            result = await super().persist_diagnostic_report_from_tool(**kwargs)
            result.safety_state = "blocked"
            result.active_safety_flags = [
                SafetyFlag(
                    code="brake_failure_suspected",
                    severity=SafetySeverity.BLOCKING,
                    phase=RepairSessionPhase.DIAGNOSTIC,
                    message="Do not ride until the brake is inspected.",
                    blocks_repair_instructions=True,
                ),
            ]
            return result

    result = await SaveDiagnosticReportTool(_SafetyReportService()).run(
        {
            "repair_session_id": "rs_tool",
            "summary": "Likely brake issue.",
            "report": _report_payload(key_artifact_ids=[]),
        },
        _context(),
    )

    assert result["data"]["safety_state"] == "blocked"
    assert result["data"]["safety_flags"][0]["code"] == "brake_failure_suspected"
