"""Diagnostic turn orchestration tests."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Mapping
from copy import copy
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import pytest
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import StatusCode

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
from bike_doc_api.schemas.observation_extraction import (
    ArtifactProcessingStatus,
    NormalizedModelImage,
)
from bike_doc_api.schemas.repair_session import repair_session_from_model
from bike_doc_api.services.diagnostic_completion_telemetry import (
    DiagnosticReportTelemetryOutcome,
)
from bike_doc_api.services.diagnostic_visual_context import (
    DiagnosticVisualContext,
    DiagnosticVisualContextError,
)


class _Store:
    """In-memory repositories for orchestration tests."""

    def __init__(self) -> None:
        self.turn_count = 1
        self.fail_completion_count = False
        self.phase_session = RepairPhaseSession(
            id="phs_orch",
            repair_session_id="rs_orch",
            phase="diagnostic",
            adk_session_id="adk_internal_orch",
            diagnostic_report_schema_version="diagnostic_report.v2",
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
        self.artifacts = {self.artifact.id: self.artifact}

    async def get(self, phase_session_id: str) -> RepairPhaseSession | None:
        if phase_session_id == self.phase_session.id:
            return self.phase_session
        return None

    async def count_for_phase_session(self, repair_phase_session_id: str) -> int:
        if self.fail_completion_count:
            raise RuntimeError("telemetry count unavailable")
        return (
            self.turn_count if repair_phase_session_id == self.phase_session.id else 0
        )

    async def count_for_phase_session_through_start_event_sequence(
        self,
        *,
        repair_phase_session_id: str,
        start_event_sequence: int,
    ) -> int:
        if (
            repair_phase_session_id == self.phase_session.id
            and start_event_sequence >= 1
        ):
            return 1
        return 0

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
        artifact = self.artifacts.get(artifact_id)
        if artifact is not None and user_id == artifact.user_id:
            return artifact
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


class _VisualContext:
    """Fake accepted-turn visual preparation boundary."""

    def __init__(self, context: DiagnosticVisualContext | None = None) -> None:
        self.context = context or DiagnosticVisualContext(True, (), (), (), (), ())
        self.calls: list[tuple[str, str]] = []
        self.agent_started_turn_ids: list[str] = []

    async def prepare_turn(
        self,
        *,
        user_id: str,
        turn_id: str,
    ) -> DiagnosticVisualContext:
        self.calls.append((user_id, turn_id))
        return self.context

    async def mark_diagnostic_agent_started(self, *, turn_id: str) -> None:
        self.agent_started_turn_ids.append(turn_id)


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


class _ExpiringTurn:
    """Turn double that raises if ORM-like attributes are read after expiry."""

    def __init__(self) -> None:
        self.expired = False
        self.message = {"text": "The brake lever bottoms out.", "artifact_ids": []}

    @property
    def id(self) -> str:
        self._raise_if_expired()
        return "turn_orch"

    @property
    def repair_session_id(self) -> str:
        self._raise_if_expired()
        return "rs_orch"

    @property
    def repair_phase_session_id(self) -> str:
        self._raise_if_expired()
        return "phs_orch"

    @property
    def start_event_sequence(self) -> int:
        self._raise_if_expired()
        return 1

    def _raise_if_expired(self) -> None:
        if self.expired:
            raise RuntimeError("expired ORM attribute refresh attempted")


class _ExpiringUser:
    """User double that raises if ORM-like attributes are read after expiry."""

    def __init__(self) -> None:
        self.expired = False

    @property
    def id(self) -> str:
        self._raise_if_expired()
        return "usr_orch"

    @property
    def skill_level(self) -> str:
        self._raise_if_expired()
        return "beginner"

    def _raise_if_expired(self) -> None:
        if self.expired:
            raise RuntimeError("expired user ORM attribute refresh attempted")


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
    visual_context: _VisualContext | None = None,
    telemetry: _Telemetry | None = None,
) -> DiagnosticTurnOrchestrator:
    calls: list[dict[str, Any]] = []
    return DiagnosticTurnOrchestrator(
        phase_sessions=store,
        turns=store,
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
        visual_context=visual_context or _VisualContext(),
        telemetry=telemetry or _Telemetry(),
    )


def _span_exporter(
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[InMemorySpanExporter, TracerProvider]:
    """Install an isolated provider at the orchestration tracing seam."""

    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    monkeypatch.setattr(
        "bike_doc_api.adk.orchestration.trace.get_tracer",
        lambda _name: provider.get_tracer("orchestration-test"),
    )
    return exporter, provider


async def test_orchestration_emits_required_ordered_child_span_tree(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    exporter, provider = _span_exporter(monkeypatch)
    runner = _Runner([DiagnosticRunnerAssistantDelta("ASSISTANT_SENTINEL")])

    with provider.get_tracer("orchestration-test").start_as_current_span(
        "bike_doc.diagnostic.turn"
    ):
        await _orchestrator(store=_Store(), runner=runner).process_turn(
            current_user=_user(), turn=_turn()
        )

    spans = exporter.get_finished_spans()
    by_name = {span.name: span for span in spans}
    root = by_name["bike_doc.diagnostic.turn"]
    expected = {
        "bike_doc.diagnostic.visual_context.prepare",
        "bike_doc.diagnostic.seed_context.build",
        "bike_doc.diagnostic.agent.run",
        "bike_doc.diagnostic.turn.finalize",
    }
    assert expected <= by_name.keys()
    assert all(
        by_name[name].parent is not None
        and by_name[name].parent.span_id == root.context.span_id
        for name in expected
    )
    for name in (
        "bike_doc.diagnostic.seed.get_bike_profile",
        "bike_doc.diagnostic.seed.lookup_repair_history",
        "bike_doc.diagnostic.seed.list_artifacts",
    ):
        assert by_name[name].parent is not None
        assert (
            by_name[name].parent.span_id
            == by_name["bike_doc.diagnostic.seed_context.build"].context.span_id
        )
        assert set(by_name[name].attributes) <= {
            "bike_doc.seed.success",
            "bike_doc.seed.returned_item_count",
            "bike_doc.seed.error_code",
        }
    assert [span.name for span in spans if span.name.startswith("bike_doc.")] == [
        "bike_doc.diagnostic.visual_context.prepare",
        "bike_doc.diagnostic.seed.get_bike_profile",
        "bike_doc.diagnostic.seed.lookup_repair_history",
        "bike_doc.diagnostic.seed.list_artifacts",
        "bike_doc.diagnostic.seed_context.build",
        "bike_doc.diagnostic.agent.run",
        "bike_doc.diagnostic.turn.finalize",
        "bike_doc.diagnostic.turn",
    ]
    assert "ASSISTANT_SENTINEL" not in str(
        [(span.attributes, span.events) for span in spans]
    )
    provider.shutdown()


async def test_visual_blocked_turn_has_no_agent_span(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    exporter, provider = _span_exporter(monkeypatch)
    visual_context = _VisualContext(DiagnosticVisualContext(False, (), (), (), (), ()))

    with provider.get_tracer("orchestration-test").start_as_current_span(
        "bike_doc.diagnostic.turn"
    ):
        await _orchestrator(
            store=_Store(), runner=_Runner(), visual_context=visual_context
        ).process_turn(current_user=_user(), turn=_turn(text=None, artifact_ids=[]))

    names = [span.name for span in exporter.get_finished_spans()]
    assert "bike_doc.diagnostic.agent.run" not in names
    assert "bike_doc.diagnostic.turn.finalize" in names
    provider.shutdown()


async def test_runner_failure_and_cancellation_close_agent_spans(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    exporter, provider = _span_exporter(monkeypatch)
    tracer = provider.get_tracer("orchestration-test")

    with tracer.start_as_current_span("bike_doc.diagnostic.turn"):
        await _orchestrator(store=_Store(), runner=_Runner(raises=True)).process_turn(
            current_user=_user(), turn=_turn()
        )

    failed_agent = next(
        span
        for span in exporter.get_finished_spans()
        if span.name == "bike_doc.diagnostic.agent.run"
    )
    assert failed_agent.status.status_code is StatusCode.ERROR
    assert any(
        span.name == "bike_doc.diagnostic.turn.finalize"
        for span in exporter.get_finished_spans()
    )

    exporter.clear()
    with (
        tracer.start_as_current_span("bike_doc.diagnostic.turn"),
        pytest.raises(asyncio.CancelledError),
    ):
        await _orchestrator(
            store=_Store(),
            runner=_Runner([asyncio.CancelledError()]),
        ).process_turn(current_user=_user(), turn=_turn())

    cancelled_agent = next(
        span
        for span in exporter.get_finished_spans()
        if span.name == "bike_doc.diagnostic.agent.run"
    )
    assert cancelled_agent.status.status_code is StatusCode.UNSET
    assert all(
        span.name != "bike_doc.diagnostic.turn.finalize"
        for span in exporter.get_finished_spans()
    )
    provider.shutdown()


async def test_accepted_turn_invokes_runner_with_server_owned_context() -> None:
    store = _Store()
    runner = _Runner()
    visual_context = _VisualContext()

    await _orchestrator(
        store=store, runner=runner, visual_context=visual_context
    ).process_turn(
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
    assert request.diagnostic_report_schema_version == "diagnostic_report.v2"
    assert request.adk_session_id == "adk_internal_orch"
    assert request.message_text == "The chain skips."
    assert request.artifact_ids == ("art_1",)
    assert request.bike_profile == {"id": "bike_orch"}
    assert request.repair_history == ()
    assert request.diagnostic_artifacts == ({"id": "art_1"},)
    assert request.current_images == ()
    assert request.current_observations == ()
    assert request.prior_observations == ()
    assert request.artifact_processing_statuses == ()
    assert visual_context.agent_started_turn_ids == ["turn_orch"]
    assert [event.type for event in store.events] == [
        "artifact.referenced",
        "turn.completed",
    ]
    assert store.events[-1].data["session"]["status"] == "awaiting_user"


async def test_orchestration_uses_committed_terminal_notifications() -> None:
    store = _Store()
    telemetry = _Telemetry()
    await _orchestrator(
        store=store,
        telemetry=telemetry,
        runner=_Runner(
            [
                DiagnosticRunnerInputRequested("measurement", "ignored"),
                DiagnosticRunnerReportCompleted(
                    report_id="rpt_1",
                    schema_version="diagnostic_report.v2",
                    observed_finding_count=0,
                    contributing_factor_count=0,
                    alternate_hypothesis_count=2,
                ),
            ],
        ),
    ).process_turn(current_user=_user(), turn=_turn())

    assert store.events[-1].data["session"]["status"] == "awaiting_decision"
    assert telemetry.input_versions == ["diagnostic_report.v2"]


async def test_report_creating_execution_emits_session_summary_after_commit() -> None:
    store = _Store()
    store.turn_count = 3
    telemetry = _Telemetry()
    await _orchestrator(
        store=store,
        telemetry=telemetry,
        runner=_Runner(
            [
                DiagnosticRunnerReportCompleted(
                    report_id="rpt_1",
                    schema_version="diagnostic_report.v2",
                    diagnostic_session_id="phs_orch",
                    created_by_current_execution=True,
                    report_created_at=datetime(2026, 1, 1, 0, 5, tzinfo=UTC),
                    completion_reason="diagnosis_supported",
                ),
            ],
        ),
    ).process_turn(current_user=_user(), turn=_turn())

    assert telemetry.session_completions == [
        (
            "rs_orch",
            "phs_orch",
            "diagnosis_supported",
            3,
            300000,
            "diagnostic_report.v2",
        )
    ]


async def test_observed_report_does_not_emit_session_summary() -> None:
    store = _Store()
    telemetry = _Telemetry()
    await _orchestrator(
        store=store,
        telemetry=telemetry,
        runner=_Runner(
            [
                DiagnosticRunnerReportCompleted(
                    report_id="rpt_existing",
                    schema_version="diagnostic_report.v2",
                    diagnostic_session_id="phs_orch",
                    completion_reason="diagnosis_supported",
                ),
            ],
        ),
    ).process_turn(current_user=_user(), turn=_turn())

    assert telemetry.session_completions == []


async def test_session_summary_query_failure_does_not_change_terminal_turn() -> None:
    store = _Store()
    store.fail_completion_count = True
    telemetry = _Telemetry()
    await _orchestrator(
        store=store,
        telemetry=telemetry,
        runner=_Runner(
            [
                DiagnosticRunnerReportCompleted(
                    report_id="rpt_1",
                    schema_version="diagnostic_report.v2",
                    created_by_current_execution=True,
                    report_created_at=datetime(2026, 1, 1, 0, 5, tzinfo=UTC),
                    completion_reason="diagnosis_supported",
                ),
            ],
        ),
    ).process_turn(current_user=_user(), turn=_turn())

    assert telemetry.session_completions == []
    assert store.events[-1].type == "turn.completed"
    assert store.events[-1].data["session"]["status"] == "awaiting_decision"


async def test_pixels_only_turn_passes_labeled_pixels_to_runner() -> None:
    store = _Store()
    runner = _Runner()
    image = NormalizedModelImage(
        artifact_id="art_1",
        mime_type="image/jpeg",
        content=b"normalized-pixels",
        original_width=100,
        original_height=80,
        normalized_width=100,
        normalized_height=80,
        content_sha256="a" * 64,
        preprocessing_version="image-normalization.v1",
    )
    visual_context = _VisualContext(
        DiagnosticVisualContext(
            True,
            (image,),
            (),
            (),
            (
                ArtifactProcessingStatus(
                    artifact_id="art_1",
                    status="available",
                ),
            ),
            (),
        )
    )

    await _orchestrator(
        store=store,
        runner=runner,
        visual_context=visual_context,
    ).process_turn(current_user=_user(), turn=_turn(artifact_ids=["art_1"]))

    assert visual_context.calls == [("usr_orch", "turn_orch")]
    assert runner.requests[0].current_images == (image,)
    assert runner.requests[0].artifact_processing_statuses[0].artifact_id == "art_1"
    assert runner.requests[0].current_observations == ()
    assert runner.requests[0].prior_observations == ()


async def test_image_only_visual_failure_persists_error_and_skips_runner() -> None:
    store = _Store()
    runner = _Runner()
    visual_context = _VisualContext(
        DiagnosticVisualContext(
            False,
            (),
            (),
            (),
            (
                ArtifactProcessingStatus(
                    artifact_id="art_1",
                    status="unavailable",
                    failure_code="image_analysis_unavailable",
                ),
            ),
            (
                DiagnosticVisualContextError(
                    code="image_analysis_unavailable",
                    artifact_id=None,
                    retryable=False,
                ),
            ),
        )
    )

    await _orchestrator(
        store=store,
        runner=runner,
        visual_context=visual_context,
    ).process_turn(current_user=_user(), turn=_turn(text=None, artifact_ids=["art_1"]))

    assert runner.stream_called == 0
    assert [event.type for event in store.events] == [
        "artifact.referenced",
        "error",
        "turn.completed",
    ]
    assert store.events[1].data == {
        "code": "image_analysis_unavailable",
        "message": "Image analysis is unavailable for this turn.",
        "retryable": False,
    }
    assert store.events[-1].data["session"]["status"] == "awaiting_user"


async def test_partial_visual_failure_persists_and_passes_valid_pixels() -> None:
    store = _Store()
    runner = _Runner()
    image = NormalizedModelImage(
        artifact_id="art_1",
        mime_type="image/jpeg",
        content=b"normalized-pixels",
        original_width=100,
        original_height=80,
        normalized_width=100,
        normalized_height=80,
        content_sha256="a" * 64,
        preprocessing_version="image-normalization.v1",
    )
    visual_context = _VisualContext(
        DiagnosticVisualContext(
            True,
            (image,),
            (),
            (),
            (
                ArtifactProcessingStatus(artifact_id="art_1", status="available"),
                ArtifactProcessingStatus(
                    artifact_id="art_bad",
                    status="unavailable",
                    failure_code="image_decode_failed",
                ),
            ),
            (
                DiagnosticVisualContextError(
                    code="image_decode_failed",
                    artifact_id="art_bad",
                    retryable=False,
                ),
            ),
        )
    )
    excluded_artifact = copy(store.artifact)
    excluded_artifact.id = "art_bad"
    store.artifacts[excluded_artifact.id] = excluded_artifact

    await _orchestrator(
        store=store,
        runner=runner,
        visual_context=visual_context,
    ).process_turn(
        current_user=_user(),
        turn=_turn(artifact_ids=["art_1", "art_bad"]),
    )

    assert runner.stream_called == 1
    assert runner.requests[0].current_images == (image,)
    assert [event.type for event in store.events] == [
        "artifact.referenced",
        "artifact.referenced",
        "error",
        "turn.completed",
    ]
    assert store.events[2].data == {
        "code": "image_decode_failed",
        "message": "Image could not be decoded.",
        "retryable": False,
        "artifact_id": "art_bad",
    }


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


async def test_post_report_runner_failure_preserves_committed_report_status() -> None:
    store = _Store()
    runner = _Runner(
        [
            DiagnosticRunnerReportCompleted(
                report_id="rpt_1",
                schema_version="diagnostic_report.v2",
                observed_finding_count=1,
            ),
            RuntimeError("provider failed after report commit"),
        ],
    )

    await _orchestrator(store=store, runner=runner).process_turn(
        current_user=_user(),
        turn=_turn(),
    )

    assert [event.type for event in store.events] == ["error", "turn.completed"]
    assert store.events[-1].data["session"]["status"] == "awaiting_decision"


async def test_turn_scalar_snapshot_survives_expired_orm_state() -> None:
    store = _Store()
    turn = _ExpiringTurn()
    runner = _Runner(
        [RuntimeError("provider failed after session rollback")],
        before_emit={0: lambda: setattr(turn, "expired", True)},
    )

    await _orchestrator(store=store, runner=runner).process_turn(
        current_user=_user(),
        turn=turn,  # type: ignore[arg-type]
    )

    assert [event.type for event in store.events] == ["error", "turn.completed"]
    assert store.events[0].turn_id == "turn_orch"
    assert store.events[0].repair_session_id == "rs_orch"
    assert store.events[-1].data["session"]["status"] == "awaiting_user"


async def test_user_scalar_snapshot_survives_tool_commit_expiry() -> None:
    store = _Store()
    user = _ExpiringUser()
    runner = _Runner(
        [
            DiagnosticRunnerInputRequested(
                request_type="photo",
                prompt="Upload brake photos.",
                accepted_media_types=("image/jpeg",),
                max_artifacts=2,
            ),
        ],
        before_emit={0: lambda: setattr(user, "expired", True)},
    )

    await _orchestrator(store=store, runner=runner).process_turn(
        current_user=user,  # type: ignore[arg-type]
        turn=_turn(),
    )

    assert [event.type for event in store.events] == ["turn.completed"]
    assert store.events[-1].data["session"]["status"] == "awaiting_user"


class _Telemetry:
    """Captures the existing completion signal until its dedicated replacement."""

    def __init__(self) -> None:
        self.input_versions: list[str] = []
        self.completed: list[DiagnosticReportTelemetryOutcome] = []
        self.session_completions: list[tuple[str, str, str, int, int, str]] = []

    def input_requested(self, *, schema_version: str) -> None:
        self.input_versions.append(schema_version)

    def report_completed(self, *, outcome: DiagnosticReportTelemetryOutcome) -> None:
        self.completed.append(outcome)

    def report_validation_failed(
        self, *, stage: str, attempt_number: int, schema_version: str
    ) -> None:
        raise AssertionError(f"unexpected validation signal: {schema_version}")

    def session_completed(self, *, completion: Any) -> None:
        self.session_completions.append(
            (
                completion.repair_session_id,
                completion.diagnostic_session_id,
                completion.completion_reason,
                completion.turn_count,
                int(
                    (
                        completion.report_created_at
                        - completion.phase_session_created_at
                    ).total_seconds()
                    * 1000
                ),
                completion.report_schema_version,
            )
        )
