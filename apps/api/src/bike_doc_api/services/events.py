"""Repair-session event persistence, replay, and SSE formatting."""

from __future__ import annotations

import asyncio
from collections import defaultdict
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Protocol

from pydantic import ValidationError as PydanticValidationError

from bike_doc_api.core.errors import NotFoundError, ValidationAppError
from bike_doc_api.models.event import RepairSessionEvent as RepairSessionEventModel
from bike_doc_api.models.repair_session import RepairSession as RepairSessionModel
from bike_doc_api.models.user import User
from bike_doc_api.schemas.event import (
    RepairSessionEvent,
    RepairSessionEventType,
    repair_session_event_from_model,
    validate_repair_session_event_data,
)

DEFAULT_TIMEOUT_SECONDS = 30
MIN_TIMEOUT_SECONDS = 5
MAX_TIMEOUT_SECONDS = 120
REPLAY_BATCH_SIZE = 100


class RepairSessionEventRepositoryProtocol(Protocol):
    """Event persistence operations required by the service."""

    async def append_for_session(
        self,
        *,
        repair_session_id: str,
        event_type: str,
        data: dict[str, Any],
        turn_id: str | None = None,
    ) -> RepairSessionEventModel:
        """Lock a session, allocate the next sequence, and add an event."""

    async def list_after_sequence(
        self,
        *,
        repair_session_id: str,
        after_sequence: int,
        limit: int = REPLAY_BATCH_SIZE,
    ) -> list[RepairSessionEventModel]:
        """Return retained events newer than a public replay cursor."""


class RepairSessionRepositoryProtocol(Protocol):
    """Repair-session persistence operations required by the service."""

    async def get_owned(
        self,
        *,
        repair_session_id: str,
        user_id: str,
    ) -> RepairSessionModel | None:
        """Return a repair session owned by a user."""


@dataclass(frozen=True, slots=True)
class EventStream:
    """Prepared event stream that has already passed pre-open validation."""

    repair_session_id: str
    after_sequence: int
    timeout_seconds: int


class _LocalEventBroker:
    """In-process event fanout for same-worker live stream listeners."""

    def __init__(self) -> None:
        self._listeners: dict[str, set[asyncio.Queue[RepairSessionEvent]]] = (
            defaultdict(set)
        )

    def subscribe(self, repair_session_id: str) -> asyncio.Queue[RepairSessionEvent]:
        """Register a listener queue for one repair session."""

        queue: asyncio.Queue[RepairSessionEvent] = asyncio.Queue()
        self._listeners[repair_session_id].add(queue)
        return queue

    def unsubscribe(
        self,
        repair_session_id: str,
        queue: asyncio.Queue[RepairSessionEvent],
    ) -> None:
        """Remove a listener queue."""

        listeners = self._listeners.get(repair_session_id)
        if listeners is None:
            return
        listeners.discard(queue)
        if not listeners:
            self._listeners.pop(repair_session_id, None)

    async def publish(self, event: RepairSessionEvent) -> None:
        """Publish a committed public event to current local listeners."""

        for queue in tuple(self._listeners.get(event.session_id, ())):
            await queue.put(event)


_LOCAL_EVENT_BROKER = _LocalEventBroker()


