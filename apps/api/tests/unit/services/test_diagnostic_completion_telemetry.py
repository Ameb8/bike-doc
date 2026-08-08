"""Diagnostic-completion rollout telemetry tests."""

from __future__ import annotations

from typing import Any

from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader

from bike_doc_api.adk.turn_telemetry_state import DiagnosticTurnTelemetryState
from bike_doc_api.services.diagnostic_completion_telemetry import (
    DiagnosticMetricDimensions,
    DiagnosticReportTelemetryOutcome,
    LoggingDiagnosticCompletionTelemetry,
)


def test_completed_report_telemetry_contains_only_documented_counts(
    monkeypatch: Any,
) -> None:
    calls: list[tuple[str, dict[str, Any]]] = []

    def capture(event_name: str, **fields: Any) -> None:
        calls.append((event_name, fields))

    monkeypatch.setattr(
        "bike_doc_api.services.diagnostic_completion_telemetry.logger.info",
        capture,
    )

    LoggingDiagnosticCompletionTelemetry().report_completed(
        outcome=DiagnosticReportTelemetryOutcome(
            schema_version="diagnostic_report.v2",
            observed_finding_count=2,
            contributing_factor_count=1,
            alternate_hypothesis_count=3,
            completion_reason="diagnosis_supported",
            same_turn_completion_after_first_finding=True,
        ),
    )

    assert calls == [
        (
            "diagnostic_report_completed",
            {
                "schema_version": "diagnostic_report.v2",
                "observed_finding_count": 2,
                "contributing_factor_count": 1,
                "alternate_hypothesis_count": 3,
                "completion_reason": "diagnosis_supported",
                "same_turn_completion_after_first_finding": True,
            },
        )
    ]


def test_validation_telemetry_does_not_accept_report_content(monkeypatch: Any) -> None:
    calls: list[tuple[str, dict[str, Any]]] = []

    def capture(event_name: str, **fields: Any) -> None:
        calls.append((event_name, fields))

    monkeypatch.setattr(
        "bike_doc_api.services.diagnostic_completion_telemetry.logger.info",
        capture,
    )

    LoggingDiagnosticCompletionTelemetry().report_validation_failed(
        schema_version="diagnostic_report.v1",
    )

    assert calls == [
        (
            "diagnostic_report_validation_failed",
            {"schema_version": "diagnostic_report.v1"},
        )
    ]


def test_final_turn_metrics_use_bounded_attributes_and_seconds() -> None:
    reader = InMemoryMetricReader()
    telemetry = LoggingDiagnosticCompletionTelemetry(
        meter_provider=MeterProvider(metric_readers=[reader])
    )
    clock = iter((10.0, 10.25, 10.75))
    state = DiagnosticTurnTelemetryState(clock=lambda: next(clock))
    state.note_assistant_delta()
    state.note_input_requested(request_type="photo", required=True)

    telemetry.turn_completed(
        snapshot=state.finalize(),
        dimensions=DiagnosticMetricDimensions(
            provider="google",
            model="gemini-2.5-flash",
            prompt_version="diagnostic-observation.v1",
            report_schema_version="diagnostic_report.v2",
            image_analysis_mode="enabled",
        ),
    )

    metrics = reader.get_metrics_data()
    assert metrics is not None
    points = [
        point
        for resource_metric in metrics.resource_metrics
        for scope_metric in resource_metric.scope_metrics
        for metric in scope_metric.metrics
        for point in metric.data.data_points
    ]
    assert any(point.attributes.get("outcome") == "input_requested" for point in points)
    assert any(getattr(point, "sum", None) == 0.75 for point in points)
