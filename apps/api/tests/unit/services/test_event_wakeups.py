"""Transaction-bound event hints and polling recovery."""

import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from bike_doc_api.core.config import Settings
from bike_doc_api.core.event_notifications import SUBJECT, NatsEventNotifications
from bike_doc_api.core.event_wakeups import (
    EventWakeups,
    install_event_wakeups,
)
from bike_doc_api.models.event import RepairSessionEvent
from bike_doc_api.repositories.events import _register_event


class FailingPublisher:
    async def publish(self, repair_session_id: str, sequence: int) -> None:
        raise ConnectionError("broker unavailable")


@pytest.mark.asyncio
async def test_commit_releases_hint_and_publisher_failure_does_not_fail_commit() -> (
    None
):
    wakeups = EventWakeups(FailingPublisher())
    install_event_wakeups(wakeups)
    queue = wakeups.subscribe("rs_test")
    try:
        async with AsyncSession() as session:
            await session.begin()
            _register_event(
                session,
                RepairSessionEvent(repair_session_id="rs_test", sequence=1),
            )
            _register_event(
                session,
                RepairSessionEvent(repair_session_id="rs_test", sequence=2),
            )
            assert queue.empty()
            await session.commit()
            assert queue.get_nowait() is None
            await asyncio.sleep(0)
    finally:
        install_event_wakeups(EventWakeups())


@pytest.mark.asyncio
async def test_rollback_discards_pending_hint() -> None:
    wakeups = EventWakeups()
    install_event_wakeups(wakeups)
    queue = wakeups.subscribe("rs_test")
    try:
        async with AsyncSession() as session:
            await session.begin()
            _register_event(
                session,
                RepairSessionEvent(repair_session_id="rs_test", sequence=1),
            )
            await session.rollback()
            await session.commit()
        assert queue.empty()
    finally:
        install_event_wakeups(EventWakeups())


@pytest.mark.asyncio
async def test_bounded_local_hint_queue() -> None:
    wakeups = EventWakeups()
    queue = wakeups.subscribe("rs_test")
    for _ in range(100):
        wakeups.receive("rs_test")
    assert queue.qsize() == 1
    wakeups.unsubscribe("rs_test", queue)


@pytest.mark.asyncio
async def test_nats_initial_failure_retries_and_subscribes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attempts = 0
    subscribed = asyncio.Event()
    wakeups = EventWakeups()
    queue = wakeups.subscribe("rs_test")

    class FakeClient:
        is_closed = False

        async def subscribe(self, subject: str, *, cb: Any) -> None:
            assert subject == SUBJECT
            await cb(
                SimpleNamespace(
                    data=json.dumps(
                        {"v": 1, "repair_session_id": "rs_test", "sequence": 2}
                    ).encode()
                )
            )
            subscribed.set()

        async def flush(self, **kwargs: Any) -> None:
            assert kwargs == {"timeout": 2}

    @asynccontextmanager
    async def fake_connection(_settings: Settings) -> AsyncIterator[FakeClient]:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise ConnectionError("broker down")
        yield FakeClient()

    monkeypatch.setattr(
        "bike_doc_api.core.event_notifications.nats_connection", fake_connection
    )
    notifications = NatsEventNotifications(Settings(environment="test"), wakeups)
    notifications.start()
    try:
        await asyncio.wait_for(subscribed.wait(), timeout=4)
        assert attempts == 2
        assert queue.get_nowait() is None
    finally:
        await notifications.close()
