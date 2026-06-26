"""Diagnostic turn orchestration tests."""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from bike_doc_api.adk.orchestration import DiagnosticTurnOrchestrator
from bike_doc_api.adk.runner import (
    DiagnosticRunnerAssistantDelta,
    DiagnosticRunnerAssistantMessageCompleted,
    DiagnosticRunnerInputRequested,
    DiagnosticRunnerRecoverableError,
    DiagnosticRunnerReportCompleted,
    DiagnosticRunnerRequest,
    DiagnosticRunnerSafetyEscalated,
)
from bike_doc_api.models.artifact import ArtifactRef
from bike_doc_api.models.event import RepairSessionEvent
from bike_doc_api.models.repair_session import (
    RepairPhaseSession,
    RepairSession,
    RepairTurn,
)
from bike_doc_api.models.user import User
from bike_doc_api.schemas.event import (
    RepairSessionEventType,
    validate_repair_session_event_data,
)
from bike_doc_api.schemas.repair_session import repair_session_from_model


class _Store:
    """In-memory repositories for orchestration tests."""

    def __init__(self) -> None:
        self.phase_session = RepairPhaseSession(
            id="phs_orch",
            repair_session_id="rs_orch",
            phase="diagnostic",
            adk_session_id="adk_internal_orch",
            status="active",
            created_at=datetime(2026, 1, 1, tzinfo=UTC),
        )
        self.session = RepairSession(
            id="rs_orch",
            user_id="usr_orch",
            bike_id="bike_orch",
            phase="diagnostic",
            status="running",
            safety_state="ok",
            current_input_request=None,
            execution_progress=None,
            active_safety_flags=[],
            latest_event_sequence=1,
            created_at=datetime(2026, 1, 1, tzinfo=UTC),
            updated_at=datetime(2026, 1, 1, tzinfo=UTC),
        )
        self.events: list[RepairSessionEvent] = []
        self.artifact = ArtifactRef(
            id="art_1",
            user_id="usr_orch",
            repair_session_id="rs_orch",
            purpose="diagnostic_photo",
            media_type="image",
            mime_type="image/jpeg",
            filename="drivetrain.jpg",
            byte_size=123,
            status="ready",
            content_sha256="a" * 64,
            storage_provider="local",
            storage_path="objects/art_1.jpg",
            created_at=datetime(2026, 1, 1, tzinfo=UTC),
            updated_at=datetime(2026, 1, 1, tzinfo=UTC),
        )

    async def get(self, phase_session_id: str) -> RepairPhaseSession | None:
        if phase_session_id == self.phase_session.id:
            return self.phase_session
        return None

    async def get_owned_for_update(
        self,
        *,
        repair_session_id: str,
        user_id: str,
    ) -> RepairSession | None:
        if repair_session_id == self.session.id and user_id == self.session.user_id:
            return self.session
        return None

    async def get_owned(
        self,
        *,
        artifact_id: str,
        user_id: str,
    ) -> ArtifactRef | None:
        if artifact_id == self.artifact.id and user_id == self.artifact.user_id:
            return self.artifact
        return None

    async def add(self, event: RepairSessionEvent) -> RepairSessionEvent:
        event.id = event.id or f"evt_internal_{event.sequence}"
        event.created_at = datetime(2026, 1, 1, 0, 0, event.sequence, tzinfo=UTC)
        self.events.append(event)
        return event


class _EventService:
    """Fake public event service."""

    def __init__(self, store: _Store) -> None:
        self.store = store

    async def append_event(
        self,
        *,
        repair_session_id: str,
        event_type: RepairSessionEventType | str,
        data: dict[str, Any],
        turn_id: str | None = None,
    ) -> RepairSessionEvent:
        public_type = RepairSessionEventType(event_type)
        sequence = self.store.session.latest_event_sequence + 1
        self.store.session.latest_event_sequence = sequence
        event = RepairSessionEvent(
            id=f"evt_internal_{sequence}",
            repair_session_id=repair_session_id,
            turn_id=turn_id,
            sequence=sequence,
            type=public_type.value,
            data=validate_repair_session_event_data(public_type, data),
            created_at=datetime(2026, 1, 1, 0, 0, sequence, tzinfo=UTC),
        )
        self.store.events.append(event)
        return event


