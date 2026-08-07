"""Privacy-safe, stable telemetry for diagnostic report rollout monitoring.

Only bounded scalar dimensions are accepted.  Report content, agent prompts,
completion-basis rationale, and model/provider traces are intentionally not
representable at this boundary.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Literal, Protocol

logger = logging.getLogger(__name__)

DiagnosticReportSchemaVersion = Literal["diagnostic_report.v1", "diagnostic_report.v2"]


@dataclass(frozen=True, slots=True)
class DiagnosticReportTelemetryOutcome:
    """Bounded dimensions emitted once for a completed diagnostic report."""

    schema_version: DiagnosticReportSchemaVersion
    observed_finding_count: int
    contributing_factor_count: int
    alternate_hypothesis_count: int
    completion_reason: str | None
    same_turn_completion_after_first_finding: bool


class DiagnosticCompletionTelemetry(Protocol):
    """Stable operational telemetry seam for Section 14 signals."""

    def input_requested(self, *, schema_version: DiagnosticReportSchemaVersion) -> None:
        """Record a diagnostic turn ending in an input request."""

    def report_completed(self, *, outcome: DiagnosticReportTelemetryOutcome) -> None:
        """Record a diagnostic turn ending in a report."""

    def report_validation_failed(
        self, *, schema_version: DiagnosticReportSchemaVersion
    ) -> None:
        """Record a failed report or completion-basis validation attempt."""


class LoggingDiagnosticCompletionTelemetry:
    """Emit the approved dimensions using stable structured event names."""

    def input_requested(self, *, schema_version: DiagnosticReportSchemaVersion) -> None:
        logger.info(
            "diagnostic_turn_input_requested",
            extra={"diagnostic_completion": {"schema_version": schema_version}},
        )

    def report_completed(self, *, outcome: DiagnosticReportTelemetryOutcome) -> None:
        logger.info(
            "diagnostic_report_completed",
            extra={
                "diagnostic_completion": {
                    "schema_version": outcome.schema_version,
                    "observed_finding_count": outcome.observed_finding_count,
                    "contributing_factor_count": outcome.contributing_factor_count,
                    "alternate_hypothesis_count": outcome.alternate_hypothesis_count,
                    "completion_reason": outcome.completion_reason,
                    "same_turn_completion_after_first_finding": (
                        outcome.same_turn_completion_after_first_finding
                    ),
                }
            },
        )

    def report_validation_failed(
        self, *, schema_version: DiagnosticReportSchemaVersion
    ) -> None:
        logger.info(
            "diagnostic_report_validation_failed",
            extra={"diagnostic_completion": {"schema_version": schema_version}},
        )


def default_diagnostic_completion_telemetry() -> DiagnosticCompletionTelemetry:
    """Build the default privacy-safe rollout telemetry adapter."""

    return LoggingDiagnosticCompletionTelemetry()
