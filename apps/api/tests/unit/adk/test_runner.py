"""Diagnostic runner boundary tests."""

from __future__ import annotations

from typing import Any

from google.adk.sessions import InMemorySessionService

from bike_doc_api.adk.runner import (
    DiagnosticRunner,
    DiagnosticRunnerAssistantDelta,
    DiagnosticRunnerAssistantMessageCompleted,
    DiagnosticRunnerInputRequested,
    DiagnosticRunnerRecoverableError,
    DiagnosticRunnerReportCompleted,
    DiagnosticRunnerRequest,
)


def _request() -> DiagnosticRunnerRequest:
    return DiagnosticRunnerRequest(
        user_id="usr_runner",
        user_skill_level="beginner",
        repair_session_id="rs_runner",
        turn_id="turn_runner",
        diagnostic_session_id="phs_runner",
        adk_session_id="adk_internal_runner",
        message_text="The chain skips.",
        artifact_ids=("art_1",),
        bike_profile={"id": "bike_1"},
    )


async def test_maps_fake_agent_output_to_app_owned_events() -> None:
    async def invoke(request: DiagnosticRunnerRequest) -> list[dict[str, Any]]:
        assert request.diagnostic_session_id == "phs_runner"
        return [
            {"type": "assistant_delta", "text": "Check "},
            {"type": "assistant_delta", "text": "cable tension."},
            {
                "type": "assistant_message_completed",
                "message_id": "msg_public",
                "artifact_ids": ["art_1"],
            },
            {
                "type": "input_requested",
                "request_type": "photo",
                "prompt": "Upload a drivetrain photo.",
                "accepted_media_types": ["image/jpeg"],
                "min_artifacts": 1,
            },
            {
                "type": "report_completed",
                "summary": "Likely indexing issue.",
                "report": {
                    "schema_version": "diagnostic_report.v1",
                    "primary_diagnosis": {
                        "component": "rear derailleur",
                        "issue": "Cable tension is likely low.",
                        "confidence": "medium",
                    },
                    "alternate_hypotheses": [],
                    "evidence_summary": "User reports skipping.",
                    "key_artifact_ids": [],
                    "user_skill_level": "beginner",
                    "safety_flags": [],
                },
            },
        ]

    result = await DiagnosticRunner(invoke).run(_request())

    assert result.completed is True
    assert isinstance(result.events[0], DiagnosticRunnerAssistantDelta)
    assert isinstance(result.events[2], DiagnosticRunnerAssistantMessageCompleted)
    completed = result.events[2]
    assert isinstance(completed, DiagnosticRunnerAssistantMessageCompleted)
    assert completed.full_text == "Check cable tension."
    assert completed.artifact_ids == ("art_1",)
    assert isinstance(result.events[3], DiagnosticRunnerInputRequested)
    assert isinstance(result.events[4], DiagnosticRunnerReportCompleted)


async def test_runner_does_not_expose_raw_adk_structures() -> None:
    class _RawADKEvent:
        adk_session_id = "adk_raw"

    async def invoke(_: DiagnosticRunnerRequest) -> list[Any]:
        return [
            _RawADKEvent(),
            {
                "type": "recoverable_error",
                "code": "provider_timeout",
                "message": "Diagnostic processing timed out.",
                "retryable": True,
            },
        ]

    result = await DiagnosticRunner(invoke).run(_request())

    assert len(result.events) == 1
    error = result.events[0]
    assert isinstance(error, DiagnosticRunnerRecoverableError)
    assert error.code == "provider_timeout"
    assert "adk" not in repr(error).lower()


async def test_runner_detects_missing_in_memory_adk_session_as_recoverable() -> None:
    async def invoke(_: DiagnosticRunnerRequest) -> list[Any]:
        raise AssertionError("stale ADK sessions must not invoke the agent")

    result = await DiagnosticRunner(
        invoke,
        session_service=InMemorySessionService(),
    ).run(_request())

    assert len(result.events) == 1
    error = result.events[0]
    assert isinstance(error, DiagnosticRunnerRecoverableError)
    assert error.code == "diagnostic_session_unavailable"
    assert error.retryable is True
    assert "adk" not in error.message.lower()