class _Runner:
    """Fake runner streaming configured app-owned events."""

    def __init__(
        self,
        events: list[Any] | None = None,
        *,
        raises: bool = False,
        before_emit: dict[int, Any] | None = None,
    ) -> None:
        self.events = events or []
        self.raises = raises
        self.requests: list[DiagnosticRunnerRequest] = []
        self.run_called = 0
        self.stream_called = 0
        self.before_emit = before_emit or {}

    async def run(self, request: DiagnosticRunnerRequest) -> object:
        self.run_called += 1
        msg = "production orchestration must consume runner.stream(...)"
        raise AssertionError(msg)

    def stream(
        self,
        request: DiagnosticRunnerRequest,
    ) -> AsyncIterator[Any]:
        async def _events() -> AsyncIterator[Any]:
            self.stream_called += 1
            self.requests.append(request)
            if self.raises:
                raise RuntimeError("raw provider credentials")
            for index, event in enumerate(self.events):
                before_emit = self.before_emit.get(index)
                if before_emit is not None:
                    before_emit()
                if isinstance(event, BaseException):
                    raise event
                yield event

        return _events()


@dataclass
class _Tool:
    """Fake ADK tool wrapper."""

    result: dict[str, Any]
    calls: list[dict[str, Any]]

    async def run(
        self,
        tool_input: Mapping[str, Any],
        context: object,
    ) -> dict[str, Any]:
        self.calls.append({"input": dict(tool_input), "context": context})
        return self.result


def _user() -> User:
    return User(
        id="usr_orch",
        auth_subject="auth|orch",
        email="orch@example.com",
        display_name="Orch User",
        skill_level="beginner",
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
    )


def _turn(**message: Any) -> RepairTurn:
    return RepairTurn(
        id="turn_orch",
        repair_session_id="rs_orch",
        repair_phase_session_id="phs_orch",
        client_turn_id="client_turn",
        request_hash="hash",
        schema_version="ai_turn.v1",
        phase="diagnostic",
        message={"text": "The chain skips.", "artifact_ids": [], **message},
        start_event_sequence=1,
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
    )


def _orchestrator(
    *,
    store: _Store,
    runner: _Runner,
    input_tool: _Tool | None = None,
    safety_tool: _Tool | None = None,
    report_tool: _Tool | None = None,
) -> DiagnosticTurnOrchestrator:
    calls: list[dict[str, Any]] = []
    return DiagnosticTurnOrchestrator(
        phase_sessions=store,
        repair_sessions=store,
        events=store,
        artifacts=store,
        event_service=_EventService(store),
        runner=runner,
        get_bike_profile=_Tool(
            {"ok": True, "data": {"bike_profile": {"id": "bike_orch"}}},
            calls,
        ),
        lookup_repair_history=_Tool({"ok": True, "data": {"entries": []}}, calls),
        list_diagnostic_artifacts=_Tool(
            {"ok": True, "data": {"artifacts": [{"id": "art_1"}]}},
            calls,
        ),
        request_diagnostic_input=input_tool or _Tool({"ok": True, "data": {}}, calls),
        raise_safety_flag=safety_tool or _Tool({"ok": True, "data": {}}, calls),
        save_diagnostic_report=report_tool or _Tool({"ok": True, "data": {}}, calls),
    )


