"""Queue observation contract. No arbitrary attributes, content or exception text."""

from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from math import isfinite

import structlog
from opentelemetry import metrics

from bike_doc_api.core.queue_contract import CONTRACT, DIMENSIONS, FIELDS


@dataclass(frozen=True)
class Observation:
    metric: str
    value: float
    attributes: dict[str, str]


class QueueTelemetry:
    """Failure-isolated observation hook, injectable without an exporter or SDK."""

    def __init__(self, sink: Callable[[Observation], None] | None = None) -> None:
        self._sink = sink or self._export
        self._instruments: dict[str, Callable[[float, dict[str, str]], None]] = {}

    def observe(self, metric: str, value: float = 1, /, **attributes: str) -> None:
        # Invalid observations are dropped wholesale, including from logs.
        if metric not in CONTRACT or frozenset(attributes) != FIELDS[metric]:
            return
        if any(value not in DIMENSIONS[key] for key, value in attributes.items()):
            return
        if not isfinite(value) or (value < 0 and metric != "active"):
            return
        with suppress(Exception):
            self._sink(Observation(metric, value, dict(attributes)))

    def _export(self, observation: Observation) -> None:
        metric = observation.metric
        if metric not in self._instruments:
            meter = metrics.get_meter("bike_doc.queue")
            instrument_type, unit = CONTRACT[metric]
            name = f"bike_doc.queue.{metric}"
            if instrument_type == "counter":
                self._instruments[metric] = meter.create_counter(name, unit=unit).add
            elif instrument_type == "updown":
                self._instruments[metric] = meter.create_up_down_counter(
                    name, unit=unit
                ).add
            elif instrument_type == "histogram":
                self._instruments[metric] = meter.create_histogram(
                    name, unit=unit
                ).record
            else:
                self._instruments[metric] = meter.create_gauge(name, unit=unit).set
        self._instruments[metric](observation.value, observation.attributes)
        if CONTRACT[metric][0] == "counter":
            structlog.get_logger(__name__).info(
                "queue_observation", signal=metric, **observation.attributes
            )


class RecordingQueueTelemetry(QueueTelemetry):
    def __init__(self) -> None:
        self.observations: list[Observation] = []
        super().__init__(self.observations.append)


queue_telemetry = QueueTelemetry()
