"""Bounded, privacy-safe observability for visual observation extraction."""

from __future__ import annotations

import logging
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Protocol

logger = logging.getLogger(__name__)

_EVENTS = frozenset(
    {
        "observation_extraction_started",
        "observation_extraction_completed",
        "observation_extraction_failed",
        "observation_extraction_retried",
    }
)
_TEXT_FIELDS = frozenset(
    {
        "provider",
        "model",
        "extractor_version",
        "prompt_version",
        "preprocessing_version",
        "schema_version",
        "mode",
        "outcome",
        "failure_class",
        "validation_kind",
    }
)
_NUMBER_FIELDS = frozenset(
    {
        "attempt_count",
        "observation_count",
        "assessability_usable_count",
        "assessability_limited_count",
        "assessability_unusable_count",
        "provider_latency_ms",
        "total_turn_latency_ms",
        "input_tokens",
        "output_tokens",
        "total_tokens",
        "cost_microunits",
    }
)
_SAFE_TEXT = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")
_SAFE_VALUES = {
    "mode": frozenset({"shadow", "enabled"}),
    "outcome": frozenset({"started", "completed", "failed", "retried"}),
    "failure_class": frozenset({"provider", "validation", "privacy", "artifact"}),
    "validation_kind": frozenset({"schema", "artifact_reference", "privacy"}),
}


class ObservationExtractionTelemetry(Protocol):
    def event(
        self, name: str, *, fields: Mapping[str, object] | None = None
    ) -> None: ...

    def metric(
        self,
        name: str,
        *,
        value: int | float = 1,
        dimensions: Mapping[str, object] | None = None,
    ) -> None: ...


@dataclass(frozen=True, slots=True)
class ObservationExtractionTelemetryRecord:
    kind: str
    name: str
    value: int | float | None
    fields: dict[str, object]


@dataclass(slots=True)
class RecordingObservationExtractionTelemetry:
    records: list[ObservationExtractionTelemetryRecord] = field(default_factory=list)

    def event(self, name: str, *, fields: Mapping[str, object] | None = None) -> None:
        self.records.append(
            ObservationExtractionTelemetryRecord(
                "event", name, None, _safe(fields or {})
            )
        )

    def metric(
        self,
        name: str,
        *,
        value: int | float = 1,
        dimensions: Mapping[str, object] | None = None,
    ) -> None:
        self.records.append(
            ObservationExtractionTelemetryRecord(
                "metric", name, value, _safe(dimensions or {})
            )
        )


class LoggingObservationExtractionTelemetry:
    def event(self, name: str, *, fields: Mapping[str, object] | None = None) -> None:
        if name in _EVENTS:
            logger.info(name, extra={"observation_extraction": _safe(fields or {})})

    def metric(
        self,
        name: str,
        *,
        value: int | float = 1,
        dimensions: Mapping[str, object] | None = None,
    ) -> None:
        logger.info(
            "observation_extraction_metric",
            extra={
                "observation_extraction_metric_name": name,
                "observation_extraction_metric_value": value,
                "observation_extraction_metric_dimensions": _safe(dimensions or {}),
            },
        )


_DEFAULT = LoggingObservationExtractionTelemetry()


def default_observation_extraction_telemetry() -> ObservationExtractionTelemetry:
    return _DEFAULT


def _safe(fields: Mapping[str, object]) -> dict[str, object]:
    safe: dict[str, object] = {}
    for key, value in fields.items():
        if key in _NUMBER_FIELDS and isinstance(value, (int, float)) and value >= 0:
            safe[key] = value
        elif key in _TEXT_FIELDS and isinstance(value, str):
            allowed = _SAFE_VALUES.get(key)
            if (allowed is not None and value in allowed) or (
                allowed is None and _SAFE_TEXT.fullmatch(value)
            ):
                safe[key] = value
    return safe
