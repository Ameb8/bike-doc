"""Process-owned OpenTelemetry runtime setup and lifecycle management."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from threading import Lock
from typing import Protocol, cast

from opentelemetry import metrics, trace
from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

from bike_doc_api import __version__
from bike_doc_api.core.config import Settings

_SHUTDOWN_TIMEOUT_SECONDS = 5.0


class TelemetryRuntime(Protocol):
    """A started process telemetry runtime with bounded shutdown."""

    async def shutdown(self) -> None:
        """Best-effort flush and shutdown of owned telemetry resources."""


@dataclass(frozen=True)
class TelemetryFactories:
    """Construction seams for isolated runtime tests."""

    span_exporter: Callable[[str], object] = OTLPSpanExporter
    metric_exporter: Callable[[str], object] = OTLPMetricExporter
    install_providers: Callable[[object, object], None] | None = None


@dataclass
class DisabledTelemetryRuntime:
    """No-op runtime that deliberately leaves process-global providers alone."""

    async def shutdown(self) -> None:
        return None


@dataclass
class OtlpTelemetryRuntime:
    """The SDK providers and their background exporters for one process."""

    tracer_provider: object
    meter_provider: object
    _shutdown_started: bool = False

    async def shutdown(self) -> None:
        """Flush asynchronously, allowing at most five seconds during lifespan exit."""

        if self._shutdown_started:
            return
        self._shutdown_started = True
        try:
            await asyncio.wait_for(
                asyncio.to_thread(self._shutdown_sync),
                timeout=_SHUTDOWN_TIMEOUT_SECONDS,
            )
        except Exception:
            # SDK exporter errors are intentionally warning/drop-only and must
            # never alter application shutdown behavior.
            return

    def _shutdown_sync(self) -> None:
        timeout_millis = int(_SHUTDOWN_TIMEOUT_SECONDS * 1000)
        tracer_provider = cast(TracerProvider, self.tracer_provider)
        meter_provider = cast(MeterProvider, self.meter_provider)
        for shutdown_operation in (
            lambda: tracer_provider.force_flush(timeout_millis=timeout_millis),
            lambda: meter_provider.force_flush(timeout_millis=timeout_millis),
            tracer_provider.shutdown,
            meter_provider.shutdown,
        ):
            try:
                shutdown_operation()
            except Exception:
                continue


_runtime_lock = Lock()
_installed_runtime: OtlpTelemetryRuntime | None = None
_installed_runtime_configuration: tuple[str, str, str] | None = None


def derive_otlp_signal_endpoint(base_endpoint: str, signal: str) -> str:
    """Derive an OTLP/HTTP signal endpoint from the validated shared base URL."""

    if signal not in {"traces", "metrics"}:
        raise ValueError("signal must be traces or metrics")
    return f"{base_endpoint.rstrip('/')}/v1/{signal}"


def initialize_telemetry(
    settings: Settings,
    *,
    factories: TelemetryFactories | None = None,
) -> TelemetryRuntime:
    """Start the configured runtime, leaving globals untouched in disabled mode."""

    global _installed_runtime
    global _installed_runtime_configuration

    if settings.telemetry_exporter == "none":
        return DisabledTelemetryRuntime()

    assert settings.telemetry_otlp_endpoint is not None
    configured_factories = factories or TelemetryFactories()
    configuration = (
        settings.telemetry_otlp_endpoint,
        settings.telemetry_service_name,
        settings.environment,
    )
    with _runtime_lock:
        if _installed_runtime is not None:
            if _installed_runtime_configuration != configuration:
                raise RuntimeError(
                    "BikeDoc telemetry is already initialized with different settings"
                )
            return _installed_runtime

        resource = Resource(
            attributes={
                "service.name": settings.telemetry_service_name,
                "service.version": __version__,
                "deployment.environment.name": settings.environment,
            }
        )
        trace_exporter = configured_factories.span_exporter(
            derive_otlp_signal_endpoint(settings.telemetry_otlp_endpoint, "traces")
        )
        metric_exporter = configured_factories.metric_exporter(
            derive_otlp_signal_endpoint(settings.telemetry_otlp_endpoint, "metrics")
        )
        tracer_provider = TracerProvider(resource=resource)
        tracer_provider.add_span_processor(
            BatchSpanProcessor(cast(OTLPSpanExporter, trace_exporter))
        )
        metric_reader = PeriodicExportingMetricReader(
            cast(OTLPMetricExporter, metric_exporter)
        )
        meter_provider = MeterProvider(
            resource=resource, metric_readers=[metric_reader]
        )

        install_providers = configured_factories.install_providers or _install_providers
        try:
            install_providers(tracer_provider, meter_provider)
        except Exception:
            tracer_provider.shutdown()
            meter_provider.shutdown()
            raise
        runtime = OtlpTelemetryRuntime(tracer_provider, meter_provider)
        _installed_runtime = runtime
        _installed_runtime_configuration = configuration
        return runtime


def _install_providers(tracer_provider: object, meter_provider: object) -> None:
    """Install the single provider pair shared by BikeDoc and Google ADK."""

    trace.set_tracer_provider(cast(TracerProvider, tracer_provider))
    metrics.set_meter_provider(cast(MeterProvider, meter_provider))
    if trace.get_tracer_provider() is not tracer_provider:
        raise RuntimeError("a tracer provider was installed before BikeDoc telemetry")
    if metrics.get_meter_provider() is not meter_provider:
        raise RuntimeError("a meter provider was installed before BikeDoc telemetry")


def reset_telemetry_runtime_for_tests() -> None:
    """Clear BikeDoc's runtime handle for tests that inject provider installation."""

    global _installed_runtime
    global _installed_runtime_configuration
    with _runtime_lock:
        _installed_runtime = None
        _installed_runtime_configuration = None
