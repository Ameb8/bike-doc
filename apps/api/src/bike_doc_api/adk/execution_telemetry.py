"""Bounded diagnostic attempt telemetry shared by both process hosts."""

import structlog
from opentelemetry import trace
from opentelemetry.trace import Status, StatusCode

from bike_doc_api.adk.turn_telemetry_state import DiagnosticTurnTelemetrySnapshot
from bike_doc_api.services.diagnostic_completion_telemetry import (
    DiagnosticMetricDimensions,
    default_diagnostic_completion_telemetry,
)

logger = structlog.get_logger(__name__)
_TELEMETRY_SCHEMA_VERSION = "diagnostic_telemetry.v1"


def _apply_snapshot(
    span: trace.Span, snapshot: DiagnosticTurnTelemetrySnapshot
) -> None:
    """Apply only bounded immutable orchestration facts to the root span."""
    span.set_attributes(
        {
            "bike_doc.turn.outcome": snapshot.outcome,
            "bike_doc.turn.terminal_status": snapshot.terminal_status.value,
            "bike_doc.turn.duration_ms": snapshot.duration_ms,
            "bike_doc.output.delta_count": snapshot.assistant_delta_count,
            "bike_doc.output.message_count": snapshot.assistant_message_count,
            "bike_doc.terminal_action_count": snapshot.terminal_action_count,
            "bike_doc.safety.escalated": snapshot.safety.escalated,
        }
    )
    if snapshot.time_to_first_output_ms is not None:
        span.set_attribute(
            "bike_doc.turn.time_to_first_output_ms", snapshot.time_to_first_output_ms
        )
    if snapshot.error_code is not None:
        span.set_attributes(
            {
                "bike_doc.error.code": snapshot.error_code,
                "bike_doc.error.retryable": bool(snapshot.error_retryable),
            }
        )
        span.add_event(
            "diagnostic.recoverable_error",
            {
                "error_code": snapshot.error_code,
                "retryable": bool(snapshot.error_retryable),
            },
        )
    for action in snapshot.terminal_action_kinds:
        span.add_event("diagnostic.terminal_action", {"action_kind": action})
    for stage in snapshot.validation.stages:
        span.add_event(
            "diagnostic.report_validation_failed", {"validation_stage": stage}
        )
    if snapshot.safety.escalated:
        span.add_event(
            "diagnostic.safety_escalated",
            {
                "safety_state": snapshot.safety.safety_state or "unknown",
                "escalation_count": snapshot.safety.escalation_count,
            },
        )
    if snapshot.report.completed:
        span.set_attributes(
            {
                "bike_doc.report.completed": True,
                "bike_doc.report.observed_finding_count": (
                    snapshot.report.observed_finding_count or 0
                ),
                "bike_doc.report.contributing_factor_count": (
                    snapshot.report.contributing_factor_count or 0
                ),
                "bike_doc.report.alternate_hypothesis_count": (
                    snapshot.report.alternate_hypothesis_count or 0
                ),
            }
        )
    if snapshot.outcome in {"recoverable_error", "terminal_error"}:
        span.set_status(
            Status(StatusCode.ERROR, snapshot.error_code or snapshot.outcome)
        )


def _record_attempt_metrics(
    *, snapshot: DiagnosticTurnTelemetrySnapshot, fields: dict[str, object]
) -> None:
    """Keep metric export outside product state transitions and content boundaries."""

    default_diagnostic_completion_telemetry().turn_completed(
        snapshot=snapshot,
        dimensions=DiagnosticMetricDimensions(
            provider=str(fields.get("provider", "unknown")),
            model=str(fields.get("model", "unknown")),
            prompt_version=str(fields.get("prompt_version", "unknown")),
            report_schema_version=str(fields.get("report_schema_version", "unknown")),
            image_analysis_mode=str(fields.get("image_analysis_mode", "unknown")),
        ),
    )


def _emit_completed(
    logger: structlog.stdlib.BoundLogger,
    *,
    snapshot: DiagnosticTurnTelemetrySnapshot | None,
    fields: dict[str, object],
    terminal_status: str | None = None,
    outcome: str | None = None,
) -> None:
    """Emit the one privacy-safe terminal lifecycle log for an attempt."""
    if snapshot is None:
        outcome = outcome or "terminal_error"
        data: dict[str, object] = {
            **fields,
            "outcome": outcome,
            "terminal_status": terminal_status or "unknown",
        }
    else:
        data = {
            **fields,
            "outcome": snapshot.outcome,
            "terminal_status": snapshot.terminal_status.value,
            "duration_ms": snapshot.duration_ms,
            "artifact_count": snapshot.artifact_count,
            "current_image_count": snapshot.current_image_count,
            "current_observation_count": snapshot.current_observation_count,
            "prior_observation_count": snapshot.prior_observation_count,
            "assistant_delta_count": snapshot.assistant_delta_count,
            "assistant_message_count": snapshot.assistant_message_count,
            "terminal_action_count": snapshot.terminal_action_count,
            "safety_escalated": snapshot.safety.escalated,
            "safety_state": snapshot.safety.safety_state,
            "report_completed": snapshot.report.completed,
        }
        if snapshot.agent_run_duration_ms is not None:
            data["agent_run_duration_ms"] = snapshot.agent_run_duration_ms
        if snapshot.time_to_first_output_ms is not None:
            data["time_to_first_output_ms"] = snapshot.time_to_first_output_ms
        if snapshot.input_request is not None:
            data.update(
                input_request_type=snapshot.input_request.request_type,
                input_required=snapshot.input_request.required,
            )
        if snapshot.report.completed:
            data.update(
                completion_reason=snapshot.report.completion_reason,
                observed_finding_count=snapshot.report.observed_finding_count,
                contributing_factor_count=snapshot.report.contributing_factor_count,
                alternate_hypothesis_count=snapshot.report.alternate_hypothesis_count,
            )
        if snapshot.error_code is not None:
            data["error_code"] = snapshot.error_code
    common = {
        "event_family": "diagnostic_flow",
        "component": "background",
        "telemetry_schema_version": _TELEMETRY_SCHEMA_VERSION,
    }
    if outcome in {"terminal_error"}:
        _safe_log("error", "diagnostic_turn_completed", **common, **data)
    elif outcome in {"recoverable_error", "no_terminal_action"} or (
        snapshot is not None
        and snapshot.outcome in {"recoverable_error", "no_terminal_action"}
    ):
        _safe_log("warning", "diagnostic_turn_completed", **common, **data)
    else:
        _safe_log("info", "diagnostic_turn_completed", **common, **data)


def _safe_log(level: str, event: str, **fields: object) -> None:
    """Keep lifecycle logging observational when a renderer is unavailable."""
    try:
        getattr(logger, level)(event, **fields)
    except Exception:
        return