async def test_accepted_turn_invokes_runner_with_server_owned_context() -> None:
    store = _Store()
    runner = _Runner()

    await _orchestrator(store=store, runner=runner).process_turn(
        current_user=_user(),
        turn=_turn(artifact_ids=["art_1"]),
    )

    request = runner.requests[0]
    assert runner.stream_called == 1
    assert runner.run_called == 0
    assert request.user_id == "usr_orch"
    assert request.user_skill_level == "beginner"
    assert request.repair_session_id == "rs_orch"
    assert request.turn_id == "turn_orch"
    assert request.diagnostic_session_id == "phs_orch"
    assert request.adk_session_id == "adk_internal_orch"
    assert request.message_text == "The chain skips."
    assert request.artifact_ids == ("art_1",)
    assert request.bike_profile == {"id": "bike_orch"}
    assert request.repair_history == ()
    assert request.diagnostic_artifacts == ({"id": "art_1"},)
    assert [event.type for event in store.events] == [
        "artifact.referenced",
        "turn.completed",
    ]
    assert store.events[-1].data["session"]["status"] == "awaiting_user"


async def test_assistant_output_becomes_public_events() -> None:
    store = _Store()
    runner = _Runner(
        [
            DiagnosticRunnerAssistantDelta("Check cable tension."),
            DiagnosticRunnerAssistantMessageCompleted(
                message_id="msg_1",
                full_text="Check cable tension.",
                artifact_ids=(),
            ),
        ],
    )

    await _orchestrator(store=store, runner=runner).process_turn(
        current_user=_user(),
        turn=_turn(),
    )

    assert [event.type for event in store.events] == [
        "assistant.delta",
        "assistant.message.completed",
        "turn.completed",
    ]
    assert store.events[0].data == {"text": "Check cable tension."}
    assert store.events[-1].data["session"]["status"] == "awaiting_user"


async def test_assistant_delta_is_appended_while_runner_iteration_is_active() -> None:
    store = _Store()

    def assert_delta_already_persisted() -> None:
        assert [event.type for event in store.events] == ["assistant.delta"]

    runner = _Runner(
        [
            DiagnosticRunnerAssistantDelta("Check cable tension."),
            DiagnosticRunnerAssistantMessageCompleted(
                message_id="msg_1",
                full_text="Check cable tension.",
                artifact_ids=(),
            ),
        ],
        before_emit={1: assert_delta_already_persisted},
    )

    await _orchestrator(store=store, runner=runner).process_turn(
        current_user=_user(),
        turn=_turn(),
    )

    assert [event.type for event in store.events] == [
        "assistant.delta",
        "assistant.message.completed",
        "turn.completed",
    ]


async def test_input_request_notification_does_not_rerun_tool() -> None:
    store = _Store()
    input_tool = _Tool(
        {"ok": True, "data": {}},
        [],
    )
    runner = _Runner(
        [
            DiagnosticRunnerInputRequested(
                request_type="photo",
                prompt="Upload a drivetrain photo.",
                accepted_media_types=("image/jpeg",),
                min_artifacts=1,
                max_artifacts=3,
            ),
        ],
    )

    await _orchestrator(
        store=store,
        runner=runner,
        input_tool=input_tool,
    ).process_turn(current_user=_user(), turn=_turn())

    assert input_tool.calls == []
    assert store.events[-1].type == "turn.completed"
    assert store.events[-1].data["session"]["status"] == "awaiting_user"


async def test_report_completion_notification_does_not_rerun_save_report_tool() -> None:
    store = _Store()
    report_tool = _Tool({"ok": True, "data": {"report_id": "rpt_1"}}, [])
    runner = _Runner(
        [
            DiagnosticRunnerReportCompleted(
                summary="Likely indexing issue.",
                report_id="rpt_1",
                schema_version="diagnostic_report.v1",
                safety_state="ok",
            ),
        ],
    )

    await _orchestrator(
        store=store,
        runner=runner,
        report_tool=report_tool,
    ).process_turn(current_user=_user(), turn=_turn())

    assert report_tool.calls == []
    assert store.events[-1].type == "turn.completed"
    assert store.events[-1].data["session"]["status"] == "awaiting_decision"
    assert store.events[-1].data["session"] == repair_session_from_model(
        store.session,
    ).model_dump(mode="json")


