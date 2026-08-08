"""Process telemetry runtime behavior tests."""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any

import pytest

from bike_doc_api.core import telemetry
from bike_doc_api.core.config import Settings


@dataclass
class FakeProvider:
    resource: Any | None = None
    processors: list[object] = field(default_factory=list)
    readers: list[object] = field(default_factory=list)
    shutdown_calls: int = 0

    def add_span_processor(self, processor: object) -> None:
        self.processors.append(processor)

    def force_flush(self, *, timeout_millis: int) -> bool:
        return True

    def shutdown(self) -> None:
        self.shutdown_calls += 1


class FakeReader:
    def __init__(self, exporter: object) -> None:
        self.exporter = exporter


def test_signal_endpoints_are_derived_from_one_base_endpoint() -> None:
    assert (
        telemetry.derive_otlp_signal_endpoint("https://collector.example/", "traces")
        == "https://collector.example/v1/traces"
    )
    assert (
        telemetry.derive_otlp_signal_endpoint(
            "https://collector.example/otlp", "metrics"
        )
        == "https://collector.example/otlp/v1/metrics"
    )


def test_disabled_runtime_does_not_install_global_providers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        telemetry.trace,
        "set_tracer_provider",
        lambda _provider: pytest.fail("disabled telemetry installed a tracer provider"),
    )
    monkeypatch.setattr(
        telemetry.metrics,
        "set_meter_provider",
        lambda _provider: pytest.fail("disabled telemetry installed a meter provider"),
    )

    runtime = telemetry.initialize_telemetry(Settings())

    assert isinstance(runtime, telemetry.DisabledTelemetryRuntime)


def test_enabled_runtime_builds_one_shared_provider_pair_with_approved_resource(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    telemetry.reset_telemetry_runtime_for_tests()
    providers: list[FakeProvider] = []
    endpoints: list[str] = []
    installed: list[object] = []

    def tracer_provider(*, resource: object) -> FakeProvider:
        provider = FakeProvider(resource=resource)
        providers.append(provider)
        return provider

    def meter_provider(
        *, resource: object, metric_readers: list[object]
    ) -> FakeProvider:
        provider = FakeProvider(resource=resource, readers=metric_readers)
        providers.append(provider)
        return provider

    monkeypatch.setattr(telemetry, "TracerProvider", tracer_provider)
    monkeypatch.setattr(telemetry, "MeterProvider", meter_provider)
    monkeypatch.setattr(telemetry, "BatchSpanProcessor", lambda exporter: exporter)
    monkeypatch.setattr(telemetry, "PeriodicExportingMetricReader", FakeReader)
    factories = telemetry.TelemetryFactories(
        span_exporter=lambda endpoint: endpoints.append(endpoint) or object(),
        metric_exporter=lambda endpoint: endpoints.append(endpoint) or object(),
        install_providers=lambda tracer, meter: installed.extend([tracer, meter]),
    )

    runtime = telemetry.initialize_telemetry(
        Settings(
            environment="test",
            telemetry_exporter="otlp",
            telemetry_otlp_endpoint="https://collector.example/base",
            telemetry_service_name="bike-doc-test",
        ),
        factories=factories,
    )

    assert endpoints == [
        "https://collector.example/base/v1/traces",
        "https://collector.example/base/v1/metrics",
    ]
    assert installed == providers
    assert providers[0].resource.attributes == {
        "service.name": "bike-doc-test",
        "service.version": "0.1.0",
        "deployment.environment.name": "test",
    }
    assert providers[1].resource.attributes == providers[0].resource.attributes
    assert isinstance(runtime, telemetry.OtlpTelemetryRuntime)
    telemetry.reset_telemetry_runtime_for_tests()


def test_repeated_enabled_initialization_reuses_the_runtime_handle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    telemetry.reset_telemetry_runtime_for_tests()
    exports: list[str] = []
    monkeypatch.setattr(
        telemetry,
        "TracerProvider",
        lambda *, resource: FakeProvider(resource=resource),
    )
    monkeypatch.setattr(telemetry, "BatchSpanProcessor", lambda exporter: exporter)
    monkeypatch.setattr(
        telemetry,
        "MeterProvider",
        lambda *, resource, metric_readers: FakeProvider(
            resource=resource, readers=metric_readers
        ),
    )
    monkeypatch.setattr(telemetry, "PeriodicExportingMetricReader", FakeReader)
    factories = telemetry.TelemetryFactories(
        span_exporter=lambda endpoint: exports.append(endpoint) or object(),
        metric_exporter=lambda endpoint: exports.append(endpoint) or object(),
        install_providers=lambda _tracer, _meter: None,
    )
    settings = Settings(
        telemetry_exporter="otlp",
        telemetry_otlp_endpoint="https://collector.example",
    )

    first = telemetry.initialize_telemetry(settings, factories=factories)
    second = telemetry.initialize_telemetry(settings, factories=factories)

    assert first is second
    assert exports == [
        "https://collector.example/v1/traces",
        "https://collector.example/v1/metrics",
    ]
    telemetry.reset_telemetry_runtime_for_tests()


def test_enabled_runtime_initialization_failure_propagates() -> None:
    telemetry.reset_telemetry_runtime_for_tests()
    factories = telemetry.TelemetryFactories(
        span_exporter=lambda _endpoint: (_ for _ in ()).throw(
            RuntimeError("bad setup")
        ),
    )

    with pytest.raises(RuntimeError, match="bad setup"):
        telemetry.initialize_telemetry(
            Settings(
                telemetry_exporter="otlp",
                telemetry_otlp_endpoint="https://collector.example",
            ),
            factories=factories,
        )


@pytest.mark.asyncio
async def test_shutdown_is_best_effort_and_bounded_during_exporter_outage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class SlowProvider(FakeProvider):
        def force_flush(self, *, timeout_millis: int) -> bool:
            time.sleep(0.1)
            return False

    monkeypatch.setattr(telemetry, "_SHUTDOWN_TIMEOUT_SECONDS", 0.001)
    runtime = telemetry.OtlpTelemetryRuntime(SlowProvider(), SlowProvider())

    await asyncio.wait_for(runtime.shutdown(), timeout=0.05)
