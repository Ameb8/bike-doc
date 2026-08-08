"""Privacy-safe, stable telemetry for diagnostic report rollout monitoring.

Only bounded scalar dimensions are accepted.  Report content, agent prompts,
completion-basis rationale, and model/provider traces are intentionally not
representable at this boundary.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Literal, Protocol, cast

import structlog
from opentelemetry import metrics, trace
from opentelemetry.metrics import Counter, Histogram, Meter
from opentelemetry.sdk.metrics import MeterProvider

from bike_doc_api.adk.turn_telemetry_state import DiagnosticTurnTelemetrySnapshot

logger = structlog.get_logger(__name__)

DiagnosticReportSchemaVersion = Literal["diagnostic_report.v1", "diagnostic_report.v2"]

_DURATION_BOUNDARIES = (0.1, 0.25, 0.5, 1, 2.5, 5, 10, 20, 30, 60, 120, 300)
_COUNT_BOUNDARIES = (0, 1, 2, 3, 4, 5, 10, 20)
_OUTCOMES = frozenset(
    {
        "input_requested",
        "report_completed",
        "visual_context_blocked",
        "recoverable_error",
        "terminal_error",
        "no_terminal_action",
        "cancelled",
    }
)
_TERMINAL_STATUSES = frozenset(
    {"awaiting_user", "awaiting_decision", "blocked_safety", "failed", "cancelled"}
)
_REQUEST_TYPES = frozenset(
    {"text", "photo", "multiple_choice", "confirmation", "none", "unknown"}
)
_COMPLETION_REASONS = frozenset(
    {"diagnosis_supported", "insufficient_evidence", "safety_escalation", "unknown"}
)
_VALIDATION_STAGES = frozenset(
    {
        "completion_basis",
        "report_schema",
        "artifact_reference",
        "phase_state",
        "tool_input",
        "unknown",
    }
)
_ERROR_CODES = frozenset(
    {
        "diagnostic_processing_error",
        "image_analysis_unavailable",
        "image_not_ready",
        "image_decode_failed",
        "image_normalization_failed",
        "provider_timeout",
        "runner_output_invalid",
        "report_validation_failed",
        "unknown",
    }
)
_SAFETY_SEVERITIES = frozenset({"info", "caution", "warning", "blocking"})
_SAFETY_STATES = frozenset({"ok", "caution", "shop_recommended", "blocked", "unknown"})
_SAFETY_CODES = frozenset(
    {
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
    }
)


@dataclass(frozen=True, slots=True)
class DiagnosticMetricDimensions:
    """Process-safe bounded dimensions resolved for one execution attempt."""

    provider: str
    model: str
    prompt_version: str
    report_schema_version: str
    image_analysis_mode: str


@dataclass(frozen=True, slots=True)
class _MetricInstruments:
    turn_count: Counter
    turn_duration: Histogram
    first_output: Histogram
    input_request: Counter
    report: Counter
    report_item_count: Histogram
    validation_failure: Counter
    safety_escalation: Counter
    runner_error: Counter


def _create_instruments(meter: Meter) -> _MetricInstruments:
    return _MetricInstruments(
        meter.create_counter("bike_doc.diagnostic.turn.count", unit="{turn}"),
        meter.create_histogram(
            "bike_doc.diagnostic.turn.duration",
            unit="s",
            explicit_bucket_boundaries_advisory=_DURATION_BOUNDARIES,
        ),
        meter.create_histogram(
            "bike_doc.diagnostic.turn.time_to_first_output",
            unit="s",
            explicit_bucket_boundaries_advisory=_DURATION_BOUNDARIES,
        ),
        meter.create_counter(
            "bike_doc.diagnostic.input_request.count", unit="{request}"
        ),
        meter.create_counter("bike_doc.diagnostic.report.count", unit="{report}"),
        meter.create_histogram(
            "bike_doc.diagnostic.report.item_count",
            unit="{item}",
            explicit_bucket_boundaries_advisory=_COUNT_BOUNDARIES,
        ),
        meter.create_counter(
            "bike_doc.diagnostic.report_validation_failure.count", unit="{failure}"
        ),
        meter.create_counter(
            "bike_doc.diagnostic.safety_escalation.count", unit="{escalation}"
        ),
        meter.create_counter("bike_doc.diagnostic.runner_error.count", unit="{error}"),
    )


# Instruments are deliberately constructed once while this feature module loads.
_PROCESS_INSTRUMENTS = _create_instruments(metrics.get_meter(__name__))


@dataclass(frozen=True, slots=True)
class DiagnosticReportTelemetryOutcome:
    """Bounded dimensions emitted once for a completed diagnostic report."""

    schema_version: DiagnosticReportSchemaVersion
    observed_finding_count: int
    contributing_factor_count: int
    alternate_hypothesis_count: int
    completion_reason: str | None


class DiagnosticCompletionTelemetry(Protocol):
    """Stable operational telemetry seam for Section 14 signals."""

    def input_requested(self, *, schema_version: DiagnosticReportSchemaVersion) -> None:
        """Record a diagnostic turn ending in an input request."""

    def report_completed(self, *, outcome: DiagnosticReportTelemetryOutcome) -> None:
        """Record a diagnostic turn ending in a report."""

    def report_validation_failed(
        self,
        *,
        stage: str,
        attempt_number: int,
        schema_version: DiagnosticReportSchemaVersion,
    ) -> None:
        """Record a failed report or completion-basis validation attempt."""

    def turn_completed(
        self,
        *,
        snapshot: DiagnosticTurnTelemetrySnapshot,
        dimensions: DiagnosticMetricDimensions,
    ) -> None:
        """Record the final, bounded metrics for one execution attempt."""


class LoggingDiagnosticCompletionTelemetry:
    """Emit the approved dimensions using stable structured event names."""

    def __init__(self, *, meter_provider: object | None = None) -> None:
        # A private provider injection keeps the protocol's recording seam
        # deterministic without replacing the process-global ADK meter.
        self._instruments = (
            _PROCESS_INSTRUMENTS
            if meter_provider is None
            else _create_instruments(
                cast(MeterProvider, meter_provider).get_meter(__name__)
            )
        )

    def input_requested(self, *, schema_version: DiagnosticReportSchemaVersion) -> None:
        logger.info("diagnostic_turn_input_requested", schema_version=schema_version)

    def report_completed(self, *, outcome: DiagnosticReportTelemetryOutcome) -> None:
        logger.info(
            "diagnostic_report_completed",
            schema_version=outcome.schema_version,
            observed_finding_count=outcome.observed_finding_count,
            contributing_factor_count=outcome.contributing_factor_count,
            alternate_hypothesis_count=outcome.alternate_hypothesis_count,
            completion_reason=outcome.completion_reason,
        )

    def report_validation_failed(
        self,
        *,
        stage: str,
        attempt_number: int,
        schema_version: DiagnosticReportSchemaVersion,
    ) -> None:
        safe_stage = _allowed(stage, _VALIDATION_STAGES, "validation_stage")
        logger.info(
            "diagnostic_report_validation_failed",
            validation_stage=safe_stage,
            error_code="report_validation_failed",
            attempt_number=max(1, attempt_number),
            report_schema_version=schema_version,
        )
        trace.get_current_span().add_event(
            "diagnostic.report_validation_failed",
            {
                "validation_stage": safe_stage,
                "error_code": "report_validation_failed",
                "attempt_number": max(1, attempt_number),
                "report_schema_version": schema_version,
            },
        )
        try:
            self._instruments.validation_failure.add(
                1,
                {
                    "validation_stage": safe_stage,
                    "report_schema_version": schema_version,
                },
            )
        except Exception:
            return

    def turn_completed(
        self,
        *,
        snapshot: DiagnosticTurnTelemetrySnapshot,
        dimensions: DiagnosticMetricDimensions,
    ) -> None:
        """Record only final-state measurements; telemetry failures are inert."""

        _validate_metric_snapshot(snapshot)
        try:
            _record_turn_metrics(self._instruments, snapshot, dimensions)
        except Exception:
            # Meter/exporter failures are strictly observational.
            return


def default_diagnostic_completion_telemetry() -> DiagnosticCompletionTelemetry:
    """Build the default privacy-safe rollout telemetry adapter."""

    return LoggingDiagnosticCompletionTelemetry()


def _record_turn_metrics(
    instruments: _MetricInstruments,
    snapshot: DiagnosticTurnTelemetrySnapshot,
    dimensions: DiagnosticMetricDimensions,
) -> None:
    outcome = _allowed(snapshot.outcome, _OUTCOMES, "outcome")
    terminal_status = snapshot.terminal_status.value
    if terminal_status == "unknown":
        if outcome != "terminal_error":
            raise ValueError("terminal_status=unknown requires terminal_error")
    else:
        _allowed(terminal_status, _TERMINAL_STATUSES, "terminal_status")
    common = {
        "provider": dimensions.provider,
        "model": dimensions.model,
        "prompt_version": dimensions.prompt_version,
        "report_schema_version": dimensions.report_schema_version,
        "image_analysis_mode": dimensions.image_analysis_mode,
    }
    instruments.turn_count.add(
        1, {"outcome": outcome, "terminal_status": terminal_status, **common}
    )
    duration_attributes = {
        key: common[key]
        for key in ("provider", "model", "prompt_version", "image_analysis_mode")
    }
    instruments.turn_duration.record(
        snapshot.duration_ms / 1000, {"outcome": outcome, **duration_attributes}
    )
    if snapshot.time_to_first_output_ms is not None:
        instruments.first_output.record(
            snapshot.time_to_first_output_ms / 1000, duration_attributes
        )
    if snapshot.input_request is not None:
        instruments.input_request.add(
            1,
            {
                "request_type": _allowed(
                    snapshot.input_request.request_type, _REQUEST_TYPES, "request_type"
                ),
                "required": snapshot.input_request.required,
                "report_schema_version": dimensions.report_schema_version,
            },
        )
    if snapshot.error_code is not None:
        instruments.runner_error.add(
            1,
            {
                "error_code": _allowed(snapshot.error_code, _ERROR_CODES, "error_code"),
                "retryable": bool(snapshot.error_retryable),
                "provider": dimensions.provider,
                "model": dimensions.model,
            },
        )
    if snapshot.safety.escalated:
        state = _allowed(
            snapshot.safety.safety_state or "unknown",
            _SAFETY_STATES,
            "safety_state",
        )
        for severity, flag_code in zip(
            snapshot.safety.severities,
            snapshot.safety.flag_codes,
            strict=True,
        ):
            instruments.safety_escalation.add(
                1,
                {
                    "severity": _allowed(severity, _SAFETY_SEVERITIES, "severity"),
                    "safety_state": state,
                    "flag_code": _allowed(flag_code, _SAFETY_CODES, "flag_code"),
                },
            )
    if snapshot.report.completed:
        reason = _allowed(
            snapshot.report.completion_reason, _COMPLETION_REASONS, "completion_reason"
        )
        report_attributes = {
            "completion_reason": reason,
            "report_schema_version": dimensions.report_schema_version,
            "provider": dimensions.provider,
            "model": dimensions.model,
            "prompt_version": dimensions.prompt_version,
        }
        instruments.report.add(1, report_attributes)
        for item_type, count in (
            ("observed_finding", snapshot.report.observed_finding_count),
            ("contributing_factor", snapshot.report.contributing_factor_count),
            ("alternate_hypothesis", snapshot.report.alternate_hypothesis_count),
        ):
            instruments.report_item_count.record(
                max(0, count or 0),
                {
                    "item_type": item_type,
                    "completion_reason": reason,
                    "report_schema_version": dimensions.report_schema_version,
                },
            )


def _validate_metric_snapshot(snapshot: DiagnosticTurnTelemetrySnapshot) -> None:
    """Reject contract regressions before an exporter can observe a partial turn."""

    outcome = _allowed(snapshot.outcome, _OUTCOMES, "outcome")
    terminal_status = snapshot.terminal_status.value
    if terminal_status == "unknown":
        if outcome != "terminal_error":
            raise ValueError("terminal_status=unknown requires terminal_error")
    else:
        _allowed(terminal_status, _TERMINAL_STATUSES, "terminal_status")
    if snapshot.input_request is not None:
        _allowed(snapshot.input_request.request_type, _REQUEST_TYPES, "request_type")
    for stage in snapshot.validation.stages:
        _allowed(stage, _VALIDATION_STAGES, "validation_stage")
    if snapshot.error_code is not None:
        _allowed(snapshot.error_code, _ERROR_CODES, "error_code")
    if snapshot.report.completed:
        _allowed(
            snapshot.report.completion_reason,
            _COMPLETION_REASONS,
            "completion_reason",
        )
    if snapshot.safety.escalated:
        _allowed(
            snapshot.safety.safety_state or "unknown", _SAFETY_STATES, "safety_state"
        )
        for severity, flag_code in zip(
            snapshot.safety.severities,
            snapshot.safety.flag_codes,
            strict=True,
        ):
            _allowed(severity, _SAFETY_SEVERITIES, "severity")
            _allowed(flag_code, _SAFETY_CODES, "flag_code")


def _allowed(value: str | None, allowed: frozenset[str], name: str) -> str:
    if value not in allowed:
        raise ValueError(f"unexpected bounded {name}: {value!r}")
    return value


class ReportValidationAttemptTracker:
    """Private per-run counter shared by report tool calls in one ADK turn."""

    def __init__(
        self,
        telemetry: DiagnosticCompletionTelemetry,
        *,
        on_failure: Callable[[str], None] | None = None,
    ) -> None:
        self._telemetry = telemetry
        self._on_failure = on_failure
        self._attempt_number = 0

    def record(
        self, *, stage: str, schema_version: DiagnosticReportSchemaVersion
    ) -> int:
        self._attempt_number += 1
        self._telemetry.report_validation_failed(
            stage=stage,
            attempt_number=self._attempt_number,
            schema_version=schema_version,
        )
        if self._on_failure is not None:
            self._on_failure(stage)
        return self._attempt_number


_report_validation_attempt_tracker: ContextVar[
    ReportValidationAttemptTracker | None
] = ContextVar("report_validation_attempt_tracker", default=None)


@contextmanager
def report_validation_attempt_scope(
    telemetry: DiagnosticCompletionTelemetry,
    *,
    on_failure: Callable[[str], None] | None = None,
) -> Iterator[ReportValidationAttemptTracker]:
    """Bind one private attempt counter for an orchestrated runner invocation."""

    tracker = ReportValidationAttemptTracker(telemetry, on_failure=on_failure)
    token = _report_validation_attempt_tracker.set(tracker)
    try:
        yield tracker
    finally:
        _report_validation_attempt_tracker.reset(token)


def record_report_validation_failure(
    *,
    telemetry: DiagnosticCompletionTelemetry,
    stage: str,
    schema_version: DiagnosticReportSchemaVersion,
) -> int:
    """Emit exactly one bounded signal for a rejected save attempt."""

    tracker = _report_validation_attempt_tracker.get()
    if tracker is not None:
        return tracker.record(stage=stage, schema_version=schema_version)
    telemetry.report_validation_failed(
        stage=stage, attempt_number=1, schema_version=schema_version
    )
    return 1
