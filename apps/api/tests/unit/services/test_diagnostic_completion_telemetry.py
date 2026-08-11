"""Diagnostic-completion rollout telemetry tests."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from opentelemetry import trace
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from bike_doc_api.adk.turn_telemetry_state import DiagnosticTurnTelemetryState
from bike_doc_api.services.diagnostic_completion_telemetry import (
    DiagnosticMetricDimensions,
    DiagnosticReportTelemetryOutcome,
    DiagnosticSessionCompletion,
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
        stage="report_schema",
        attempt_number=2,
        schema_version="diagnostic_report.v1",
    )

    assert calls == [
        (
            "diagnostic_report_validation_failed",
            {
                "validation_stage": "report_schema",
                "error_code": "report_validation_failed",
                "attempt_number": 2,
                "report_schema_version": "diagnostic_report.v1",
            },
        )
    ]


def test_validation_failure_emits_one_bounded_span_event_and_metric() -> None:
    reader = InMemoryMetricReader()
    telemetry = LoggingDiagnosticCompletionTelemetry(
        meter_provider=MeterProvider(metric_readers=[reader])
    )
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    span = provider.get_tracer(__name__).start_span("diagnostic-root")

    with trace.use_span(span, end_on_exit=True):
        telemetry.report_validation_failed(
            stage="completion_basis",
            attempt_number=3,
            schema_version="diagnostic_report.v2",
        )

    event = exporter.get_finished_spans()[0].events[0]
    assert event.name == "diagnostic.report_validation_failed"
    assert dict(event.attributes or {}) == {
        "validation_stage": "completion_basis",
        "error_code": "report_validation_failed",
        "attempt_number": 3,
        "report_schema_version": "diagnostic_report.v2",
    }
    metric = next(
        metric
        for resource in reader.get_metrics_data().resource_metrics  # type: ignore[union-attr]
        for scope in resource.scope_metrics
        for metric in scope.metrics
        if metric.name == "bike_doc.diagnostic.report_validation_failure.count"
    )
    point = metric.data.data_points[0]
    assert point.value == 1
    assert dict(point.attributes or {}) == {
        "validation_stage": "completion_basis",
        "report_schema_version": "diagnostic_report.v2",
    }


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


def test_session_completion_logs_and_records_only_bounded_dimensions(
    monkeypatch: Any,
) -> None:
    calls: list[tuple[str, dict[str, Any]]] = []
    reader = InMemoryMetricReader()
    telemetry = LoggingDiagnosticCompletionTelemetry(
        meter_provider=MeterProvider(metric_readers=[reader])
    )
    monkeypatch.setattr(
        "bike_doc_api.services.diagnostic_completion_telemetry.logger.info",
        lambda event_name, **fields: calls.append((event_name, fields)),
    )

    telemetry.session_completed(
        completion=DiagnosticSessionCompletion(
            repair_session_id="rs_private",
            diagnostic_session_id="phs_private",
            completion_reason="diagnosis_supported",
            turn_count=2,
            phase_session_created_at=datetime(2026, 1, 1, tzinfo=UTC),
            report_created_at=datetime(2026, 1, 1, 0, 2, 30, tzinfo=UTC),
            report_schema_version="diagnostic_report.v2",
        )
    )

    assert calls == [
        (
            "diagnostic_session_completed",
            {
                "repair_session_id": "rs_private",
                "diagnostic_session_id": "phs_private",
                "completion_reason": "diagnosis_supported",
                "turn_count": 2,
                "session_elapsed_ms": 150000,
                "single_turn_completion": False,
                "report_schema_version": "diagnostic_report.v2",
            },
        )
    ]
    metrics = reader.get_metrics_data()
    assert metrics is not None
    points_by_metric = {
        metric.name: metric.data.data_points[0]
        for resource_metric in metrics.resource_metrics
        for scope_metric in resource_metric.scope_metrics
        for metric in scope_metric.metrics
        if metric.name.startswith("bike_doc.diagnostic.session.")
    }
    assert points_by_metric["bike_doc.diagnostic.session.turns_to_completion"].sum == 2
    assert points_by_metric["bike_doc.diagnostic.session.elapsed"].sum == 150.0
    for point in points_by_metric.values():
        assert dict(point.attributes or {}) == {
            "completion_reason": "diagnosis_supported",
            "report_schema_version": "diagnostic_report.v2",
        }


@pytest.mark.parametrize(
    ("turn_count", "single_turn_completion"),
    [(1, True), (2, False), (20, False)],
)
def test_session_completion_single_turn_means_exactly_one_durable_turn(
    monkeypatch: Any,
    turn_count: int,
    single_turn_completion: bool,
) -> None:
    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(
        "bike_doc_api.services.diagnostic_completion_telemetry.logger.info",
        lambda _event_name, **fields: calls.append(fields),
    )

    LoggingDiagnosticCompletionTelemetry().session_completed(
        completion=DiagnosticSessionCompletion(
            repair_session_id="rs_private",
            diagnostic_session_id="phs_private",
            completion_reason="diagnosis_supported",
            turn_count=turn_count,
            phase_session_created_at=datetime(2026, 1, 1, tzinfo=UTC),
            report_created_at=datetime(2026, 1, 1, 0, 0, 1, tzinfo=UTC),
            report_schema_version="diagnostic_report.v2",
        )
    )

    assert calls[0]["single_turn_completion"] is single_turn_completion


def test_session_completion_logging_failure_is_inert(monkeypatch: Any) -> None:
    def fail(*_: Any, **__: Any) -> None:
        raise RuntimeError("export unavailable")

    monkeypatch.setattr(
        "bike_doc_api.services.diagnostic_completion_telemetry.logger.info", fail
    )
    LoggingDiagnosticCompletionTelemetry().session_completed(
        completion=DiagnosticSessionCompletion(
            repair_session_id="rs_private",
            diagnostic_session_id="phs_private",
            completion_reason="diagnosis_supported",
            turn_count=1,
            phase_session_created_at=datetime(2026, 1, 1, tzinfo=UTC),
            report_created_at=datetime(2026, 1, 1, 0, 0, 1, tzinfo=UTC),
            report_schema_version="diagnostic_report.v2",
        )
    )
