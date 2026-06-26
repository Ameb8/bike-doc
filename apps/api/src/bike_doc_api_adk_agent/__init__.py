"""Agents CLI entrypoint for the backend-owned Bike Doc diagnostic agent.

This package exists only so Agents CLI can discover an ADK ``root_agent``.
The product runtime remains ``bike_doc_api.main:app`` and exposes the custom
turn-based FastAPI API described in ``docs/specs/apps/adk-wiring-spec.md``.
"""

from google.adk.agents import Agent

from bike_doc_api.adk.agents.diagnostic import DIAGNOSTIC_AGENT_NAME, DIAGNOSTIC_PROMPT
from bike_doc_api.core.config import get_settings

_settings = get_settings()

root_agent = Agent(
    name=DIAGNOSTIC_AGENT_NAME,
    model=_settings.diagnostic_agent_model,
    instruction=DIAGNOSTIC_PROMPT,
    tools=[],
)