async def test_safety_escalation_notification_does_not_rerun_safety_tool() -> None:
    store = _Store()
    safety_tool = _Tool({"ok": True, "data": {}}, [])
    runner = _Runner(
        [
            DiagnosticRunnerSafetyEscalated(
                safety_state="blocked",
                safety_flags=(
                    {
                        "code": "brake_failure_suspected",
                        "severity": "blocking",
                        "phase": "diagnostic",
                        "message": "Do not ride until brakes are inspected.",
                        "blocks_repair_instructions": True,
                    },
                ),
            ),
        ],
    )

    await _orchestrator(
        store=store,
        runner=runner,
        safety_tool=safety_tool,
    ).process_turn(current_user=_user(), turn=_turn())

    assert safety_tool.calls == []
    assert store.events[-1].type == "turn.completed"
    assert store.events[-1].data["session"]["status"] == "blocked_safety"


async def test_recoverable_processing_failure_persists_public_error() -> None:
    store = _Store()
    runner = _Runner(
        [
            DiagnosticRunnerAssistantDelta("Check cable tension."),
            DiagnosticRunnerRecoverableError(
                code="provider_timeout",
                message="Diagnostic processing timed out.",
                retryable=True,
            ),
        ],
    )

    await _orchestrator(store=store, runner=runner).process_turn(
        current_user=_user(),
        turn=_turn(),
    )

    assert [event.type for event in store.events] == [
        "assistant.delta",
        "error",
        "turn.completed",
    ]
    assert store.events[1].data == {
        "code": "provider_timeout",
        "message": "Diagnostic processing timed out.",
        "retryable": True,
    }
    assert store.events[-1].data["session"]["status"] == "awaiting_user"


async def test_non_retryable_runner_error_marks_session_failed() -> None:
    store = _Store()
    runner = _Runner(
        [
            DiagnosticRunnerRecoverableError(
                code="runner_output_invalid",
                message="Diagnostic runner produced an invalid tool response.",
                retryable=False,
            ),
        ],
    )

    await _orchestrator(store=store, runner=runner).process_turn(
        current_user=_user(),
        turn=_turn(),
    )

    assert [event.type for event in store.events] == ["error", "turn.completed"]
    assert store.events[-1].data["session"]["status"] == "failed"


async def test_runner_exception_persists_public_error() -> None:
    store = _Store()

    await _orchestrator(store=store, runner=_Runner(raises=True)).process_turn(
        current_user=_user(),
        turn=_turn(),
    )

    assert [event.type for event in store.events] == ["error", "turn.completed"]
    assert store.events[0].data["code"] == "diagnostic_processing_error"
    assert store.events[0].data["message"] == (
        "Diagnostic processing could not be completed."
    )
    assert "credential" not in repr(store.events)
    assert store.events[-1].data["session"]["status"] == "awaiting_user"


async def test_stream_exception_after_prior_event_preserves_order() -> None:
    store = _Store()
    runner = _Runner(
        [
            DiagnosticRunnerAssistantDelta("Check cable tension."),
            RuntimeError("raw provider metadata"),
        ],
    )

    await _orchestrator(store=store, runner=runner).process_turn(
        current_user=_user(),
        turn=_turn(),
    )

    assert [event.type for event in store.events] == [
        "assistant.delta",
        "error",
        "turn.completed",
    ]
    assert store.events[1].data == {
        "code": "diagnostic_processing_error",
        "message": "Diagnostic processing could not be completed.",
        "retryable": True,
    }
    assert "raw provider" not in repr(store.events)
    assert "adk_internal_orch" not in repr(store.events)
    assert store.events[-1].data["session"]["status"] != "running"
