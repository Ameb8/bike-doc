"""Lossy event hints; PostgreSQL remains the source of SSE frames."""

import asyncio
from collections import defaultdict
from typing import Protocol

import structlog

logger = structlog.get_logger(__name__)


class EventWakeupPublisher(Protocol):
    """Publish a minimal hint after an event transaction commits."""

    async def publish(self, repair_session_id: str, sequence: int) -> None: ...


class EventWakeupSubscriber(Protocol):
    """Register a local reader for a repair session."""

    def subscribe(self, repair_session_id: str) -> asyncio.Queue[None]: ...

    def unsubscribe(
        self, repair_session_id: str, queue: asyncio.Queue[None]
    ) -> None: ...

    def receive(self, repair_session_id: str) -> None: ...


class EventWakeups(EventWakeupPublisher, EventWakeupSubscriber):
    """One process-wide fanout with bounded per-reader hints."""

    def __init__(self, publisher: EventWakeupPublisher | None = None) -> None:
        self.publisher = publisher
        self._listeners: dict[str, set[asyncio.Queue[None]]] = defaultdict(set)
        self._pending: set[asyncio.Task[None]] = set()

    def subscribe(self, repair_session_id: str) -> asyncio.Queue[None]:
        queue: asyncio.Queue[None] = asyncio.Queue(maxsize=1)
        self._listeners[repair_session_id].add(queue)
        return queue

    def unsubscribe(self, repair_session_id: str, queue: asyncio.Queue[None]) -> None:
        listeners = self._listeners.get(repair_session_id)
        if listeners is not None:
            listeners.discard(queue)
            if not listeners:
                del self._listeners[repair_session_id]

    def receive(self, repair_session_id: str) -> None:
        """Fan out a hint; its sequence is deliberately ignored by readers."""
        for queue in tuple(self._listeners.get(repair_session_id, ())):
            if queue.empty():
                queue.put_nowait(None)

    async def publish(self, repair_session_id: str, sequence: int) -> None:
        self.receive(repair_session_id)
        if self.publisher is not None:
            await self.publisher.publish(repair_session_id, sequence)

    def publish_soon(self, repair_session_id: str, sequence: int) -> None:
        """Dispatch a committed hint without holding the product transaction."""
        self.receive(repair_session_id)
        task = asyncio.get_running_loop().create_task(
            self.publish(repair_session_id, sequence)
        )
        self._pending.add(task)
        task.add_done_callback(self._published)

    def _published(self, task: asyncio.Task[None]) -> None:
        self._pending.discard(task)
        try:
            task.result()
        except asyncio.CancelledError:
            return
        except Exception:
            logger.warning("event_wakeup_publish_failed")

    async def drain(self) -> None:
        """Give pending best-effort publications a bounded shutdown window."""
        if self._pending:
            try:
                await asyncio.wait_for(
                    asyncio.gather(*tuple(self._pending), return_exceptions=True),
                    timeout=3,
                )
            except TimeoutError:
                for task in tuple(self._pending):
                    task.cancel()


_wakeups = EventWakeups()


def get_event_wakeups() -> EventWakeups:
    """Return the process-owned fanout shared by writers and SSE readers."""
    return _wakeups


def install_event_wakeups(wakeups: EventWakeups) -> None:
    """Install the lifespan-owned fanout."""
    global _wakeups
    _wakeups = wakeups
