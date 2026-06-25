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
    DiagnosticRunnerResult,
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
from bike_doc_api.schemas.repair_session import InputRequest, repair_session_from_model


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
    """Fake runner returning configured app-owned events."""

    def __init__(
        self, events: list[Any] | None = None, *, raises: bool = False
    ) -> None:
        self.events = events or []
        self.raises = raises
        self.requests: list[DiagnosticRunnerRequest] = []

    async def run(self, request: DiagnosticRunnerRequest) -> DiagnosticRunnerResult:
        self.requests.append(request)
        if self.raises:
            raise RuntimeError("boom")
        return DiagnosticRunnerResult(events=tuple(self.events), completed=True)

    def stream(
        self,
        request: DiagnosticRunnerRequest,
    ) -> AsyncIterator[Any]:
        async def _events() -> AsyncIterator[Any]:
            result = await self.run(request)
            for event in result.events:
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
        raise_safety_flag=_Tool({"ok": True, "data": {}}, calls),
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
    assert request.user_id == "usr_orch"
    assert request.user_skill_level == "beginner"
    assert request.repair_session_id == "rs_orch"
    assert request.diagnostic_session_id == "phs_orch"
    assert request.adk_session_id == "adk_internal_orch"
    assert request.bike_profile == {"id": "bike_orch"}
    assert request.diagnostic_artifacts == ({"id": "art_1"},)
    assert [event.type for event in store.events] == [
        "artifact.referenced",
        "turn.completed",
    ]


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


async def test_input_request_output_uses_tool_path() -> None:
    store = _Store()
    input_request = InputRequest(
        id="req_1",
        type="photo",
        prompt="Upload a drivetrain photo.",
        required=True,
        accepted_media_types=["image/jpeg"],
        choices=[],
        min_artifacts=1,
        max_artifacts=3,
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    input_tool = _Tool(
        {
            "ok": True,
            "data": {"input_request": input_request.model_dump(mode="json")},
        },
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

    assert input_tool.calls[0]["input"]["type"] == "photo"
    assert input_tool.calls[0]["input"]["min_artifacts"] == 1
    assert store.events[-1].type == "turn.completed"


async def test_report_completion_uses_save_report_tool_and_completes_turn() -> None:
    store = _Store()
    report_tool = _Tool({"ok": True, "data": {"report_id": "rpt_1"}}, [])
    runner = _Runner(
        [
            DiagnosticRunnerReportCompleted(
                summary="Likely indexing issue.",
                report={
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
            ),
        ],
    )

    await _orchestrator(
        store=store,
        runner=runner,
        report_tool=report_tool,
    ).process_turn(current_user=_user(), turn=_turn())

    assert report_tool.calls[0]["input"]["summary"] == "Likely indexing issue."
    assert store.events[-1].type == "turn.completed"
    assert store.events[-1].data["session"] == repair_session_from_model(
        store.session,
    ).model_dump(mode="json")


async def test_recoverable_processing_failure_persists_public_error() -> None:
    store = _Store()
    runner = _Runner(
        [
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

    assert [event.type for event in store.events] == ["error", "turn.completed"]
    assert store.events[0].data == {
        "code": "provider_timeout",
        "message": "Diagnostic processing timed out.",
        "retryable": True,
    }


async def test_runner_exception_persists_public_error() -> None:
    store = _Store()

    await _orchestrator(store=store, runner=_Runner(raises=True)).process_turn(
        current_user=_user(),
        turn=_turn(),
    )

    assert [event.type for event in store.events] == ["error", "turn.completed"]
    assert store.events[0].data["code"] == "diagnostic_processing_error"
