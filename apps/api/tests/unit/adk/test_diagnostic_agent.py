"""Diagnostic agent structural tests."""

from __future__ import annotations

from typing import Any

from bike_doc_api.adk.agents.diagnostic import (
    DIAGNOSTIC_COMPLETION_TOOL_NAME,
    DIAGNOSTIC_PROMPT,
    V1_DIAGNOSTIC_TOOL_NAMES,
    DiagnosticAgentToolDependencies,
    create_diagnostic_agent,
    load_diagnostic_prompt,
)
from bike_doc_api.adk.report_schemas.diagnostic import DiagnosticReportToolPayload
from bike_doc_api.core.config import Settings


class _FakeService:
    """Fake service dependency for structural agent construction tests."""

    def __getattr__(self, name: str) -> Any:
        async def _call(**_kwargs: Any) -> Any:
            msg = f"{name} should not be called by structural tests"
            raise AssertionError(msg)

        return _call


def _dependencies() -> DiagnosticAgentToolDependencies:
    service = _FakeService()
    return DiagnosticAgentToolDependencies(
        bike_profile_service=service,
        repair_history_service=service,
        artifact_service=service,
        input_request_service=service,
        safety_service=service,
        report_service=service,
    )


def test_diagnostic_agent_constructs_with_fake_tool_dependencies() -> None:
    settings = Settings(environment="test", diagnostic_agent_model="test-model")

    agent = create_diagnostic_agent(_dependencies(), settings=settings)

    assert agent.name == "diagnostic_agent"
    assert agent.model == "test-model"
    assert agent.instruction == DIAGNOSTIC_PROMPT
    assert agent.output_schema is DiagnosticReportToolPayload
    assert agent.completion_condition.tool_name == DIAGNOSTIC_COMPLETION_TOOL_NAME
    assert agent.completion_condition.agent_side_only_for_stage_14 is True


def test_diagnostic_agent_registers_all_and_only_v1_tools() -> None:
    agent = create_diagnostic_agent(
        _dependencies(),
        settings=Settings(environment="test"),
    )

    assert agent.tool_names == V1_DIAGNOSTIC_TOOL_NAMES
    assert "lookup_tool_catalog" not in agent.tool_names
    assert "price_lookup" not in agent.tool_names
    assert "lookup_repair_reference" not in agent.tool_names
    assert "lookup_diagnostic_reference" not in agent.tool_names


def test_diagnostic_agent_adk_kwargs_use_registered_tools() -> None:
    agent = create_diagnostic_agent(
        _dependencies(),
        settings=Settings(environment="test", diagnostic_agent_model="gemini-test"),
    )

    kwargs = agent.as_adk_agent_kwargs()

    assert kwargs["name"] == agent.name
    assert kwargs["model"] == "gemini-test"
    assert kwargs["instruction"] == agent.instruction
    assert kwargs["tools"] == [tool.tool for tool in agent.tools]


def test_diagnostic_prompt_file_is_loaded_by_agent_module() -> None:
    prompt = load_diagnostic_prompt()

    assert prompt == DIAGNOSTIC_PROMPT
    assert "Bike Doc Diagnostic Agent" in prompt
    assert "save_diagnostic_report" in prompt


def test_diagnostic_prompt_contains_required_stage_14_instructions() -> None:
    prompt = " ".join(DIAGNOSTIC_PROMPT.split())

    required_fragments = [
        "Ask for missing diagnostic evidence before concluding",
        "Treat photos as first-class diagnostic evidence",
        "request_diagnostic_input",
        'type: "photo"',
        "Track alternate hypotheses explicitly",
        "Do not invent torque specs",
        "manufacturer-specific claims",
        "service manual",
        "parts compatibility",
        "step-by-step repair instructions",
        "frame_or_fork_damage_suspected",
        "brake_failure_suspected",
        "carbon_damage_suspected",
        "ebike_electrical_concern",
        "suspension_internal_concern",
        "safety_critical_fastener_damaged",
        "uncertain_torque_spec",
        "contradictory_evidence",
        "insufficient_evidence_for_safe_guidance",
        "unsafe_riding_condition",
        "Set `blocks_repair_instructions: true` for every `blocking` flag",
        "Prefer shop referral",
        "diagnostic_report.v1",
        "Do not include `diagnostic_session_id` in the tool input",
        "complete the phase only by calling `save_diagnostic_report`",
    ]
    for fragment in required_fragments:
        assert fragment in prompt
