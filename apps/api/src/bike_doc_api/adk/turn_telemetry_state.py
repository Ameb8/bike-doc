"""Privacy-safe, process-local facts for one diagnostic orchestration turn.

This module deliberately has no logging, tracing, metrics, persistence, or
product-event responsibilities.  It is the single deterministic place that
derives a Level 2 turn outcome from normalized orchestration facts.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from time import monotonic
from typing import Literal, cast

from bike_doc_api.schemas.common import RepairSessionStatus

DiagnosticTurnOutcome = Literal[
    "input_requested",
    "report_completed",
    "visual_context_blocked",
    "recoverable_error",
    "terminal_error",
    "no_terminal_action",
    "cancelled",
]
InputRequestType = Literal[
    "text", "photo", "multiple_choice", "confirmation", "none", "unknown"
]
SafetyState = Literal["ok", "caution", "shop_recommended", "blocked", "unknown"]
CompletionReason = Literal[
    "diagnosis_supported", "insufficient_evidence", "safety_escalation", "unknown"
]
ValidationStage = Literal[
    "completion_basis",
    "report_schema",
    "artifact_reference",
    "phase_state",
    "tool_input",
    "unknown",
]
BoundedErrorCode = Literal[
    "diagnostic_processing_error",
    "image_analysis_unavailable",
    "image_not_ready",
    "image_decode_failed",
    "image_normalization_failed",
    "provider_timeout",
    "runner_output_invalid",
    "report_validation_failed",
    "unknown",
]
TerminalActionKind = Literal["input_request", "report"]
MonotonicClock = Callable[[], float]

_INPUT_TYPES = {"text", "photo", "multiple_choice", "confirmation", "none"}
_SAFETY_STATES = {"ok", "caution", "shop_recommended", "blocked"}
_COMPLETION_REASONS = {
    "diagnosis_supported",
    "insufficient_evidence",
    "safety_escalation",
}
_VALIDATION_STAGES = {
    "completion_basis",
    "report_schema",
    "artifact_reference",
    "phase_state",
    "tool_input",
}
_ERROR_CODES = {
    "diagnostic_processing_error",
    "image_analysis_unavailable",
    "image_not_ready",
    "image_decode_failed",
    "image_normalization_failed",
    "provider_timeout",
    "runner_output_invalid",
    "report_validation_failed",
}


@dataclass(frozen=True, slots=True)
class DiagnosticInputRequestTelemetry:
    """Approved, content-free input-request dimensions."""

    request_type: InputRequestType
    required: bool


@dataclass(frozen=True, slots=True)
class DiagnosticSafetyTelemetry:
    """Bounded safety facts observed during the turn."""

    escalated: bool
    escalation_count: int
    safety_state: SafetyState | None
    flag_codes: tuple[str, ...]
    severities: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class DiagnosticReportTelemetry:
    """Bounded report-composition dimensions."""

    completed: bool
    observed_finding_count: int | None
    contributing_factor_count: int | None
    alternate_hypothesis_count: int | None
    completion_reason: CompletionReason | None


@dataclass(frozen=True, slots=True)
class DiagnosticValidationTelemetry:
    """Bounded report-validation attempt facts."""

    attempt_count: int
    stages: tuple[ValidationStage, ...]


@dataclass(frozen=True, slots=True)
class DiagnosticTurnTelemetrySnapshot:
    """Immutable, privacy-safe final state for a single orchestration attempt."""

    outcome: DiagnosticTurnOutcome
    terminal_status: RepairSessionStatus
    duration_ms: int
    agent_run_duration_ms: int | None
    time_to_first_output_ms: int | None
    artifact_count: int
    current_image_count: int
    current_observation_count: int
    prior_observation_count: int
    assistant_delta_count: int
    assistant_message_count: int
    terminal_action_count: int
    terminal_action_kinds: tuple[TerminalActionKind, ...]
    multiple_terminal_actions: bool
    input_request: DiagnosticInputRequestTelemetry | None
    safety: DiagnosticSafetyTelemetry
    report: DiagnosticReportTelemetry
    validation: DiagnosticValidationTelemetry
    error_code: BoundedErrorCode | None
    error_retryable: bool | None
    error_count: int


class DiagnosticTurnTelemetryState:
    """Accumulate normalized facts and select outcome once at finalization."""

    def __init__(
        self, *, clock: MonotonicClock | None = None, artifact_count: int = 0
    ) -> None:
        self._clock = clock or monotonic
        self._started_at = self._clock()
        self._agent_started_at: float | None = None
        self._agent_ended_at: float | None = None
        self._first_output_at: float | None = None
        self._artifact_count = artifact_count
        self._current_image_count = 0
        self._current_observation_count = 0
        self._prior_observation_count = 0
        self._delta_count = 0
        self._message_count = 0
        self._actions: list[TerminalActionKind] = []
        self._input: DiagnosticInputRequestTelemetry | None = None
        self._safety_escalated = False
        self._safety_escalation_count = 0
        self._safety_state: SafetyState | None = None
        self._safety_flag_codes: list[str] = []
        self._safety_severities: list[str] = []
        self._report: DiagnosticReportTelemetry | None = None
        self._validation_stages: list[ValidationStage] = []
        self._error: tuple[BoundedErrorCode, bool] | None = None
        self._error_count = 0
        self._visual_blocked = False
        self._cancelled = False
        self._final_snapshot: DiagnosticTurnTelemetrySnapshot | None = None

    def note_visual_context(
        self,
        *,
        invoke_agent: bool,
        current_image_count: int,
        current_observation_count: int,
        prior_observation_count: int,
    ) -> None:
        self._current_image_count = current_image_count
        self._current_observation_count = current_observation_count
        self._prior_observation_count = prior_observation_count
        self._visual_blocked = not invoke_agent

    def note_agent_started(self) -> None:
        self._agent_started_at = self._clock()

    def note_agent_ended(self) -> None:
        self._agent_ended_at = self._clock()

    def note_assistant_delta(self) -> None:
        self._delta_count += 1
        self._note_first_output()

    def note_assistant_message(self) -> None:
        self._message_count += 1
        self._note_first_output()

    def note_input_requested(self, *, request_type: str, required: bool) -> None:
        self._actions.append("input_request")
        self._input = DiagnosticInputRequestTelemetry(
            request_type=cast(InputRequestType, _bounded(request_type, _INPUT_TYPES)),
            required=required,
        )

    def note_report_completed(
        self,
        *,
        observed_finding_count: int,
        contributing_factor_count: int,
        alternate_hypothesis_count: int,
        completion_reason: str | None,
    ) -> None:
        self._actions.append("report")
        self._report = DiagnosticReportTelemetry(
            completed=True,
            observed_finding_count=max(0, observed_finding_count),
            contributing_factor_count=max(0, contributing_factor_count),
            alternate_hypothesis_count=max(0, alternate_hypothesis_count),
            completion_reason=(
                cast(
                    CompletionReason,
                    _bounded(completion_reason, _COMPLETION_REASONS),
                )
                if completion_reason is not None
                else None
            ),
        )

    def note_safety_escalated(
        self,
        *,
        safety_state: str | None,
        blocks: bool,
        safety_flags: tuple[dict[str, object], ...] = (),
    ) -> None:
        self._safety_escalated = True
        self._safety_escalation_count += 1
        for flag in safety_flags:
            code, severity = flag.get("code"), flag.get("severity")
            if isinstance(code, str) and isinstance(severity, str):
                self._safety_flag_codes.append(code)
                self._safety_severities.append(severity)
        self.note_safety_state(safety_state=safety_state, blocks=blocks)

    def note_safety_state(self, *, safety_state: str | None, blocks: bool) -> None:
        """Record a durable safety state without claiming a new escalation."""
        state = (
            cast(SafetyState, _bounded(safety_state, _SAFETY_STATES))
            if safety_state
            else None
        )
        self._safety_state = "blocked" if blocks else state

    def note_validation_failure(self, *, stage: str) -> None:
        self._validation_stages.append(
            cast(ValidationStage, _bounded(stage, _VALIDATION_STAGES))
        )

    def note_error(self, *, code: str, retryable: bool) -> None:
        # A terminal error outranks a prior retryable error in the absence of a
        # durable terminal action; retain one bounded error fact only.
        normalized = cast(BoundedErrorCode, _bounded(code, _ERROR_CODES))
        self._error_count += 1
        if self._error is None or (not retryable and self._error[1]):
            self._error = (normalized, retryable)

    def note_cancelled(self) -> None:
        self._cancelled = True

    def finalize(self) -> DiagnosticTurnTelemetrySnapshot:
        """Freeze facts and apply Section 8 precedence exactly once."""
        if self._final_snapshot is not None:
            return self._final_snapshot
        ended_at = self._clock()
        report_completed = self._report is not None
        input_completed = self._input is not None
        if report_completed:
            outcome: DiagnosticTurnOutcome = "report_completed"
        elif input_completed:
            outcome = "input_requested"
        elif self._visual_blocked:
            outcome = "visual_context_blocked"
        elif self._cancelled:
            outcome = "cancelled"
        elif self._error is not None and not self._error[1]:
            outcome = "terminal_error"
        elif self._error is not None:
            outcome = "recoverable_error"
        else:
            outcome = "no_terminal_action"
        status = _terminal_status(outcome, self._safety_state == "blocked")
        agent_duration = None
        if self._agent_started_at is not None and self._agent_ended_at is not None:
            agent_duration = _elapsed_ms(self._agent_started_at, self._agent_ended_at)
        self._final_snapshot = DiagnosticTurnTelemetrySnapshot(
            outcome=outcome,
            terminal_status=status,
            duration_ms=_elapsed_ms(self._started_at, ended_at),
            agent_run_duration_ms=agent_duration,
            time_to_first_output_ms=(
                _elapsed_ms(self._started_at, self._first_output_at)
                if self._first_output_at is not None
                else None
            ),
            artifact_count=self._artifact_count,
            current_image_count=self._current_image_count,
            current_observation_count=self._current_observation_count,
            prior_observation_count=self._prior_observation_count,
            assistant_delta_count=self._delta_count,
            assistant_message_count=self._message_count,
            terminal_action_count=len(self._actions),
            terminal_action_kinds=tuple(self._actions),
            multiple_terminal_actions=len(self._actions) > 1,
            input_request=self._input,
            safety=DiagnosticSafetyTelemetry(
                self._safety_escalated,
                self._safety_escalation_count,
                self._safety_state,
                tuple(self._safety_flag_codes),
                tuple(self._safety_severities),
            ),
            report=self._report
            or DiagnosticReportTelemetry(False, None, None, None, None),
            validation=DiagnosticValidationTelemetry(
                len(self._validation_stages), tuple(self._validation_stages)
            ),
            error_code=self._error[0] if self._error else None,
            error_retryable=self._error[1] if self._error else None,
            error_count=self._error_count,
        )
        return self._final_snapshot

    def _note_first_output(self) -> None:
        if self._first_output_at is None:
            self._first_output_at = self._clock()


def _bounded(value: str | None, allowed: set[str]) -> str:
    return value if value in allowed else "unknown"


def _elapsed_ms(start: float, end: float) -> int:
    return max(0, int((end - start) * 1000))


def _terminal_status(
    outcome: DiagnosticTurnOutcome, safety_blocked: bool
) -> RepairSessionStatus:
    if safety_blocked:
        return RepairSessionStatus.BLOCKED_SAFETY
    if outcome == "report_completed":
        return RepairSessionStatus.AWAITING_DECISION
    if outcome == "terminal_error":
        return RepairSessionStatus.FAILED
    if outcome == "cancelled":
        return RepairSessionStatus.CANCELLED
    return RepairSessionStatus.AWAITING_USER
