"""Privacy-safe operational telemetry for profile-inference work."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Protocol

logger = logging.getLogger(__name__)

SAFE_FAILURE_CLASSES = frozenset({"provider", "artifact", "validation", "transaction"})
SAFE_OUTCOMES = frozenset(
    {
        "started",
        "completed",
        "abstained",
        "retryable_failure",
        "terminal_failure",
        "retried",
        "exhausted",
    },
)
SAFE_DISPOSITIONS = frozenset(
    {"pending", "applied", "supporting", "conflict", "superseded", "rejected"},
)


class ProfileInferenceTelemetry(Protocol):
    """Small sink used by services and deterministic tests."""

    def event(self, name: str, *, fields: Mapping[str, object] | None = None) -> None:
        """Record one bounded-cardinality structured event."""

    def metric(
        self,
        name: str,
        *,
        value: int | float = 1,
        dimensions: Mapping[str, object] | None = None,
    ) -> None:
        """Record one bounded-cardinality metric sample."""


@dataclass(frozen=True, slots=True)
class TelemetryRecord:
    """A deterministic representation of one safe telemetry item."""

    kind: str
    name: str
    value: int | float | None
    fields: dict[str, object]


@dataclass(slots=True)
class RecordingProfileInferenceTelemetry:
    """In-memory sink for backend tests and local operational inspection."""

    records: list[TelemetryRecord] = field(default_factory=list)

    def event(self, name: str, *, fields: Mapping[str, object] | None = None) -> None:
        self.records.append(
            TelemetryRecord(
                kind="event",
                name=name,
                value=None,
                fields=_safe_fields(fields or {}),
            ),
        )

    def metric(
        self,
        name: str,
        *,
        value: int | float = 1,
        dimensions: Mapping[str, object] | None = None,
    ) -> None:
        self.records.append(
            TelemetryRecord(
                kind="metric",
                name=name,
                value=value,
                fields=_safe_fields(dimensions or {}),
            ),
        )


class LoggingProfileInferenceTelemetry:
    """Emit the same safe records through the application's log pipeline."""

    def event(self, name: str, *, fields: Mapping[str, object] | None = None) -> None:
        logger.info(
            name,
            extra={"profile_inference_fields": _safe_fields(fields or {})},
        )

    def metric(
        self,
        name: str,
        *,
        value: int | float = 1,
        dimensions: Mapping[str, object] | None = None,
    ) -> None:
        logger.info(
            "profile_inference_metric",
            extra={
                "profile_inference_metric_name": name,
                "profile_inference_metric_value": value,
                "profile_inference_metric_dimensions": _safe_fields(dimensions or {}),
            },
        )


_DEFAULT_TELEMETRY = LoggingProfileInferenceTelemetry()


def default_profile_inference_telemetry() -> ProfileInferenceTelemetry:
    """Return the process-safe default sink."""

    return _DEFAULT_TELEMETRY


def _safe_fields(fields: Mapping[str, object]) -> dict[str, object]:
    """Keep telemetry to approved, compact scalar dimensions only."""

    safe: dict[str, object] = {}
    for key, value in fields.items():
        if key in {"failure_class", "retry_classification"}:
            if value in SAFE_FAILURE_CLASSES:
                safe[key] = value
        elif key == "outcome":
            if value in SAFE_OUTCOMES:
                safe[key] = value
        elif key == "disposition":
            if value in SAFE_DISPOSITIONS:
                safe[key] = value
        elif (
            (
                key
                in {
                    "field_path",
                    "source_type",
                    "source_transition",
                    "policy_mode",
                    "schema_version",
                    "extractor_version",
                    "provider",
                    "failure_code",
                    "reason",
                }
                and isinstance(value, str)
            )
            or (
                key
                in {
                    "attempt",
                    "attempt_count",
                    "retry_count",
                    "claim_count",
                }
                and isinstance(value, int)
            )
            or (
                key
                in {
                    "duration_ms",
                    "latency_ms",
                    "input_tokens",
                    "output_tokens",
                    "total_tokens",
                    "cost_usd",
                }
                and isinstance(value, (int, float))
            )
        ):
            safe[key] = value
    return safe
