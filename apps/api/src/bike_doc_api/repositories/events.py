"""Event repository."""

from typing import Any

import structlog
from sqlalchemy import event as sqlalchemy_event
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from bike_doc_api.core.event_wakeups import get_event_wakeups
from bike_doc_api.models.event import RepairSessionEvent
from bike_doc_api.models.repair_session import RepairSession

_PENDING = "bike_doc_event_wakeups"
_INSTALLED = "bike_doc_event_wakeup_hooks_installed"
logger = structlog.get_logger(__name__)


def _after_commit(session: Session) -> None:
    """Only the successful product commit releases hints."""
    if session.in_nested_transaction():
        return
    pending: dict[str, int] = session.info.pop(_PENDING, {})
    wakeups = get_event_wakeups()
    for repair_session_id, sequence in pending.items():
        try:
            wakeups.publish_soon(repair_session_id, sequence)
        except Exception:
            # This callback runs after the database commit. Notification
            # failures must never change the committed product result.
            logger.warning("event_wakeup_dispatch_failed")


def _after_rollback(session: Session) -> None:
    session.info.pop(_PENDING, None)


def _register_event(session: AsyncSession, row: RepairSessionEvent) -> None:
    sync = session.sync_session
    if not sync.info.get(_INSTALLED):
        sqlalchemy_event.listen(sync, "after_commit", _after_commit)
        sqlalchemy_event.listen(sync, "after_rollback", _after_rollback)
        sync.info[_INSTALLED] = True
    pending: dict[str, int] = sync.info.setdefault(_PENDING, {})
    pending[row.repair_session_id] = max(
        row.sequence, pending.get(row.repair_session_id, 0)
    )


class RepairSessionEventRepository:
    """Persistence operations for repair session events."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, event: RepairSessionEvent) -> RepairSessionEvent:
        """Add an event with an already allocated sequence."""
        self._session.add(event)
        await self._session.flush()
        await self._session.refresh(event)
        if event.type != "heartbeat":
            _register_event(self._session, event)
        return event

    async def append_for_session(
        self,
        *,
        repair_session_id: str,
        event_type: str,
        data: dict[str, Any],
        turn_id: str | None = None,
    ) -> RepairSessionEvent:
        """Lock a session, allocate the next sequence, and add an event."""
        result = await self._session.execute(
            select(RepairSession)
            .where(RepairSession.id == repair_session_id)
            .with_for_update(),
        )
        repair_session = result.scalar_one()
        sequence = repair_session.latest_event_sequence + 1
        event = RepairSessionEvent(
            repair_session_id=repair_session_id,
            turn_id=turn_id,
            sequence=sequence,
            type=event_type,
            data=data,
        )
        self._session.add(event)
        repair_session.latest_event_sequence = sequence
        await self._session.flush()
        await self._session.refresh(event)
        if event.type != "heartbeat":
            _register_event(self._session, event)
        return event

    async def get(self, event_id: str) -> RepairSessionEvent | None:
        """Return an event by internal row ID."""
        return await self._session.get(RepairSessionEvent, event_id)

    async def get_by_sequence(
        self,
        *,
        repair_session_id: str,
        sequence: int,
    ) -> RepairSessionEvent | None:
        """Return an event by public per-session sequence."""
        result = await self._session.execute(
            select(RepairSessionEvent).where(
                RepairSessionEvent.repair_session_id == repair_session_id,
                RepairSessionEvent.sequence == sequence,
            ),
        )
        return result.scalar_one_or_none()

    async def list_after_sequence(
        self,
        *,
        repair_session_id: str,
        after_sequence: int,
        limit: int = 100,
    ) -> list[RepairSessionEvent]:
        """Return retained events newer than a public replay cursor."""
        result = await self._session.execute(
            select(RepairSessionEvent)
            .where(
                RepairSessionEvent.repair_session_id == repair_session_id,
                RepairSessionEvent.sequence > after_sequence,
            )
            .order_by(RepairSessionEvent.sequence.asc())
            .limit(limit),
        )
        return list(result.scalars().all())
