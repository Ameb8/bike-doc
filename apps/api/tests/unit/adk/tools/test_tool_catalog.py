"""Diagnostic ADK FunctionTool catalog tests."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

from google.adk.tools import FunctionTool

from bike_doc_api.adk.tools.tool_catalog import (
    V2_DIAGNOSTIC_TOOL_NAMES,
    DiagnosticAgentToolDependencies,
    build_tool_catalog,
)
from bike_doc_api.core.errors import NotFoundError
from bike_doc_api.schemas.common import (
    PhaseReportType,
    RepairSessionPhase,
    SafetySeverity,
)
from bike_doc_api.schemas.repair_session import InputRequest
from bike_doc_api.schemas.report import (
    DiagnosticReportV1,
    DiagnosticReportV2,
    PhaseReportEnvelope,
    SafetyFlag,
)


class _CatalogService:
    """Fake service graph for catalog wrapper tests."""

    def __init__(self, *, error: Exception | None = None) -> None:
        self.error = error
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def get_diagnostic_bike_profile(self, **kwargs: Any) -> Any:
        self._record("get_diagnostic_bike_profile", kwargs)
        return SimpleNamespace(
            bike_profile=SimpleNamespace(
                id="bike_1",
                display_name="Commuter",
                bike_type="gravel",
            ),
            user_skill_level="beginner",
        )

    async def lookup_repair_history(self, **kwargs: Any) -> Any:
        self._record("lookup_repair_history", kwargs)
        return SimpleNamespace(entries=[])

    async def list_diagnostic_artifacts(self, **kwargs: Any) -> list[Any]:
        self._record("list_diagnostic_artifacts", kwargs)
        return []

    async def request_diagnostic_input(self, **kwargs: Any) -> Any:
        self._record("request_diagnostic_input", kwargs)
        input_request = InputRequest(
            id="req_1",
            type=kwargs["request_type"],
            prompt=kwargs["prompt"],
            required=kwargs["required"],
            accepted_media_types=kwargs["accepted_media_types"],
            choices=kwargs["choices"],
            min_artifacts=kwargs["min_artifacts"],
            max_artifacts=kwargs["max_artifacts"],
            created_at=datetime(2026, 6, 25, 12, tzinfo=UTC),
        )
        return SimpleNamespace(
            input_request=input_request,
            event_id="evt_request",
            event_sequence=10,
        )

    async def raise_safety_flag(self, **kwargs: Any) -> Any:
        self._record("raise_safety_flag", kwargs)
        return SimpleNamespace(
            safety_state="blocked",
            active_safety_flags=[
                SafetyFlag(
                    code="brake_failure_suspected",
                    severity=SafetySeverity.BLOCKING,
                    phase=RepairSessionPhase.DIAGNOSTIC,
                    message="Do not ride until inspected.",
                    blocks_repair_instructions=True,
                ),
            ],
            event_id="evt_safety",
            event_sequence=11,
        )

    async def persist_diagnostic_report_from_tool(self, **kwargs: Any) -> Any:
        self._record("persist_diagnostic_report_from_tool", kwargs)
        payload_data = dict(kwargs["payload"])
        payload_data["diagnostic_session_id"] = kwargs["diagnostic_session_id"]
        if payload_data["schema_version"] == "diagnostic_report.v2":
            payload_data["diagnostic_outcome"] = kwargs["completion_reason"]
            payload = DiagnosticReportV2.model_validate(payload_data)
            summary = payload.evidence_summary
        else:
            payload = DiagnosticReportV1.model_validate(payload_data)
            summary = kwargs["summary"]
        report = PhaseReportEnvelope(
            id="rpt_1",
            repair_session_id=kwargs["repair_session_id"],
            type=PhaseReportType.DIAGNOSTIC,
            schema_version=payload.schema_version,
            phase=RepairSessionPhase.DIAGNOSTIC,
            summary=summary,
            safety_flags=payload.safety_flags,
            source_artifact_ids=payload.key_artifact_ids,
            created_at=datetime(2026, 6, 25, 12, 1, tzinfo=UTC),
            payload=payload,
        )
        return SimpleNamespace(
            report=report,
            events=SimpleNamespace(
                phase_report_created=SimpleNamespace(id="evt_report", sequence=12),
                phase_transitioned=None,
            ),
            safety_state="ok",
            active_safety_flags=[],
        )

    def _record(self, name: str, kwargs: dict[str, Any]) -> None:
        self.calls.append((name, kwargs))
        if self.error is not None:
            raise self.error


def _dependencies(service: _CatalogService) -> DiagnosticAgentToolDependencies:
    return DiagnosticAgentToolDependencies(
        bike_profile_service=service,
        repair_history_service=service,
        artifact_service=service,
        input_request_service=service,
        safety_service=service,
        report_service=service,
    )


def _tool_context(app_context: dict[str, Any] | None = None) -> Any:
    return SimpleNamespace(
        state={
            "app_context": app_context
            if app_context is not None
            else {
                "user_id": "usr_server",
                "user_skill_level": "beginner",
                "repair_session_id": "rs_server",
                "diagnostic_session_id": "phs_server",
                "turn_id": "turn_server",
            },
        },
    )


def _tool_by_name(tools: tuple[FunctionTool, ...], name: str) -> FunctionTool:
    for tool in tools:
        if tool.name == name:
            return tool
    raise AssertionError(f"Missing tool {name}")


def _report_payload(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "schema_version": "diagnostic_report.v2",
        "reported_symptoms": ["The chain skips under load."],
        "primary_diagnosis": {
            "component": "chain",
            "issue": "Chain wear causes skipping.",
            "confidence": "medium",
            "diy_suitability": "reasonable",
            "supporting_finding_ids": ["reported-symptom"],
        },
        "contributing_factors": [],
        "observed_findings": [
            {
                "finding_id": "reported-symptom",
                "component": "drivetrain",
                "finding": "The user reports skipping under load.",
                "evidence_source": "user_report",
                "evidence_source_detail": None,
                "relationship_to_symptoms": "supports_primary_diagnosis",
                "artifact_ids": [],
            }
        ],
        "alternate_hypotheses": [],
        "unresolved_uncertainties": [],
        "evidence_summary": "The symptom pattern points to rear indexing.",
        "key_artifact_ids": [],
        "user_skill_level": "beginner",
        "safety_flags": [],
    }
    payload.update(overrides)
    return payload


def _completion_basis() -> dict[str, Any]:
    return {
        "completion_reason": "diagnosis_supported",
        "material_hypotheses_considered": ["rear derailleur indexing"],
        "readily_obtainable_material_evidence_missing": False,
        "why_ready": "The symptom pattern supports the diagnosis.",
    }


def _safety_flag() -> dict[str, Any]:
    return {
        "code": "brake_failure_suspected",
        "severity": "blocking",
        "phase": "diagnostic",
        "message": "Do not ride until inspected.",
        "blocks_repair_instructions": True,
    }


async def test_build_tool_catalog_returns_exact_v2_function_tools() -> None:
    tools = build_tool_catalog(_dependencies(_CatalogService()))

    assert all(isinstance(tool, FunctionTool) for tool in tools)
    assert tuple(tool.name for tool in tools) == V2_DIAGNOSTIC_TOOL_NAMES


async def test_save_diagnostic_report_declares_internal_completion_basis_schema() -> (
    None
):
    tool = _tool_by_name(
        build_tool_catalog(_dependencies(_CatalogService())),
        "save_diagnostic_report",
    )

    declaration = tool._get_declaration()
    schema = declaration.parameters_json_schema

    assert schema["properties"]["report"] == {
        "$ref": "#/$defs/DiagnosticReportV2ToolPayload"
    }
    assert schema["properties"]["completion_basis"] == {
        "$ref": "#/$defs/CompletionBasis",
    }
    completion_basis_schema = schema["$defs"]["CompletionBasis"]
    assert completion_basis_schema["additionalProperties"] is False
    assert completion_basis_schema["required"] == [
        "completion_reason",
        "material_hypotheses_considered",
        "readily_obtainable_material_evidence_missing",
        "why_ready",
    ]
    report_schema = schema["$defs"]["DiagnosticReportV2ToolPayload"]
    assert report_schema["additionalProperties"] is False
    assert report_schema["required"] == [
        "schema_version",
        "reported_symptoms",
        "primary_diagnosis",
        "contributing_factors",
        "observed_findings",
        "alternate_hypotheses",
        "unresolved_uncertainties",
        "evidence_summary",
        "key_artifact_ids",
        "user_skill_level",
        "safety_flags",
    ]
    assert "repair_estimate" not in report_schema["properties"]
    assert (
        "diagnostic_outcome"
        not in schema["$defs"]["DiagnosticReportV2ToolPayload"]["properties"]
    )
    assert (
        "diagnostic_session_id"
        not in schema["$defs"]["DiagnosticReportV2ToolPayload"]["properties"]
    )


async def test_tool_catalog_requires_server_owned_context_from_adk_state() -> None:
    service = _CatalogService()
    tool = _tool_by_name(build_tool_catalog(_dependencies(service)), "get_bike_profile")
    empty_tool_context: Any = SimpleNamespace(state={})

    missing = await tool.run_async(args={}, tool_context=empty_tool_context)
    malformed = await tool.run_async(
        args={},
        tool_context=_tool_context({"user_id": "usr_only"}),
    )

    assert missing["ok"] is False
    assert missing["error"]["code"] == "validation_error"
    assert malformed["ok"] is False
    assert malformed["error"]["code"] == "validation_error"
    assert service.calls == []


async def test_tool_catalog_accepts_complete_runner_app_context() -> None:
    service = _CatalogService()
    tool = _tool_by_name(build_tool_catalog(_dependencies(service)), "get_bike_profile")

    result = await tool.run_async(
        args={},
        tool_context=_tool_context(
            {
                "user_id": "usr_server",
                "user_skill_level": "beginner",
                "repair_session_id": "rs_server",
                "active_phase": "diagnostic",
                "diagnostic_session_id": "phs_server",
                "turn_id": "turn_server",
                "artifact_ids": ["art_photo"],
                "bike_profile": {"id": "bike_1"},
                "repair_history": [],
                "diagnostic_artifacts": [],
                "current_observations": [],
                "prior_observations": [],
                "artifact_processing_statuses": [],
            },
        ),
    )

    assert result["ok"] is True
    assert service.calls[0][1]["repair_session_id"] == "rs_server"


async def test_model_provided_identity_override_is_ignored_by_wrappers() -> None:
    service = _CatalogService()
    tool = _tool_by_name(build_tool_catalog(_dependencies(service)), "get_bike_profile")

    result = await tool.run_async(
        args={"repair_session_id": "rs_model_chosen", "user_id": "usr_model"},
        tool_context=_tool_context(),
    )

    assert result["ok"] is True
    assert service.calls[0][1]["repair_session_id"] == "rs_server"
    assert service.calls[0][1]["current_user"].id == "usr_server"
    assert service.calls[0][1]["diagnostic_session_id"] == "phs_server"


async def test_tool_catalog_normalizes_known_domain_failures() -> None:
    service = _CatalogService(error=NotFoundError())
    tool = _tool_by_name(build_tool_catalog(_dependencies(service)), "get_bike_profile")

    result = await tool.run_async(args={}, tool_context=_tool_context())

    assert result == {
        "ok": False,
        "error": {
            "code": "not_found",
            "message": "Repair session was not found.",
        },
    }


async def test_request_diagnostic_input_wrapper_invokes_bound_service_once() -> None:
    service = _CatalogService()
    tool = _tool_by_name(
        build_tool_catalog(_dependencies(service)),
        "request_diagnostic_input",
    )

    result = await tool.run_async(
        args={
            "type": "photo",
            "prompt": "Upload a drivetrain photo.",
            "accepted_media_types": ["image/jpeg"],
        },
        tool_context=_tool_context(),
    )

    assert result["ok"] is True
    assert [call[0] for call in service.calls] == ["request_diagnostic_input"]
    assert service.calls[0][1]["repair_session_id"] == "rs_server"
    assert service.calls[0][1]["diagnostic_session_id"] == "phs_server"


async def test_raise_safety_flag_wrapper_invokes_bound_service_once() -> None:
    service = _CatalogService()
    tool = _tool_by_name(
        build_tool_catalog(_dependencies(service)),
        "raise_safety_flag",
    )

    result = await tool.run_async(
        args={"safety_flag": _safety_flag()},
        tool_context=_tool_context(),
    )

    assert result["ok"] is True
    assert [call[0] for call in service.calls] == ["raise_safety_flag"]
    assert service.calls[0][1]["repair_session_id"] == "rs_server"


async def test_save_diagnostic_report_wrapper_invokes_bound_service_once() -> None:
    service = _CatalogService()
    tool = _tool_by_name(
        build_tool_catalog(_dependencies(service)),
        "save_diagnostic_report",
    )

    result = await tool.run_async(
        args={
            "report": _report_payload(diagnostic_session_id="phs_model_chosen"),
            "completion_basis": _completion_basis(),
            "diagnostic_session_id": "adk_model_chosen",
        },
        tool_context=_tool_context(),
    )

    assert result["ok"] is False
    assert result["error"]["code"] == "report_validation_failed"
    assert service.calls == []

    result = await tool.run_async(
        args={
            "report": _report_payload(),
            "completion_basis": _completion_basis(),
        },
        tool_context=_tool_context(),
    )

    assert result["ok"] is True
    assert [call[0] for call in service.calls] == [
        "persist_diagnostic_report_from_tool",
    ]
    assert "diagnostic_session_id" not in service.calls[0][1]["payload"]
    assert service.calls[0][1]["turn_id"] == "turn_server"
