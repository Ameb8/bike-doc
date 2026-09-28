"""Real PostgreSQL/Core NATS cross-process SSE check.

Run after migrating a disposable database::

    BIKE_DOC_API_EVENT_TEST_DATABASE_URL=<postgresql+asyncpg URL> \
      BIKE_DOC_API_EVENT_TEST_NATS_URL=<nats URL> \
      uv run pytest -m nats tests/integration/test_cross_process_event_wakeups.py

The reader and writer are separate Python interpreters and database sessions.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
import uuid

import pytest
from sqlalchemy import delete

from bike_doc_api.core.config import Settings
from bike_doc_api.core.event_notifications import SUBJECT, NatsEventNotifications
from bike_doc_api.core.event_wakeups import EventWakeups, install_event_wakeups
from bike_doc_api.core.nats import nats_connection
from bike_doc_api.db.session import get_sessionmaker
from bike_doc_api.models.bike import BikeProfile
from bike_doc_api.models.event import RepairSessionEvent
from bike_doc_api.models.repair_session import RepairSession
from bike_doc_api.models.user import User
from bike_doc_api.repositories.events import RepairSessionEventRepository
from bike_doc_api.repositories.repair_sessions import RepairSessionRepository
from bike_doc_api.services.events import EventService, EventStream

DB_URL = os.getenv("BIKE_DOC_API_EVENT_TEST_DATABASE_URL")
NATS_URL = os.getenv("BIKE_DOC_API_EVENT_TEST_NATS_URL")
pytestmark = pytest.mark.skipif(
    DB_URL is None or NATS_URL is None, reason="requires migrated PostgreSQL and NATS"
)


async def _child(role: str, db_url: str, nats_url: str, session_id: str) -> None:
    settings = Settings(environment="test", database_url=db_url, nats_url=nats_url)
    wakeups = EventWakeups()
    install_event_wakeups(wakeups)
    notifications = NatsEventNotifications(settings, wakeups)
    wakeups.publisher = notifications
    if role in {"reader", "writer", "writer_add"}:
        notifications.start()
    try:
        async with get_sessionmaker(db_url)() as session:
            service = EventService(
                RepairSessionEventRepository(session),
                RepairSessionRepository(session),
                commit=session.commit,
                rollback=session.rollback,
                wakeups=wakeups,
                poll_interval_seconds=8 if role == "reader" else 1,
            )
            if role.startswith("reader"):
                frames = service.stream_sse_frames(EventStream(session_id, 0, 12))
                frame_task = asyncio.create_task(anext(frames))
                await asyncio.sleep(0.5)
                print("READY", flush=True)
                frame = await asyncio.wait_for(frame_task, timeout=10)
                print(json.dumps({"frame": frame}), flush=True)
                await frames.aclose()
            else:
                print("READY", flush=True)
                await asyncio.to_thread(sys.stdin.readline)
                if role == "writer_add":
                    repair_session = await session.get(RepairSession, session_id)
                    assert repair_session is not None
                    repair_session.latest_event_sequence = 1
                    await RepairSessionEventRepository(session).add(
                        RepairSessionEvent(
                            repair_session_id=session_id,
                            sequence=1,
                            type="assistant.delta",
                            data={"text": "private user-authored bicycle content"},
                        )
                    )
                    await session.commit()
                else:
                    await service.append_event(
                        repair_session_id=session_id,
                        event_type="assistant.delta",
                        data={"text": "private user-authored bicycle content"},
                    )
                await asyncio.sleep(0.2)
                print("COMMITTED", flush=True)
    finally:
        await notifications.close()


async def _process(*args: str) -> asyncio.subprocess.Process:
    return await asyncio.create_subprocess_exec(
        sys.executable,
        __file__,
        *args,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )


@pytest.mark.nats
@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("hinted", "direct"),
    [(True, False), (True, True), (False, False)],
    ids=["core-nats-append", "core-nats-product-transaction", "poll-only"],
)
async def test_cross_process_event_delivery(hinted: bool, direct: bool) -> None:
    assert DB_URL is not None and NATS_URL is not None
    suffix = uuid.uuid4().hex
    user_id, bike_id, session_id = f"usr_{suffix}", f"bike_{suffix}", f"rs_{suffix}"
    maker = get_sessionmaker(DB_URL)
    async with maker() as db:
        db.add(
            User(
                id=user_id,
                auth_subject=suffix,
                email="test@example.invalid",
                display_name="Test",
            )
        )
        await db.flush()
        db.add(BikeProfile(id=bike_id, user_id=user_id, display_name="Test bike"))
        await db.flush()
        db.add(RepairSession(id=session_id, user_id=user_id, bike_id=bike_id))
        await db.commit()
    try:
        async with nats_connection(Settings(nats_url=NATS_URL)) as nc:
            observer = await nc.subscribe(SUBJECT)
            await nc.flush()
            writer = await _process(
                "writer_add" if direct else ("writer" if hinted else "writer_poll"),
                DB_URL,
                NATS_URL,
                session_id,
            )
            assert writer.stdout is not None and writer.stdin is not None
            assert (
                await asyncio.wait_for(writer.stdout.readline(), 8)
            ).strip() == b"READY"
            reader = await _process(
                "reader" if hinted else "reader_poll", DB_URL, NATS_URL, session_id
            )
            assert reader.stdout is not None
            assert (
                await asyncio.wait_for(reader.stdout.readline(), 6)
            ).strip() == b"READY"
            started = time.monotonic()
            writer.stdin.write(b"GO\n")
            await writer.stdin.drain()
            output, errors = await asyncio.wait_for(writer.communicate(), 8)
            assert writer.returncode == 0, errors.decode()
            assert b"COMMITTED" in output
            if hinted:
                message = await asyncio.wait_for(observer.next_msg(), 4)
                assert message.subject == SUBJECT
                assert not message.headers
                assert json.loads(message.data) == {
                    "v": 1,
                    "repair_session_id": session_id,
                    "sequence": 1,
                }
                for prohibited in (
                    b"private",
                    b"text",
                    b"email",
                    b"artifact",
                    b"user_id",
                    b"secret",
                ):
                    assert prohibited not in message.data
            line = await asyncio.wait_for(reader.stdout.readline(), 10)
            elapsed = time.monotonic() - started
            assert json.loads(line)["frame"].startswith(
                "id: 1\nevent: assistant.delta\n"
            )
            assert elapsed < (4 if hinted else 5)
            _, errors = await asyncio.wait_for(reader.communicate(), 5)
            assert reader.returncode == 0, errors.decode()
            if not hinted:
                with pytest.raises(TimeoutError):
                    await asyncio.wait_for(observer.next_msg(), 0.2)
    finally:
        async with maker() as db:
            await db.execute(
                delete(RepairSessionEvent).where(
                    RepairSessionEvent.repair_session_id == session_id
                )
            )
            await db.execute(
                delete(RepairSession).where(RepairSession.id == session_id)
            )
            await db.execute(delete(BikeProfile).where(BikeProfile.id == bike_id))
            await db.execute(delete(User).where(User.id == user_id))
            await db.commit()
        await maker.kw["bind"].dispose()


if __name__ == "__main__":
    asyncio.run(_child(*sys.argv[1:]))
