"""Diagnostic runner boundary tests."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any, cast

from google.adk.events import Event
from google.adk.sessions import InMemorySessionService
from google.genai import types

from bike_doc_api.adk.runner import (
    DiagnosticRunner,
    DiagnosticRunnerAssistantDelta,
    DiagnosticRunnerAssistantMessageCompleted,
    DiagnosticRunnerInputRequested,
    DiagnosticRunnerRecoverableError,
    DiagnosticRunnerReportCompleted,
    DiagnosticRunnerRequest,
    DiagnosticRunnerResult,
)
from bike_doc_api.adk.sessions import DIAGNOSTIC_ADK_APP_NAME, DIAGNOSTIC_ADK_USER_ID


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


async def _seed_session(service: InMemorySessionService) -> None:
    await service.create_session(
        app_name=DIAGNOSTIC_ADK_APP_NAME,
        user_id=DIAGNOSTIC_ADK_USER_ID,
        session_id=_request().adk_session_id,
    )


async def _collect(runner: DiagnosticRunner) -> list[Any]:
    return [event async for event in runner.stream(_request())]


def _partial(text: str) -> Event:
    return Event(
        author="diagnostic_agent",
        content=types.Content(
            role="model",
            parts=[types.Part.from_text(text=text)],
        ),
        partial=True,
    )


def _final(text: str = "") -> Event:
    parts = [types.Part.from_text(text=text)] if text else []
    return Event(
        author="diagnostic_agent",
        content=types.Content(role="model", parts=parts),
    )


def _function_response(name: str, response: dict[str, Any]) -> Event:
    return Event(
        author=name,
        content=types.Content(
            role="tool",
            parts=[
                types.Part.from_function_response(
                    name=name,
                    response=response,
                ),
            ],
        ),
    )


class _FakeADKRunner:
    def __init__(self, events: list[Any]) -> None:
        self.events = events
        self.calls: list[dict[str, Any]] = []

    def run_async(self, **kwargs: Any) -> AsyncIterator[Any]:
        self.calls.append(kwargs)

        async def _events() -> AsyncIterator[Any]:
            for event in self.events:
                if isinstance(event, BaseException):
                    raise event
                yield event

        return _events()


class _Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.now += seconds


async def _sleep_never(_: float) -> None:
    await asyncio.Event().wait()


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


async def test_stream_returns_async_iterator_of_app_owned_events() -> None:
    service = InMemorySessionService()
    await _seed_session(service)
    fake_adk = _FakeADKRunner([_partial("Check cable tension is low."), _final()])

    runner = DiagnosticRunner(
        agent=cast(Any, object()),
        session_service=service,
        runner_factory=lambda _agent, _service: fake_adk,
        sleep=_sleep_never,
    )

    stream = runner.stream(_request())

    assert hasattr(stream, "__anext__")
    events = [event async for event in stream]
    assert all(
        isinstance(
            event,
            (
                DiagnosticRunnerAssistantDelta,
                DiagnosticRunnerAssistantMessageCompleted,
                DiagnosticRunnerInputRequested,
                DiagnosticRunnerReportCompleted,
                DiagnosticRunnerRecoverableError,
            ),
        )
        for event in events
    )


async def test_adk_stream_yields_delta_before_later_final_response() -> None:
    service = InMemorySessionService()
    await _seed_session(service)
    fake_adk = _FakeADKRunner(
        [_partial("Check cable tension now."), _final("Check cable tension now.")],
    )

    events = await _collect(
        DiagnosticRunner(
            agent=cast(Any, object()),
            session_service=service,
            runner_factory=lambda _agent, _service: fake_adk,
            sleep=_sleep_never,
        ),
    )

    assert isinstance(events[0], DiagnosticRunnerAssistantDelta)
    assert events[0].text == "Check cable tension now."
    assert isinstance(events[1], DiagnosticRunnerAssistantMessageCompleted)


async def test_run_collects_stream_output_for_compatibility() -> None:
    class _StreamingRunner(DiagnosticRunner):
        def stream(
            self,
            request: DiagnosticRunnerRequest,
        ) -> AsyncIterator[Any]:
            async def _events() -> AsyncIterator[Any]:
                assert request.turn_id == "turn_runner"
                yield DiagnosticRunnerAssistantDelta("collected")

            return _events()

    result = await _StreamingRunner().run(_request())

    assert result == DiagnosticRunnerResult(
        events=(DiagnosticRunnerAssistantDelta("collected"),),
        completed=True,
    )


async def test_character_threshold_coalescing_flushes_at_25_characters() -> None:
    service = InMemorySessionService()
    await _seed_session(service)
    fake_adk = _FakeADKRunner([_partial("123456789012"), _partial("3456789012345")])

    events = await _collect(
        DiagnosticRunner(
            agent=cast(Any, object()),
            session_service=service,
            runner_factory=lambda _agent, _service: fake_adk,
            sleep=_sleep_never,
        ),
    )

    assert events == [DiagnosticRunnerAssistantDelta("1234567890123456789012345")]


async def test_elapsed_time_coalescing_flushes_pending_text() -> None:
    service = InMemorySessionService()
    await _seed_session(service)
    clock = _Clock()
    fake_adk = _FakeADKRunner([_partial("short")])

    events = await _collect(
        DiagnosticRunner(
            agent=cast(Any, object()),
            session_service=service,
            runner_factory=lambda _agent, _service: fake_adk,
            clock=clock,
            sleep=clock.sleep,
        ),
    )

    assert events == [DiagnosticRunnerAssistantDelta("short")]
    assert clock.now == 0.150


async def test_remaining_text_flushes_before_completed_message() -> None:
    service = InMemorySessionService()
    await _seed_session(service)
    fake_adk = _FakeADKRunner([_partial("Check cable"), _final("Check cable tension.")])

    events = await _collect(
        DiagnosticRunner(
            agent=cast(Any, object()),
            session_service=service,
            runner_factory=lambda _agent, _service: fake_adk,
            sleep=_sleep_never,
        ),
    )

    assert isinstance(events[0], DiagnosticRunnerAssistantDelta)
    assert events[0].text == "Check cable"
    assert isinstance(events[1], DiagnosticRunnerAssistantMessageCompleted)
    assert events[1].full_text == "Check cable tension."


async def test_final_response_has_app_owned_message_id_and_public_safe_text() -> None:
    service = InMemorySessionService()
    await _seed_session(service)
    fake_adk = _FakeADKRunner([_final("Use a barrel adjuster.")])

    events = await _collect(
        DiagnosticRunner(
            agent=cast(Any, object()),
            session_service=service,
            runner_factory=lambda _agent, _service: fake_adk,
        ),
    )

    completed = events[0]
    assert isinstance(completed, DiagnosticRunnerAssistantMessageCompleted)
    assert completed.message_id.startswith("msg_")
    assert completed.full_text == "Use a barrel adjuster."
    assert completed.artifact_ids == ("art_1",)
    assert "adk_internal_runner" not in repr(completed)


async def test_runner_seeds_adk_call_with_safe_app_context() -> None:
    service = InMemorySessionService()
    await _seed_session(service)
    fake_adk = _FakeADKRunner([_final("Done.")])

    await _collect(
        DiagnosticRunner(
            agent=cast(Any, object()),
            session_service=service,
            runner_factory=lambda _agent, _service: fake_adk,
        ),
    )

    call = fake_adk.calls[0]
    assert call["user_id"] == DIAGNOSTIC_ADK_USER_ID
    assert call["session_id"] == "adk_internal_runner"
    assert call["new_message"].role == "user"
    assert call["new_message"].parts[0].text == "The chain skips."
    app_context = call["state_delta"]["app_context"]
    assert app_context["diagnostic_session_id"] == "phs_runner"
    assert app_context["artifact_ids"] == ["art_1"]
    assert "adk_session_id" not in app_context
    assert "prompt" not in repr(call["state_delta"]).lower()


async def test_runner_detects_missing_in_memory_adk_session_as_recoverable() -> None:
    fake_adk = _FakeADKRunner([_final("should not run")])

    events = await _collect(
        DiagnosticRunner(
            agent=cast(Any, object()),
            session_service=InMemorySessionService(),
            runner_factory=lambda _agent, _service: fake_adk,
        ),
    )

    assert len(events) == 1
    error = events[0]
    assert isinstance(error, DiagnosticRunnerRecoverableError)
    assert error.code == "diagnostic_session_unavailable"
    assert error.retryable is True
    assert "adk" not in error.message.lower()
    assert fake_adk.calls == []


async def test_runner_level_exception_before_output_yields_recoverable_error() -> None:
    service = InMemorySessionService()
    await _seed_session(service)
    fake_adk = _FakeADKRunner([RuntimeError("raw provider credential failure")])

    events = await _collect(
        DiagnosticRunner(
            agent=cast(Any, object()),
            session_service=service,
            runner_factory=lambda _agent, _service: fake_adk,
        ),
    )

    assert events == [
        DiagnosticRunnerRecoverableError(
            code="diagnostic_processing_error",
            message="Diagnostic processing encountered a recoverable error.",
            retryable=True,
        ),
    ]


async def test_runner_level_exception_after_delta_preserves_prior_events() -> None:
    service = InMemorySessionService()
    await _seed_session(service)
    fake_adk = _FakeADKRunner(
        [
            _partial("Check cable tension now please."),
            RuntimeError("raw provider metadata"),
        ],
    )

    events = await _collect(
        DiagnosticRunner(
            agent=cast(Any, object()),
            session_service=service,
            runner_factory=lambda _agent, _service: fake_adk,
            sleep=_sleep_never,
        ),
    )

    assert isinstance(events[0], DiagnosticRunnerAssistantDelta)
    assert events[0].text == "Check cable tension now please."
    assert isinstance(events[1], DiagnosticRunnerRecoverableError)
    assert events[1].code == "diagnostic_processing_error"


async def test_raw_adk_metadata_is_not_yielded() -> None:
    service = InMemorySessionService()
    await _seed_session(service)
    fake_adk = _FakeADKRunner(
        [
            Event(
                author="diagnostic_agent",
                content=types.Content(
                    role="model",
                    parts=[types.Part.from_text(text="Done.")],
                ),
            ),
        ],
    )

    events = await _collect(
        DiagnosticRunner(
            agent=cast(Any, object()),
            session_service=service,
            runner_factory=lambda _agent, _service: fake_adk,
        ),
    )

    rendered = repr(events)
    assert "adk_internal_runner" not in rendered
    assert "usage" not in rendered.lower()
    assert "gemini" not in rendered.lower()
    assert "credential" not in rendered.lower()
    assert "tool_call" not in rendered.lower()


async def test_state_mutating_tool_response_maps_to_notification_event() -> None:
    service = InMemorySessionService()
    await _seed_session(service)
    fake_adk = _FakeADKRunner(
        [
            _function_response(
                "request_diagnostic_input",
                {
                    "ok": True,
                    "data": {
                        "input_request": {
                            "id": "req_1",
                            "type": "photo",
                            "prompt": "Upload a drivetrain photo.",
                            "required": True,
                            "accepted_media_types": ["image/jpeg"],
                            "choices": [],
                            "min_artifacts": 1,
                            "max_artifacts": 3,
                        },
                        "event_sequence": 12,
                    },
                },
            ),
        ],
    )

    events = await _collect(
        DiagnosticRunner(
            agent=cast(Any, object()),
            session_service=service,
            runner_factory=lambda _agent, _service: fake_adk,
        ),
    )

    assert events == [
        DiagnosticRunnerInputRequested(
            request_type="photo",
            prompt="Upload a drivetrain photo.",
            required=True,
            accepted_media_types=("image/jpeg",),
            min_artifacts=1,
            max_artifacts=3,
        ),
    ]


async def test_malformed_tool_response_maps_to_public_safe_error() -> None:
    service = InMemorySessionService()
    await _seed_session(service)
    fake_adk = _FakeADKRunner(
        [
            _function_response(
                "request_diagnostic_input",
                {"ok": True, "data": {"input_request": {"type": "photo"}}},
            ),
        ],
    )

    events = await _collect(
        DiagnosticRunner(
            agent=cast(Any, object()),
            session_service=service,
            runner_factory=lambda _agent, _service: fake_adk,
        ),
    )

    assert events == [
        DiagnosticRunnerRecoverableError(
            code="runner_output_invalid",
            message="Diagnostic runner produced an invalid tool response.",
            retryable=False,
        ),
    ]
