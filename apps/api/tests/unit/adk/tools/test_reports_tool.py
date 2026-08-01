"""save_diagnostic_report ADK tool tests."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest

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
        "repair_estimate": {
            "difficulty": "easy",
            "difficulty_notes": "Cable tension adjustment is beginner-friendly.",
            "tools_required": ["bike stand or safe way to lift rear wheel"],
            "parts_required": [],
            "repair_time": {"low_minutes": 10, "high_minutes": 30},
            "shop_repair_cost": {
                "low_usd": 20,
                "high_usd": 60,
                "notes": "Estimate only; actual shop pricing varies.",
            },
        },
        "key_artifact_ids": ["art_1"],
        "user_skill_level": "beginner",
        "safety_flags": [],
    }
    payload.update(overrides)
    return payload


def _completion_basis(**overrides: Any) -> dict[str, Any]:
    basis: dict[str, Any] = {
        "completion_reason": "diagnosis_supported",
        "material_hypotheses_considered": ["rear derailleur indexing"],
        "readily_obtainable_material_evidence_missing": False,
        "why_ready": "The symptom pattern supports the diagnosis.",
    }
    basis.update(overrides)
    return basis


async def test_save_diagnostic_report_injects_server_owned_session_id() -> None:
    service = _ReportService()

    result = await SaveDiagnosticReportTool(service).run(
        {
            "repair_session_id": "rs_tool",
            "summary": "Likely indexing issue.",
            "report": _report_payload(),
            "completion_basis": _completion_basis(),
        },
        _context(),
    )

    assert result["ok"] is True
    assert result["data"]["report_id"] == "rpt_1"
    assert result["data"]["diagnostic_session_id"] == "phs_tool"
    assert result["data"]["phase_report_created_event_id"] == "evt_report"
    assert "completion_basis" not in result["data"]
    assert service.calls[0]["payload"]["diagnostic_session_id"] == "phs_tool"
    assert "completion_basis" not in service.calls[0]["payload"]
    assert service.calls[0]["current_user"].id == "usr_tool"


async def test_save_diagnostic_report_rejects_agent_selected_session_id() -> None:
    service = _ReportService()

    result = await SaveDiagnosticReportTool(service).run(
        {
            "repair_session_id": "rs_tool",
            "summary": "Likely indexing issue.",
            "report": _report_payload(diagnostic_session_id="phs_agent_chosen"),
            "completion_basis": _completion_basis(),
        },
        _context(),
    )

    assert result["ok"] is False
    assert result["error"]["code"] == "report_validation_failed"
    assert service.calls == []


async def test_save_diagnostic_report_returns_field_validation_details() -> None:
    service = _ReportService()

    result = await SaveDiagnosticReportTool(service).run(
        {
            "repair_session_id": "rs_tool",
            "summary": "Likely indexing issue.",
            "report": _report_payload(primary_diagnosis="stiff chain link"),
            "completion_basis": _completion_basis(),
        },
        _context(),
    )

    assert result["ok"] is False
    assert result["error"]["code"] == "report_validation_failed"
    assert result["error"]["details"]["fields"] == [
        {
            "path": "report.primary_diagnosis",
            "message": "Input should be a valid dictionary or instance of Diagnosis",
            "type": "model_type",
        },
    ]
    assert service.calls == []


async def test_save_diagnostic_report_rejects_context_mismatch() -> None:
    service = _ReportService()

    result = await SaveDiagnosticReportTool(service).run(
        {
            "repair_session_id": "rs_other",
            "summary": "Likely indexing issue.",
            "report": _report_payload(),
            "completion_basis": _completion_basis(),
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
                "completion_basis": _completion_basis(),
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
            "completion_basis": _completion_basis(),
        },
        _context(),
    )

    assert result["data"]["safety_state"] == "blocked"
    assert result["data"]["safety_flags"][0]["code"] == "brake_failure_suspected"


async def test_save_report_requires_completion_basis_before_persistence() -> None:
    service = _ReportService()

    result = await SaveDiagnosticReportTool(service).run(
        {
            "repair_session_id": "rs_tool",
            "summary": "Likely indexing issue.",
            "report": _report_payload(),
        },
        _context(),
    )

    assert result["ok"] is False
    assert result["error"]["code"] == "report_validation_failed"
    assert result["error"]["details"]["fields"][0]["path"] == "completion_basis"
    assert service.calls == []


async def test_supported_completion_requires_primary_diagnosis_before_persistence() -> (
    None
):
    service = _ReportService()

    result = await SaveDiagnosticReportTool(service).run(
        {
            "repair_session_id": "rs_tool",
            "summary": "Likely indexing issue.",
            "report": _report_payload(primary_diagnosis=None),
            "completion_basis": _completion_basis(),
        },
        _context(),
    )

    assert result["ok"] is False
    assert result["error"]["code"] == "report_validation_failed"
    assert result["error"]["details"]["fields"][0]["path"] == (
        "report.primary_diagnosis"
    )
    assert service.calls == []


async def test_save_diagnostic_report_preserves_active_phase_validation() -> None:
    service = _ReportService()
    invalid_context = _context().model_copy(
        update={"active_phase": RepairSessionPhase.PLANNING},
    )

    result = await SaveDiagnosticReportTool(service).run(
        {
            "repair_session_id": "rs_tool",
            "summary": "Likely indexing issue.",
            "report": _report_payload(),
            "completion_basis": _completion_basis(),
        },
        invalid_context,
    )

    assert result["ok"] is False
    assert result["error"]["code"] == "invalid_phase"
    assert service.calls == []


@pytest.mark.parametrize(
    ("basis", "field"),
    [
        (_completion_basis(material_hypotheses_considered=[]), "completion_basis"),
        (_completion_basis(material_hypotheses_considered=["  "]), "completion_basis"),
        (_completion_basis(why_ready="  "), "completion_basis.why_ready"),
        (_completion_basis(completion_reason="unsupported"), "completion_basis"),
        (
            _completion_basis(readily_obtainable_material_evidence_missing=True),
            "completion_basis",
        ),
    ],
)
async def test_supported_completion_rejects_invalid_basis_before_persistence(
    basis: dict[str, Any],
    field: str,
) -> None:
    service = _ReportService()

    result = await SaveDiagnosticReportTool(service).run(
        {
            "repair_session_id": "rs_tool",
            "summary": "Likely indexing issue.",
            "report": _report_payload(),
            "completion_basis": basis,
        },
        _context(),
    )

    assert result["ok"] is False
    assert result["error"]["code"] == "report_validation_failed"
    assert any(
        detail["path"].startswith(field)
        for detail in result["error"]["details"]["fields"]
    )
    assert service.calls == []


@pytest.mark.parametrize(
    "completion_reason",
    [
        "user_declined_more_input",
        "requested_input_unavailable",
        "in_person_assessment_required",
    ],
)
async def test_limited_completion_reasons_allow_material_evidence_gap(
    completion_reason: str,
) -> None:
    service = _ReportService()

    result = await SaveDiagnosticReportTool(service).run(
        {
            "repair_session_id": "rs_tool",
            "summary": "Further inspection is needed.",
            "report": _report_payload(),
            "completion_basis": _completion_basis(
                completion_reason=completion_reason,
                material_hypotheses_considered=[],
                readily_obtainable_material_evidence_missing=True,
            ),
        },
        _context(),
    )

    assert result["ok"] is True
    assert len(service.calls) == 1
    assert "completion_basis" not in service.calls[0]["payload"]
