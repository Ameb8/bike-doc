"""Compatibility checks for the pinned Google ADK integration surface."""

from __future__ import annotations

import inspect
from importlib.metadata import version

from google.adk.agents import Agent
from google.adk.events import Event
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.adk.tools import FunctionTool
from google.genai import types


def _compat_tool(query: str) -> dict[str, str]:
    return {"query": query}


def _parameter_names(callable_object: object) -> set[str]:
    return set(inspect.signature(callable_object).parameters)


def test_pinned_adk_and_genai_versions_are_installed() -> None:
    assert version("google-adk") == "2.3.0"
    assert version("google-genai") == "2.10.0"


def test_adk_import_paths_and_constructor_signatures_are_compatible() -> None:
    assert {"name", "model", "instruction", "tools"}.issubset(
        _parameter_names(Agent),
    )
    assert {"app_name", "agent", "session_service"}.issubset(
        _parameter_names(Runner),
    )
    assert {"user_id", "session_id", "new_message", "state_delta"}.issubset(
        _parameter_names(Runner.run_async),
    )
    assert "func" in _parameter_names(FunctionTool)

    tool = FunctionTool(_compat_tool)
    agent = Agent(
        name="compat_agent",
        model="gemini-2.5-flash",
        instruction="Return concise diagnostic output.",
        tools=[tool],
    )
    runner = Runner(
        app_name="bike_doc_compat",
        agent=agent,
        session_service=InMemorySessionService(),
    )

    assert agent.tools == [tool]
    assert runner.run_async is not None


async def test_in_memory_session_service_create_get_and_delete_api() -> None:
    service = InMemorySessionService()

    session = await service.create_session(
        app_name="bike_doc_compat",
        user_id="usr_compat",
        state={"diagnostic_session_id": "phs_compat"},
        session_id="adk_compat",
    )
    stored = await service.get_session(
        app_name="bike_doc_compat",
        user_id="usr_compat",
        session_id=session.id,
    )

    assert session.id == "adk_compat"
    assert stored is not None
    assert stored.state["diagnostic_session_id"] == "phs_compat"

    await service.delete_session(
        app_name="bike_doc_compat",
        user_id="usr_compat",
        session_id=session.id,
    )
    assert (
        await service.get_session(
            app_name="bike_doc_compat",
            user_id="usr_compat",
            session_id=session.id,
        )
        is None
    )


def test_adk_event_fields_support_future_runner_normalization() -> None:
    delta_event = Event(
        author="diagnostic_agent",
        content=types.Content(
            role="model",
            parts=[types.Part.from_text(text="Check ")],
        ),
        partial=True,
    )
    final_event = Event(
        author="diagnostic_agent",
        content=types.Content(
            role="model",
            parts=[types.Part.from_text(text="cable tension.")],
        ),
        usageMetadata=types.GenerateContentResponseUsageMetadata(
            prompt_token_count=10,
            candidates_token_count=4,
            total_token_count=14,
        ),
    )
    function_call_event = Event(
        author="diagnostic_agent",
        content=types.Content(
            role="model",
            parts=[
                types.Part.from_function_call(
                    name="request_diagnostic_input",
                    args={"request_type": "photo"},
                ),
            ],
        ),
    )
    tool_response_event = Event(
        author="request_diagnostic_input",
        content=types.Content(
            role="tool",
            parts=[
                types.Part.from_function_response(
                    name="request_diagnostic_input",
                    response={"ok": True},
                ),
            ],
        ),
    )

    assert delta_event.partial is True
    assert delta_event.content is not None
    assert delta_event.content.parts is not None
    assert delta_event.content.parts[0].text == "Check "

    assert final_event.is_final_response() is True
    assert final_event.usage_metadata is not None
    assert final_event.usage_metadata.total_token_count == 14

    function_calls = function_call_event.get_function_calls()
    assert len(function_calls) == 1
    assert function_calls[0].name == "request_diagnostic_input"
    assert function_calls[0].args == {"request_type": "photo"}

    function_responses = tool_response_event.get_function_responses()
    assert len(function_responses) == 1
    assert function_responses[0].name == "request_diagnostic_input"
    assert function_responses[0].response == {"ok": True}
