"""ADK runner integration boundary."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol, cast

from google.adk.sessions import InMemorySessionService

from bike_doc_api.adk.sessions import (
    StaleInMemoryADKSessionError,
    ensure_adk_session_available,
)
from bike_doc_api.models._ids import generate_prefixed_ulid
from bike_doc_api.schemas.event import DisplaySafetyLevel


@dataclass(frozen=True, slots=True)
class DiagnosticRunnerRequest:
    """App-owned diagnostic runner input with server-seeded context."""

    user_id: str
    user_skill_level: str
    repair_session_id: str
    turn_id: str
    diagnostic_session_id: str
    adk_session_id: str
    message_text: str | None
    artifact_ids: tuple[str, ...]
    bike_profile: Mapping[str, Any] | None
    repair_history: tuple[Mapping[str, Any], ...] = field(default_factory=tuple)
    diagnostic_artifacts: tuple[Mapping[str, Any], ...] = field(default_factory=tuple)


@dataclass(frozen=True, slots=True)
class DiagnosticRunnerAssistantDelta:
    """Assistant text chunk normalized for public event mapping."""

    text: str


@dataclass(frozen=True, slots=True)
class DiagnosticRunnerAssistantMessageCompleted:
    """Completed assistant message normalized for public event mapping."""

    message_id: str
    full_text: str
    artifact_ids: tuple[str, ...] = field(default_factory=tuple)
    display_safety_level: DisplaySafetyLevel = DisplaySafetyLevel.NORMAL


@dataclass(frozen=True, slots=True)
class DiagnosticRunnerInputRequested:
    """Structured request for missing diagnostic input."""

    request_type: str
    prompt: str
    required: bool = True
    accepted_media_types: tuple[str, ...] = field(default_factory=tuple)
    choices: tuple[Mapping[str, Any], ...] = field(default_factory=tuple)
    min_artifacts: int | None = None
    max_artifacts: int | None = None


@dataclass(frozen=True, slots=True)
class DiagnosticRunnerSafetyEscalated:
    """Safety flag output that must be persisted through the safety service."""

    safety_flag: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class DiagnosticRunnerReportCompleted:
    """Diagnostic report output that must be persisted through the report tool."""

    summary: str
    report: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class DiagnosticRunnerArtifactReferenced:
    """Artifact metadata referenced by diagnostic processing."""

    artifact: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class DiagnosticRunnerRecoverableError:
    """Recoverable processing failure safe for public error events."""

    code: str
    message: str
    retryable: bool = False


type DiagnosticRunnerEvent = (
    DiagnosticRunnerAssistantDelta
    | DiagnosticRunnerAssistantMessageCompleted
    | DiagnosticRunnerInputRequested
    | DiagnosticRunnerSafetyEscalated
    | DiagnosticRunnerReportCompleted
    | DiagnosticRunnerArtifactReferenced
    | DiagnosticRunnerRecoverableError
)


@dataclass(frozen=True, slots=True)
class DiagnosticRunnerResult:
    """App-owned diagnostic runner result with no raw ADK classes."""

    events: tuple[DiagnosticRunnerEvent, ...] = field(default_factory=tuple)
    completed: bool = True


type DiagnosticAgentInvoker = Callable[
    [DiagnosticRunnerRequest],
    Awaitable[Iterable[DiagnosticRunnerEvent | Mapping[str, Any]]],
]


class DiagnosticRunnerProtocol(Protocol):
    """Internal diagnostic runner boundary consumed by orchestration."""

    async def run(self, request: DiagnosticRunnerRequest) -> DiagnosticRunnerResult:
        """Invoke the diagnostic agent and return app-owned output."""


class DiagnosticRunner:
    """Normalize diagnostic ADK output into app-owned runner events."""

    def __init__(
        self,
        invoker: DiagnosticAgentInvoker | None = None,
        *,
        session_service: InMemorySessionService | None = None,
    ) -> None:
        self._invoker = invoker
        self._session_service = session_service

    @property
    def session_service(self) -> InMemorySessionService | None:
        """Return the shared ADK session service used for resume checks."""

        return self._session_service

    async def run(self, request: DiagnosticRunnerRequest) -> DiagnosticRunnerResult:
        """Invoke the configured diagnostic agent adapter."""

        if self._session_service is not None:
            try:
                await ensure_adk_session_available(
                    self._session_service,
                    adk_session_id=request.adk_session_id,
                )
            except StaleInMemoryADKSessionError:
                return DiagnosticRunnerResult(
                    events=(
                        DiagnosticRunnerRecoverableError(
                            code="diagnostic_session_unavailable",
                            message=(
                                "Diagnostic processing needs a fresh turn because "
                                "the in-memory session is no longer available."
                            ),
                            retryable=True,
                        ),
                    ),
                    completed=True,
                )

        if self._invoker is None:
            return DiagnosticRunnerResult()

        raw_events = await self._invoker(request)
        normalized: list[DiagnosticRunnerEvent] = []
        text_chunks: list[str] = []
        completed = True

        for raw_event in raw_events:
            if isinstance(raw_event, DiagnosticRunnerAssistantDelta):
                text_chunks.append(raw_event.text)
                normalized.append(raw_event)
                continue
            if isinstance(raw_event, _RUNNER_EVENT_CLASSES):
                normalized.append(raw_event)
                continue
            mapped = _map_raw_event(raw_event, text_chunks=text_chunks)
            if mapped is None:
                continue
            if mapped == "running":
                completed = False
                continue
            if isinstance(mapped, DiagnosticRunnerAssistantDelta):
                text_chunks.append(mapped.text)
            normalized.append(mapped)

        return DiagnosticRunnerResult(events=tuple(normalized), completed=completed)


_RUNNER_EVENT_CLASSES = (
    DiagnosticRunnerAssistantMessageCompleted,
    DiagnosticRunnerInputRequested,
    DiagnosticRunnerSafetyEscalated,
    DiagnosticRunnerReportCompleted,
    DiagnosticRunnerArtifactReferenced,
    DiagnosticRunnerRecoverableError,
)


def _map_raw_event(
    raw_event: DiagnosticRunnerEvent | Mapping[str, Any],
    *,
    text_chunks: list[str],
) -> DiagnosticRunnerEvent | Literal["running"] | None:
    """Map generic adapter output into app-owned runner events."""

    if not isinstance(raw_event, Mapping):
        return None

    event_type = str(raw_event.get("type", "")).strip()
    if event_type in {"assistant.delta", "assistant_delta"}:
        text = _text_chunk(raw_event.get("text"))
        if text is None:
            return None
        return DiagnosticRunnerAssistantDelta(text=text)

    if event_type in {
        "assistant.message.completed",
        "assistant_message_completed",
    }:
        full_text = _non_empty_string(raw_event.get("full_text")) or "".join(
            text_chunks,
        )
        message_id = _non_empty_string(raw_event.get("message_id")) or (
            generate_prefixed_ulid("msg_")
        )
        return DiagnosticRunnerAssistantMessageCompleted(
            message_id=message_id,
            full_text=full_text,
            artifact_ids=_string_tuple(raw_event.get("artifact_ids")),
            display_safety_level=_display_safety_level(
                raw_event.get("display_safety_level"),
            ),
        )

    if event_type in {"input.requested", "input_requested"}:
        request_type = _non_empty_string(raw_event.get("request_type"))
        prompt = _non_empty_string(raw_event.get("prompt"))
        if request_type is None or prompt is None:
            return DiagnosticRunnerRecoverableError(
                code="runner_output_invalid",
                message="Diagnostic runner produced an invalid input request.",
                retryable=False,
            )
        return DiagnosticRunnerInputRequested(
            request_type=request_type,
            prompt=prompt,
            required=bool(raw_event.get("required", True)),
            accepted_media_types=_string_tuple(raw_event.get("accepted_media_types")),
            choices=_mapping_tuple(raw_event.get("choices")),
            min_artifacts=_optional_int(raw_event.get("min_artifacts")),
            max_artifacts=_optional_int(raw_event.get("max_artifacts")),
        )

    if event_type in {"safety.escalated", "safety_escalated"}:
        safety_flag = raw_event.get("safety_flag")
        if not isinstance(safety_flag, Mapping):
            return DiagnosticRunnerRecoverableError(
                code="runner_output_invalid",
                message="Diagnostic runner produced an invalid safety flag.",
                retryable=False,
            )
        return DiagnosticRunnerSafetyEscalated(safety_flag=safety_flag)

    if event_type in {"phase.report.created", "report_completed"}:
        summary = _non_empty_string(raw_event.get("summary"))
        report = raw_event.get("report")
        if summary is None or not isinstance(report, Mapping):
            return DiagnosticRunnerRecoverableError(
                code="runner_output_invalid",
                message="Diagnostic runner produced an invalid diagnostic report.",
                retryable=False,
            )
        return DiagnosticRunnerReportCompleted(summary=summary, report=report)

    if event_type in {"artifact.referenced", "artifact_referenced"}:
        artifact = raw_event.get("artifact")
        if isinstance(artifact, Mapping):
            return DiagnosticRunnerArtifactReferenced(artifact=artifact)
        return None

    if event_type in {"error", "recoverable_error"}:
        code = _non_empty_string(raw_event.get("code")) or "diagnostic_processing_error"
        message = _non_empty_string(raw_event.get("message")) or (
            "Diagnostic processing encountered a recoverable error."
        )
        return DiagnosticRunnerRecoverableError(
            code=code,
            message=message,
            retryable=bool(raw_event.get("retryable", False)),
        )

    if event_type == "running":
        return "running"
    return None


def _non_empty_string(value: object) -> str | None:
    """Return a stripped non-empty string."""

    if not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized or None


def _text_chunk(value: object) -> str | None:
    """Return a non-empty assistant text chunk without trimming content."""

    if not isinstance(value, str) or not value:
        return None
    return value


def _string_tuple(value: object) -> tuple[str, ...]:
    """Return a tuple of stripped strings from raw adapter output."""

    if not isinstance(value, Iterable) or isinstance(value, str):
        return ()
    strings: list[str] = []
    for item in value:
        normalized = _non_empty_string(item)
        if normalized is not None:
            strings.append(normalized)
    return tuple(strings)


def _mapping_tuple(value: object) -> tuple[Mapping[str, Any], ...]:
    """Return a tuple of mappings from raw adapter output."""

    if not isinstance(value, Iterable) or isinstance(value, (str, bytes)):
        return ()
    return tuple(
        cast(Mapping[str, Any], item) for item in value if isinstance(item, Mapping)
    )


def _optional_int(value: object) -> int | None:
    """Return an optional integer from raw adapter output."""

    return value if isinstance(value, int) else None


def _display_safety_level(value: object) -> DisplaySafetyLevel:
    """Return a public display safety level without exposing provider metadata."""

    try:
        return DisplaySafetyLevel(cast(str, value))
    except ValueError:
        return DisplaySafetyLevel.NORMAL
