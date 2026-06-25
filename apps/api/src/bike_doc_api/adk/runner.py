"""ADK runner integration boundary."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable, Iterable, Mapping
from dataclasses import dataclass, field
from time import monotonic
from typing import Any, Literal, Protocol, cast

from google.adk.agents import Agent
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types

from bike_doc_api.adk.sessions import (
    DIAGNOSTIC_ADK_APP_NAME,
    DIAGNOSTIC_ADK_USER_ID,
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
    """Safety escalation notification normalized from an executed tool result."""

    safety_flag: Mapping[str, Any] = field(default_factory=dict)
    safety_state: str | None = None
    safety_flags: tuple[Mapping[str, Any], ...] = field(default_factory=tuple)
    event_id: str | None = None
    event_sequence: int | None = None


@dataclass(frozen=True, slots=True)
class DiagnosticRunnerReportCompleted:
    """Diagnostic report notification normalized from an executed tool result."""

    summary: str = ""
    report: Mapping[str, Any] = field(default_factory=dict)
    report_id: str | None = None
    schema_version: str | None = None
    diagnostic_session_id: str | None = None
    safety_state: str | None = None
    safety_flags: tuple[Mapping[str, Any], ...] = field(default_factory=tuple)
    phase_report_created_event_id: str | None = None
    phase_report_created_event_sequence: int | None = None
    phase_transitioned_event_id: str | None = None
    phase_transitioned_event_sequence: int | None = None


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
type DiagnosticADKRunnerFactory = Callable[[Agent, InMemorySessionService], Any]
type MonotonicClock = Callable[[], float]
type SleepFunction = Callable[[float], Awaitable[None]]


class DiagnosticRunnerProtocol(Protocol):
    """Internal diagnostic runner boundary consumed by orchestration."""

    def stream(
        self,
        request: DiagnosticRunnerRequest,
    ) -> AsyncIterator[DiagnosticRunnerEvent]:
        """Stream app-owned diagnostic runner events incrementally."""

    async def run(self, request: DiagnosticRunnerRequest) -> DiagnosticRunnerResult:
        """Collect streamed diagnostic events for compatibility callers."""


class DiagnosticRunner:
    """Normalize diagnostic ADK output into app-owned runner events."""

    def __init__(
        self,
        invoker: DiagnosticAgentInvoker | None = None,
        *,
        agent: Agent | None = None,
        session_service: InMemorySessionService | None = None,
        runner_factory: DiagnosticADKRunnerFactory | None = None,
        clock: MonotonicClock | None = None,
        sleep: SleepFunction | None = None,
        delta_character_threshold: int = 25,
        delta_flush_interval_seconds: float = 0.150,
    ) -> None:
        self._invoker = invoker
        self._agent = agent
        self._session_service = session_service
        self._runner_factory = runner_factory or _default_runner_factory
        self._clock = clock or monotonic
        self._sleep = sleep or asyncio.sleep
        self._delta_character_threshold = delta_character_threshold
        self._delta_flush_interval_seconds = delta_flush_interval_seconds

    @property
    def session_service(self) -> InMemorySessionService | None:
        """Return the shared ADK session service used for resume checks."""

        return self._session_service

    @property
    def agent(self) -> Agent | None:
        """Return the diagnostic ADK agent used by the streaming adapter."""

        return self._agent

    def stream(
        self,
        request: DiagnosticRunnerRequest,
    ) -> AsyncIterator[DiagnosticRunnerEvent]:
        """Stream app-owned diagnostic events while ADK processing is active."""

        return self._stream(request)

    async def run(self, request: DiagnosticRunnerRequest) -> DiagnosticRunnerResult:
        """Collect ``stream(...)`` output for older non-streaming callers."""

        return DiagnosticRunnerResult(
            events=tuple([event async for event in self.stream(request)]),
            completed=True,
        )

    async def _stream(
        self,
        request: DiagnosticRunnerRequest,
    ) -> AsyncIterator[DiagnosticRunnerEvent]:
        emitted_any = False

        if self._session_service is not None:
            try:
                await ensure_adk_session_available(
                    self._session_service,
                    adk_session_id=request.adk_session_id,
                )
            except StaleInMemoryADKSessionError:
                yield DiagnosticRunnerRecoverableError(
                    code="diagnostic_session_unavailable",
                    message=(
                        "Diagnostic processing needs a fresh turn because "
                        "the in-memory session is no longer available."
                    ),
                    retryable=True,
                )
                emitted_any = True
                return

        try:
            if self._invoker is not None:
                async for event in self._stream_invoker(request):
                    emitted_any = True
                    yield event
                return

            if self._agent is None or self._session_service is None:
                return

            async for event in self._stream_adk(request):
                emitted_any = True
                yield event
        except asyncio.CancelledError:
            raise
        except Exception:
            _ = emitted_any
            yield DiagnosticRunnerRecoverableError(
                code="diagnostic_processing_error",
                message="Diagnostic processing encountered a recoverable error.",
                retryable=True,
            )

    async def _stream_invoker(
        self,
        request: DiagnosticRunnerRequest,
    ) -> AsyncIterator[DiagnosticRunnerEvent]:
        """Stream legacy fake adapter output through the app-owned mapper."""

        if self._invoker is None:
            return
        raw_events = await self._invoker(request)
        text_chunks: list[str] = []

        for raw_event in raw_events:
            if isinstance(raw_event, DiagnosticRunnerAssistantDelta):
                text_chunks.append(raw_event.text)
                yield raw_event
                continue
            if isinstance(raw_event, _RUNNER_EVENT_CLASSES):
                yield raw_event
                continue
            mapped = _map_raw_event(raw_event, text_chunks=text_chunks)
            if mapped is None:
                continue
            if mapped == "running":
                continue
            if isinstance(mapped, DiagnosticRunnerAssistantDelta):
                text_chunks.append(mapped.text)
            yield mapped

    async def _stream_adk(
        self,
        request: DiagnosticRunnerRequest,
    ) -> AsyncIterator[DiagnosticRunnerEvent]:
        """Run the verified ADK Runner API and normalize events incrementally."""

        if self._agent is None or self._session_service is None:
            return

        runner = self._runner_factory(self._agent, self._session_service)
        adk_events = runner.run_async(
            user_id=DIAGNOSTIC_ADK_USER_ID,
            session_id=request.adk_session_id,
            new_message=_content_from_request(request),
            state_delta=_state_delta_from_request(request),
        )
        coalescer = _TextDeltaCoalescer(
            clock=self._clock,
            character_threshold=self._delta_character_threshold,
            flush_interval_seconds=self._delta_flush_interval_seconds,
        )
        async for event in _iter_adk_events_with_timed_flushes(
            adk_events,
            coalescer=coalescer,
            sleep=self._sleep,
        ):
            if isinstance(event, DiagnosticRunnerAssistantDelta):
                yield event
                continue
            async for normalized in self._normalize_adk_event(
                event,
                coalescer,
                request=request,
            ):
                yield normalized

    async def _normalize_adk_event(
        self,
        event: Any,
        coalescer: _TextDeltaCoalescer,
        *,
        request: DiagnosticRunnerRequest,
    ) -> AsyncIterator[DiagnosticRunnerEvent]:
        """Normalize one verified ADK event without leaking raw ADK details."""

        if _event_is_partial_text(event):
            for text in _event_text_parts(event):
                flushed = coalescer.add(text)
                if flushed is not None:
                    yield flushed
            return

        for function_response_event in _map_function_responses(event):
            yield function_response_event

        if _event_is_final_response(event):
            flushed = coalescer.flush()
            if flushed is not None:
                yield flushed
            final_text = _final_text_from_event(
                event,
                accumulated_text=coalescer.full_text,
            )
            yield DiagnosticRunnerAssistantMessageCompleted(
                message_id=generate_prefixed_ulid("msg_"),
                full_text=final_text,
                artifact_ids=request.artifact_ids,
                display_safety_level=DisplaySafetyLevel.NORMAL,
            )


def _default_runner_factory(
    agent: Agent, session_service: InMemorySessionService
) -> Runner:
    """Construct the real Google ADK runner for diagnostic turns."""

    return Runner(
        app_name=DIAGNOSTIC_ADK_APP_NAME,
        agent=agent,
        session_service=session_service,
    )


def _content_from_request(request: DiagnosticRunnerRequest) -> types.Content:
    """Convert app-owned turn text into the verified GenAI content shape."""

    return types.Content(
        role="user",
        parts=[types.Part.from_text(text=request.message_text or "")],
    )


def _state_delta_from_request(request: DiagnosticRunnerRequest) -> dict[str, Any]:
    """Build safe app-owned ADK state without raw ADK/provider internals."""

    return {
        "app_context": {
            "user_id": request.user_id,
            "user_skill_level": request.user_skill_level,
            "repair_session_id": request.repair_session_id,
            "active_phase": "diagnostic",
            "diagnostic_session_id": request.diagnostic_session_id,
            "turn_id": request.turn_id,
            "artifact_ids": list(request.artifact_ids),
            "bike_profile": (
                dict(request.bike_profile)
                if isinstance(request.bike_profile, Mapping)
                else None
            ),
            "repair_history": [dict(entry) for entry in request.repair_history],
            "diagnostic_artifacts": [
                dict(artifact) for artifact in request.diagnostic_artifacts
            ],
        },
    }


class _TextDeltaCoalescer:
    """Coalesce assistant text deltas by size or elapsed time."""

    def __init__(
        self,
        *,
        clock: MonotonicClock,
        character_threshold: int,
        flush_interval_seconds: float,
    ) -> None:
        self._clock = clock
        self._character_threshold = character_threshold
        self._flush_interval_seconds = flush_interval_seconds
        self._buffer: list[str] = []
        self._full_text_parts: list[str] = []
        self._last_flush_at = self._clock()

    @property
    def has_pending(self) -> bool:
        """Return whether buffered text is waiting to be yielded."""

        return bool(self._buffer)

    @property
    def full_text(self) -> str:
        """Return all assistant text seen from partial events."""

        return "".join(self._full_text_parts)

    def add(self, text: str) -> DiagnosticRunnerAssistantDelta | None:
        """Append text and flush when the character threshold is reached."""

        if not text:
            return None
        self._buffer.append(text)
        self._full_text_parts.append(text)
        if len("".join(self._buffer)) >= self._character_threshold:
            return self.flush()
        if self.time_until_flush() <= 0:
            return self.flush()
        return None

    def time_until_flush(self) -> float:
        """Return seconds remaining before pending text should flush."""

        elapsed = self._clock() - self._last_flush_at
        return max(self._flush_interval_seconds - elapsed, 0.0)

    def flush(self) -> DiagnosticRunnerAssistantDelta | None:
        """Flush pending text into one app-owned assistant delta."""

        if not self._buffer:
            return None
        text = "".join(self._buffer)
        self._buffer.clear()
        self._last_flush_at = self._clock()
        if not text:
            return None
        return DiagnosticRunnerAssistantDelta(text=text)


async def _iter_adk_events_with_timed_flushes(
    adk_events: AsyncIterator[Any],
    *,
    coalescer: _TextDeltaCoalescer,
    sleep: SleepFunction,
) -> AsyncIterator[Any | DiagnosticRunnerAssistantDelta]:
    """Yield ADK events and timed text-buffer flushes while the run is active."""

    iterator = aiter(adk_events)
    next_task: asyncio.Future[Any] | None = asyncio.ensure_future(anext(iterator))
    sleep_task: asyncio.Future[None] | None = None

    try:
        while next_task is not None:
            wait_tasks: set[asyncio.Future[Any]] = {next_task}
            if coalescer.has_pending:
                sleep_task = asyncio.ensure_future(sleep(coalescer.time_until_flush()))
                wait_tasks.add(cast(asyncio.Future[Any], sleep_task))

            done, _pending = await asyncio.wait(
                wait_tasks,
                return_when=asyncio.FIRST_COMPLETED,
            )

            if sleep_task is not None and sleep_task in done:
                sleep_task = None
                flushed = coalescer.flush()
                if flushed is not None:
                    yield flushed
                continue

            if sleep_task is not None:
                sleep_task.cancel()
                sleep_task = None

            if next_task in done:
                try:
                    event = next_task.result()
                except StopAsyncIteration:
                    next_task = None
                    break
                yield event
                next_task = asyncio.ensure_future(anext(iterator))
    finally:
        if sleep_task is not None and not sleep_task.done():
            sleep_task.cancel()
        if next_task is not None and not next_task.done():
            next_task.cancel()

    flushed = coalescer.flush()
    if flushed is not None:
        yield flushed


def _event_is_partial_text(event: Any) -> bool:
    """Return whether an ADK event carries streamed assistant text."""

    return getattr(event, "partial", False) is True and bool(_event_text_parts(event))


def _event_is_final_response(event: Any) -> bool:
    """Return whether an ADK event marks the assistant response as final."""

    is_final_response = getattr(event, "is_final_response", None)
    if not callable(is_final_response):
        return False
    try:
        return bool(is_final_response())
    except Exception:
        return False


def _event_text_parts(event: Any) -> tuple[str, ...]:
    """Extract only text parts from a verified ADK event."""

    content = getattr(event, "content", None)
    parts = getattr(content, "parts", None)
    if not isinstance(parts, Iterable):
        return ()
    texts: list[str] = []
    for part in parts:
        text = getattr(part, "text", None)
        if isinstance(text, str) and text:
            texts.append(text)
    return tuple(texts)


def _final_text_from_event(event: Any, *, accumulated_text: str) -> str:
    """Return a public-safe final assistant message body."""

    final_text = "".join(_event_text_parts(event))
    if not final_text:
        return accumulated_text
    if not accumulated_text:
        return final_text
    if final_text.startswith(accumulated_text):
        return final_text
    if accumulated_text.endswith(final_text):
        return accumulated_text
    return f"{accumulated_text}{final_text}"


def _map_function_responses(event: Any) -> tuple[DiagnosticRunnerEvent, ...]:
    """Map executed ADK tool responses into app-owned notifications."""

    get_function_responses = getattr(event, "get_function_responses", None)
    if not callable(get_function_responses):
        return ()
    try:
        function_responses = get_function_responses()
    except Exception:
        return (
            DiagnosticRunnerRecoverableError(
                code="runner_output_invalid",
                message="Diagnostic runner produced an invalid tool response.",
                retryable=False,
            ),
        )

    mapped: list[DiagnosticRunnerEvent] = []
    for function_response in function_responses:
        event_or_none = _map_function_response(function_response)
        if event_or_none is not None:
            mapped.append(event_or_none)
    return tuple(mapped)


def _map_function_response(function_response: Any) -> DiagnosticRunnerEvent | None:
    """Map one state-mutating tool result without exposing raw ADK traces."""

    name = _non_empty_string(getattr(function_response, "name", None))
    if name not in {
        "request_diagnostic_input",
        "raise_safety_flag",
        "save_diagnostic_report",
    }:
        return None
    response = getattr(function_response, "response", None)
    if not isinstance(response, Mapping):
        return _invalid_tool_response()

    ok = response.get("ok")
    if ok is False:
        return _tool_error_response(response)
    if ok is not True:
        return _invalid_tool_response()

    data = response.get("data")
    if not isinstance(data, Mapping):
        return _invalid_tool_response()
    if name == "request_diagnostic_input":
        return _input_requested_from_tool_data(data)
    if name == "raise_safety_flag":
        return _safety_escalated_from_tool_data(data)
    return _report_completed_from_tool_data(data)


def _input_requested_from_tool_data(
    data: Mapping[str, Any],
) -> DiagnosticRunnerEvent:
    input_request = data.get("input_request")
    if not isinstance(input_request, Mapping):
        return _invalid_tool_response()
    request_type = _non_empty_string(input_request.get("type"))
    prompt = _non_empty_string(input_request.get("prompt"))
    if request_type is None or prompt is None:
        return _invalid_tool_response()
    return DiagnosticRunnerInputRequested(
        request_type=request_type,
        prompt=prompt,
        required=bool(input_request.get("required", True)),
        accepted_media_types=_string_tuple(input_request.get("accepted_media_types")),
        choices=_mapping_tuple(input_request.get("choices")),
        min_artifacts=_optional_int(input_request.get("min_artifacts")),
        max_artifacts=_optional_int(input_request.get("max_artifacts")),
    )


def _safety_escalated_from_tool_data(
    data: Mapping[str, Any],
) -> DiagnosticRunnerEvent:
    safety_state = _non_empty_string(data.get("safety_state"))
    safety_flags = _mapping_tuple(data.get("active_safety_flags"))
    if safety_state is None:
        return _invalid_tool_response()
    return DiagnosticRunnerSafetyEscalated(
        safety_flag=safety_flags[0] if safety_flags else {},
        safety_state=safety_state,
        safety_flags=safety_flags,
        event_sequence=_optional_int(data.get("event_sequence")),
    )


def _report_completed_from_tool_data(
    data: Mapping[str, Any],
) -> DiagnosticRunnerEvent:
    report_id = _non_empty_string(data.get("report_id"))
    schema_version = _non_empty_string(data.get("schema_version"))
    diagnostic_session_id = _app_owned_id(data.get("diagnostic_session_id"), "phs_")
    if report_id is None or schema_version is None:
        return _invalid_tool_response()
    return DiagnosticRunnerReportCompleted(
        report_id=report_id,
        schema_version=schema_version,
        diagnostic_session_id=diagnostic_session_id,
        safety_state=_non_empty_string(data.get("safety_state")),
        safety_flags=_mapping_tuple(data.get("safety_flags")),
        phase_report_created_event_sequence=_optional_int(
            data.get("phase_report_created_event_sequence"),
        ),
        phase_transitioned_event_sequence=_optional_int(
            data.get("phase_transitioned_event_sequence"),
        ),
    )


def _tool_error_response(
    response: Mapping[str, Any],
) -> DiagnosticRunnerRecoverableError:
    """Map a structured tool error to a public-safe runner error."""

    error = response.get("error")
    if not isinstance(error, Mapping):
        return _invalid_tool_response()
    code = _non_empty_string(error.get("code")) or "diagnostic_processing_error"
    message = _non_empty_string(error.get("message")) or (
        "Diagnostic processing encountered a recoverable error."
    )
    return DiagnosticRunnerRecoverableError(code=code, message=message, retryable=False)


def _invalid_tool_response() -> DiagnosticRunnerRecoverableError:
    """Return a stable error for malformed ADK tool output."""

    return DiagnosticRunnerRecoverableError(
        code="runner_output_invalid",
        message="Diagnostic runner produced an invalid tool response.",
        retryable=False,
    )


def _app_owned_id(value: object, prefix: str) -> str | None:
    """Return an app-owned ID only when it has the expected public prefix."""

    normalized = _non_empty_string(value)
    if normalized is None or not normalized.startswith(prefix):
        return None
    return normalized


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
        return DiagnosticRunnerSafetyEscalated(
            safety_flag=cast(Mapping[str, Any], safety_flag),
        )

    if event_type in {"phase.report.created", "report_completed"}:
        summary = _non_empty_string(raw_event.get("summary"))
        report = raw_event.get("report")
        if summary is None or not isinstance(report, Mapping):
            return DiagnosticRunnerRecoverableError(
                code="runner_output_invalid",
                message="Diagnostic runner produced an invalid diagnostic report.",
                retryable=False,
            )
        return DiagnosticRunnerReportCompleted(
            summary=summary,
            report=cast(Mapping[str, Any], report),
        )

    if event_type in {"artifact.referenced", "artifact_referenced"}:
        artifact = raw_event.get("artifact")
        if isinstance(artifact, Mapping):
            return DiagnosticRunnerArtifactReferenced(
                artifact=cast(Mapping[str, Any], artifact),
            )
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