class EventService:
    """Application-owned event log behavior."""

    def __init__(
        self,
        events: RepairSessionEventRepositoryProtocol,
        repair_sessions: RepairSessionRepositoryProtocol,
        *,
        commit: Callable[[], Awaitable[None]] | None = None,
        rollback: Callable[[], Awaitable[None]] | None = None,
    ) -> None:
        self._events = events
        self._repair_sessions = repair_sessions
        self._commit = commit
        self._rollback = rollback

    async def prepare_stream(
        self,
        *,
        current_user: User,
        repair_session_id: str,
        after: str | None,
        last_event_id: str | None,
        timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
    ) -> EventStream:
        """Validate ownership and cursor inputs before opening an SSE stream."""

        if (
            timeout_seconds < MIN_TIMEOUT_SECONDS
            or timeout_seconds > MAX_TIMEOUT_SECONDS
        ):
            raise ValidationAppError()

        repair_session = await self._repair_sessions.get_owned(
            repair_session_id=repair_session_id,
            user_id=current_user.id,
        )
        if repair_session is None:
            raise NotFoundError()

        return EventStream(
            repair_session_id=repair_session.id,
            after_sequence=_resolve_after_sequence(
                after=after,
                last_event_id=last_event_id,
                latest_event_sequence=repair_session.latest_event_sequence,
            ),
            timeout_seconds=timeout_seconds,
        )

    async def append_event(
        self,
        *,
        repair_session_id: str,
        event_type: RepairSessionEventType | str,
        data: dict[str, Any],
        turn_id: str | None = None,
    ) -> RepairSessionEvent:
        """Persist a public event, commit it, then notify local listeners."""

        try:
            public_event_type = RepairSessionEventType(event_type)
            validated_data = validate_repair_session_event_data(
                public_event_type,
                data,
            )
        except (PydanticValidationError, ValueError) as exc:
            raise ValidationAppError() from exc

        try:
            event = await self._events.append_for_session(
                repair_session_id=repair_session_id,
                turn_id=turn_id,
                event_type=public_event_type.value,
                data=validated_data,
            )
            if self._commit is not None:
                await self._commit()
        except Exception:
            if self._rollback is not None:
                await self._rollback()
            raise

        public_event = repair_session_event_from_model(event)
        await _LOCAL_EVENT_BROKER.publish(public_event)
        return public_event

    async def stream_sse_frames(self, stream: EventStream) -> AsyncIterator[str]:
        """Yield replay and live event frames for a prepared stream."""

        current_sequence = stream.after_sequence
        replayed_any = False

        async for event in self._iter_replay_events(
            repair_session_id=stream.repair_session_id,
            after_sequence=current_sequence,
        ):
            replayed_any = True
            current_sequence = event.sequence
            yield format_sse_frame(event)

        if stream.timeout_seconds <= MIN_TIMEOUT_SECONDS:
            if not replayed_any:
                heartbeat = await self.append_event(
                    repair_session_id=stream.repair_session_id,
                    event_type=RepairSessionEventType.HEARTBEAT,
                    data={"ok": True},
                )
                yield format_sse_frame(heartbeat)
            return

        queue = _LOCAL_EVENT_BROKER.subscribe(stream.repair_session_id)
        loop = asyncio.get_running_loop()
        deadline = loop.time() + stream.timeout_seconds
        heartbeat_interval = max(1.0, min(15.0, stream.timeout_seconds / 2))
        next_heartbeat_at = loop.time() + heartbeat_interval

        try:
            while True:
                now = loop.time()
                if now >= deadline:
                    return

                wait_seconds = max(0.0, min(deadline, next_heartbeat_at) - now)
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=wait_seconds)
                except TimeoutError:
                    if loop.time() >= next_heartbeat_at:
                        heartbeat = await self.append_event(
                            repair_session_id=stream.repair_session_id,
                            event_type=RepairSessionEventType.HEARTBEAT,
                            data={"ok": True},
                        )
                        current_sequence = heartbeat.sequence
                        next_heartbeat_at = loop.time() + heartbeat_interval
                        yield format_sse_frame(heartbeat)
                    continue

                if event.sequence <= current_sequence:
                    continue
                current_sequence = event.sequence
                next_heartbeat_at = loop.time() + heartbeat_interval
                yield format_sse_frame(event)
        finally:
            _LOCAL_EVENT_BROKER.unsubscribe(stream.repair_session_id, queue)

    async def _iter_replay_events(
        self,
        *,
        repair_session_id: str,
        after_sequence: int,
    ) -> AsyncIterator[RepairSessionEvent]:
        """Yield all retained events newer than the cursor in sequence order."""

        cursor = after_sequence
        while True:
            events = await self._events.list_after_sequence(
                repair_session_id=repair_session_id,
                after_sequence=cursor,
                limit=REPLAY_BATCH_SIZE,
            )
            if not events:
                return
            for event in events:
                public_event = repair_session_event_from_model(event)
                cursor = public_event.sequence
                yield public_event
            if len(events) < REPLAY_BATCH_SIZE:
                return


def format_sse_frame(event: RepairSessionEvent) -> str:
    """Return one OpenAPI-compatible SSE frame."""

    return (
        f"id: {event.id}\n"
        f"event: {event.type.value}\n"
        f"data: {event.model_dump_json()}\n\n"
    )


def _resolve_after_sequence(
    *,
    after: str | None,
    last_event_id: str | None,
    latest_event_sequence: int,
) -> int:
    """Resolve and validate the public replay cursor."""

    cursor = after if after is not None else last_event_id
    if cursor is None:
        return latest_event_sequence

    try:
        after_sequence = int(cursor, 10)
    except ValueError as exc:
        raise ValidationAppError() from exc

    if str(after_sequence) != cursor or after_sequence < 0:
        raise ValidationAppError()
    if after_sequence > latest_event_sequence:
        raise ValidationAppError()
    return after_sequence
