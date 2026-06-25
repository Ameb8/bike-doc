"""Agents CLI adapter tests."""

from bike_doc_api.adk.agents.diagnostic import DIAGNOSTIC_AGENT_NAME, DIAGNOSTIC_PROMPT
from bike_doc_api_adk_agent import root_agent


def test_agents_cli_entrypoint_exposes_backend_diagnostic_agent() -> None:
    assert root_agent.name == DIAGNOSTIC_AGENT_NAME
    assert root_agent.instruction == DIAGNOSTIC_PROMPT
    assert root_agent.tools == []
