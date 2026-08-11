"""Code-owned privacy policy for diagnostic ADK telemetry."""

from __future__ import annotations

import importlib.util
import inspect
import os
from collections.abc import Callable, Mapping

from google.adk.agents.run_config import RunConfig
from google.adk.telemetry.context import (
    ADK_CAPTURE_MESSAGE_CONTENT_IN_SPANS,
    ADK_TELEMETRY_IGNORE_RUN_CONFIG,
    OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT,
    ContentCapturingMode,
    TelemetryConfig,
)
from google.genai.models import Models

from bike_doc_api.core.config import Settings

_GOOGLE_GENAI_INSTRUMENTATION_MODULE = "opentelemetry.instrumentation.google_genai"
_TRUTHY = frozenset({"1", "true"})
_FALSY = frozenset({"0", "false"})
_UNWRAPPED_GENERATE_CONTENT = Models.generate_content


class DiagnosticTelemetryConfigurationError(ValueError):
    """The installed ADK telemetry runtime can export diagnostic content."""


def diagnostic_no_content_run_config() -> RunConfig:
    """Build the immutable-in-effect policy supplied to every ADK invocation."""

    return RunConfig(
        telemetry=TelemetryConfig(
            capture_message_content=ContentCapturingMode.NO_CONTENT,
        )
    )


def validate_diagnostic_telemetry_runtime_configuration(
    settings: Settings,
    *,
    environ: Mapping[str, str] | None = None,
    find_spec: Callable[[str], object | None] = importlib.util.find_spec,
    generate_content: object | None = None,
) -> None:
    """Reject process configuration that can bypass diagnostic no-content policy.

    Test environments intentionally retain a controlled instrumentation seam for
    deterministic export assertions. Production-style startup must reject both
    ADK's admin lock and any independent Google GenAI instrumentation because
    neither can be safely governed by a per-run ``RunConfig``.
    """

    if settings.environment.lower() == "test":
        return

    env = environ if environ is not None else os.environ
    violations: list[str] = []
    if _is_truthy(env.get(ADK_TELEMETRY_IGNORE_RUN_CONFIG)):
        violations.append(ADK_TELEMETRY_IGNORE_RUN_CONFIG)
    if _is_unsafe_genai_content_mode(
        env.get(OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT)
    ):
        violations.append(OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT)
    if ADK_CAPTURE_MESSAGE_CONTENT_IN_SPANS in env and not _is_falsy(
        env[ADK_CAPTURE_MESSAGE_CONTENT_IN_SPANS]
    ):
        violations.append(ADK_CAPTURE_MESSAGE_CONTENT_IN_SPANS)
    try:
        genai_instrumentation_installed = (
            find_spec(_GOOGLE_GENAI_INSTRUMENTATION_MODULE) is not None
        )
    except ModuleNotFoundError:
        # ``find_spec`` raises when an optional module's parent package is absent.
        genai_instrumentation_installed = False
    if genai_instrumentation_installed:
        violations.append(_GOOGLE_GENAI_INSTRUMENTATION_MODULE)
    if _has_external_generate_content_wrapper(
        generate_content or Models.generate_content
    ):
        violations.append("google.genai.Models.generate_content runtime hook")

    policy = diagnostic_no_content_run_config().telemetry
    if (
        policy is None
        or policy.should_add_content_to_legacy_spans
        or policy.should_add_content_to_experimental_spans
        or policy.should_add_content_to_logs
    ):
        violations.append("installed ADK RunConfig.telemetry resolution")

    if violations:
        raise DiagnosticTelemetryConfigurationError(
            "diagnostic telemetry content capture is unsafe: " + ", ".join(violations)
        )


def _is_truthy(value: str | None) -> bool:
    return value is not None and value.strip().lower() in _TRUTHY


def _is_falsy(value: str) -> bool:
    return value.strip().lower() in _FALSY


def _is_unsafe_genai_content_mode(value: str | None) -> bool:
    if value is None:
        return False
    normalized = value.strip().upper()
    return normalized not in {"", ContentCapturingMode.NO_CONTENT.value}


def _has_external_generate_content_wrapper(function: object) -> bool:
    """Detect active wrappers, which receive model content before ADK can redact."""

    if function is not _UNWRAPPED_GENERATE_CONTENT:
        return True
    current = function
    while wrapped := getattr(current, "__wrapped__", None):
        module = getattr(current, "__module__", "")
        try:
            filename = inspect.getsourcefile(current) or ""
        except (OSError, TypeError):
            filename = ""
        if (
            not module.startswith("google.genai")
            or _GOOGLE_GENAI_INSTRUMENTATION_MODULE.replace(".", "/") in filename
        ):
            return True
        current = wrapped
    return False
