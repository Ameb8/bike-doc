"""Diagnostic-completion rollout telemetry tests."""

from __future__ import annotations

from typing import Any

from bike_doc_api.services.diagnostic_completion_telemetry import (
    DiagnosticReportTelemetryOutcome,
    LoggingDiagnosticCompletionTelemetry,
)


def test_completed_report_telemetry_contains_only_documented_counts(
    monkeypatch: Any,
) -> None:
    calls: list[tuple[str, dict[str, Any]]] = []

    def capture(event_name: str, *, extra: dict[str, Any]) -> None:
        calls.append((event_name, extra))

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
                "diagnostic_completion": {
                    "schema_version": "diagnostic_report.v2",
                    "observed_finding_count": 2,
                    "contributing_factor_count": 1,
                    "alternate_hypothesis_count": 3,
                    "completion_reason": "diagnosis_supported",
                    "same_turn_completion_after_first_finding": True,
                }
            },
        )
    ]


def test_validation_telemetry_does_not_accept_report_content(monkeypatch: Any) -> None:
    calls: list[tuple[str, dict[str, Any]]] = []

    def capture(event_name: str, *, extra: dict[str, Any]) -> None:
        calls.append((event_name, extra))

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
            {"diagnostic_completion": {"schema_version": "diagnostic_report.v1"}},
        )
    ]
