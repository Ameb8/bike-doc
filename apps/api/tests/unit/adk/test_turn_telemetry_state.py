"""State-machine tests for privacy-safe diagnostic-turn telemetry facts."""

from __future__ import annotations

import pytest

from bike_doc_api.adk.turn_telemetry_state import DiagnosticTurnTelemetryState
from bike_doc_api.schemas.common import RepairSessionStatus


class _Clock:
    def __init__(self) -> None:
        self.value = 10.0

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


def _state(clock: _Clock) -> DiagnosticTurnTelemetryState:
    return DiagnosticTurnTelemetryState(clock=clock, artifact_count=2)


@pytest.mark.parametrize(
    ("record", "outcome", "status"),
    [
        (
            lambda state: state.note_input_requested(
                request_type="photo", required=True
            ),
            "input_requested",
            RepairSessionStatus.AWAITING_USER,
        ),
        (
            lambda state: state.note_report_completed(
                observed_finding_count=1,
                contributing_factor_count=0,
                alternate_hypothesis_count=0,
                completion_reason="diagnosis_supported",
            ),
            "report_completed",
            RepairSessionStatus.AWAITING_DECISION,
        ),
        (
            lambda state: state.note_visual_context(
                invoke_agent=False,
                current_image_count=0,
                current_observation_count=0,
                prior_observation_count=0,
            ),
            "visual_context_blocked",
            RepairSessionStatus.AWAITING_USER,
        ),
        (
            lambda state: state.note_error(code="provider_timeout", retryable=True),
            "recoverable_error",
            RepairSessionStatus.AWAITING_USER,
        ),
        (
            lambda state: state.note_error(
                code="runner_output_invalid", retryable=False
            ),
            "terminal_error",
            RepairSessionStatus.FAILED,
        ),
        (lambda state: None, "no_terminal_action", RepairSessionStatus.AWAITING_USER),
        (
            lambda state: state.note_cancelled(),
            "cancelled",
            RepairSessionStatus.CANCELLED,
        ),
    ],
)
def test_finalizes_each_allowed_outcome_once(
    record: object, outcome: str, status: RepairSessionStatus
) -> None:
    clock = _Clock()
    state = _state(clock)
    record(state)  # type: ignore[operator]

    snapshot = state.finalize()

    assert snapshot.outcome == outcome
    assert snapshot.terminal_status is status


def test_durable_report_outranks_input_error_and_cancellation() -> None:
    clock = _Clock()
    state = _state(clock)
    state.note_input_requested(request_type="photo", required=True)
    state.note_report_completed(
        observed_finding_count=1,
        contributing_factor_count=2,
        alternate_hypothesis_count=3,
        completion_reason="diagnosis_supported",
    )
    state.note_error(code="runner_output_invalid", retryable=False)
    state.note_cancelled()

    snapshot = state.finalize()

    assert snapshot.outcome == "report_completed"
    assert snapshot.terminal_status is RepairSessionStatus.AWAITING_DECISION
    assert snapshot.terminal_action_kinds == ("input_request", "report")
    assert snapshot.multiple_terminal_actions is True
    assert snapshot.error_code == "runner_output_invalid"


def test_input_outranks_visual_cancellation_and_error() -> None:
    clock = _Clock()
    state = _state(clock)
    state.note_visual_context(
        invoke_agent=False,
        current_image_count=0,
        current_observation_count=0,
        prior_observation_count=0,
    )
    state.note_input_requested(request_type="text", required=False)
    state.note_error(code="provider_timeout", retryable=True)
    state.note_cancelled()

    assert state.finalize().outcome == "input_requested"


def test_records_monotonic_timing_and_counts_without_content() -> None:
    clock = _Clock()
    state = _state(clock)
    state.note_visual_context(
        invoke_agent=True,
        current_image_count=1,
        current_observation_count=2,
        prior_observation_count=3,
    )
    clock.advance(0.250)
    state.note_agent_started()
    clock.advance(0.500)
    state.note_assistant_delta()
    clock.advance(0.125)
    state.note_assistant_message()
    clock.advance(0.125)
    state.note_agent_ended()
    clock.advance(0.250)

    snapshot = state.finalize()

    assert snapshot.duration_ms == 1250
    assert snapshot.agent_run_duration_ms == 750
    assert snapshot.time_to_first_output_ms == 750
    assert (
        snapshot.artifact_count,
        snapshot.current_image_count,
        snapshot.current_observation_count,
        snapshot.prior_observation_count,
    ) == (2, 1, 2, 3)
    assert (snapshot.assistant_delta_count, snapshot.assistant_message_count) == (1, 1)


def test_safety_blocking_is_orthogonal_and_sets_terminal_status() -> None:
    clock = _Clock()
    state = _state(clock)
    state.note_safety_escalated(safety_state="blocked", blocks=True)
    state.note_report_completed(
        observed_finding_count=1,
        contributing_factor_count=0,
        alternate_hypothesis_count=0,
        completion_reason="diagnosis_supported",
    )

    snapshot = state.finalize()

    assert snapshot.outcome == "report_completed"
    assert snapshot.terminal_status is RepairSessionStatus.BLOCKED_SAFETY
    assert snapshot.safety.escalated is True


def test_bounds_untrusted_dimensions_and_excludes_arbitrary_content() -> None:
    clock = _Clock()
    state = _state(clock)
    state.note_input_requested(request_type="prompt with user content", required=True)
    state.note_validation_failure(stage="secret.field.path")
    state.note_error(code="provider said: secret prompt", retryable=True)

    snapshot = state.finalize()

    assert snapshot.input_request is not None
    assert snapshot.input_request.request_type == "unknown"
    assert snapshot.validation.stages == ("unknown",)
    assert snapshot.error_code == "unknown"
    assert "secret" not in repr(snapshot)


def test_counts_repeated_actions_validation_safety_and_errors() -> None:
    clock = _Clock()
    state = _state(clock)
    state.note_input_requested(request_type="photo", required=True)
    state.note_input_requested(request_type="confirmation", required=False)
    state.note_validation_failure(stage="report_schema")
    state.note_validation_failure(stage="not-an-approved-stage")
    state.note_safety_escalated(safety_state="caution", blocks=False)
    state.note_safety_escalated(safety_state="blocked", blocks=True)
    state.note_error(code="provider_timeout", retryable=True)
    state.note_error(code="runner_output_invalid", retryable=False)

    snapshot = state.finalize()

    assert snapshot.terminal_action_count == 2
    assert snapshot.terminal_action_kinds == ("input_request", "input_request")
    assert snapshot.validation.attempt_count == 2
    assert snapshot.validation.stages == ("report_schema", "unknown")
    assert snapshot.safety.escalation_count == 2
    assert snapshot.error_count == 2
    assert snapshot.error_code == "runner_output_invalid"


def test_finalization_returns_one_frozen_snapshot() -> None:
    clock = _Clock()
    state = _state(clock)
    clock.advance(1)
    first = state.finalize()
    state.note_report_completed(
        observed_finding_count=1,
        contributing_factor_count=0,
        alternate_hypothesis_count=0,
        completion_reason="diagnosis_supported",
    )
    clock.advance(1)

    second = state.finalize()

    assert second is first
    assert second.outcome == "no_terminal_action"
    assert second.duration_ms == 1000
