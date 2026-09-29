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
import re
import sys
import time
import uuid
from contextlib import suppress
from dataclasses import dataclass, field

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

READINESS_TIMEOUT_SECONDS = 30
HINT_DELIVERY_TIMEOUT_SECONDS = 4
POLL_DELIVERY_TIMEOUT_SECONDS = 5
PROTOCOL_TIMEOUT_SECONDS = 8
SHUTDOWN_TIMEOUT_SECONDS = 3
OUTPUT_LIMIT_BYTES = 512
POLL_INTERVAL_SECONDS = 2
HINTED_POLL_INTERVAL_SECONDS = 8


@dataclass
class ChildProcess:
    role: str
    process: asyncio.subprocess.Process
    stderr_task: asyncio.Task[bytes]
    stdout_lines: list[bytes] = field(default_factory=list)


async def _bounded_output(stream: asyncio.StreamReader) -> bytes:
    """Drain a pipe while retaining only a small diagnostic sample."""
    sample = bytearray()
    while chunk := await stream.read(4096):
        sample.extend(chunk[: max(0, OUTPUT_LIMIT_BYTES - len(sample))])
    return bytes(sample)


async def _spawn(
    children: list[ChildProcess], role: str, *command: str
) -> ChildProcess:
    process = await asyncio.create_subprocess_exec(
        *command,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    assert process.stderr is not None
    child = ChildProcess(
        role, process, asyncio.create_task(_bounded_output(process.stderr))
    )
    children.append(child)
    return child


def _diagnostic(child: ChildProcess, expected: str, phase: str) -> str:
    # Child output may include event data, identifiers, paths, or connection URLs.
    # Only the fixed protocol vocabulary is safe to show verbatim.
    allowed = {b"READY writer", b"READY reader", b"COMMITTED writer"}
    stdout = [
        line.decode("ascii") if line in allowed else "[redacted line]"
        for line in child.stdout_lines[-8:]
    ]
    stderr = child.stderr_task.result() if child.stderr_task.done() else b""
    safe_errors = {
        b"AssertionError",
        b"ConnectionError",
        b"RuntimeError",
        b"TimeoutError",
        b"TypeError",
        b"ValueError",
    }
    exception_names = re.findall(rb"\b[A-Za-z_]+(?:Error|Exception)\b", stderr)
    safe_names = [name for name in exception_names if name in safe_errors]
    error_type = safe_names[-1].decode("ascii") if safe_names else "unknown"
    return (
        f"phase={phase} role={child.role} expected={expected} "
        f"returncode={child.process.returncode} stdout={stdout!r} "
        f"stderr=[redacted {len(stderr)} bytes; exception={error_type}]"
    )


async def _expect(
    child: ChildProcess, expected: str, phase: str, timeout_seconds: float
) -> bytes:
    assert child.process.stdout is not None
    try:
        line = await asyncio.wait_for(child.process.stdout.readline(), timeout_seconds)
    except TimeoutError as exc:
        raise AssertionError(_diagnostic(child, expected, f"{phase} timeout")) from exc
    stripped = line.strip()
    child.stdout_lines.append(stripped[:OUTPUT_LIMIT_BYTES])
    if stripped != expected.encode():
        raise AssertionError(_diagnostic(child, expected, phase))
    return stripped


async def _wait_exit(child: ChildProcess, phase: str) -> None:
    try:
        await asyncio.wait_for(child.process.wait(), PROTOCOL_TIMEOUT_SECONDS)
    except TimeoutError as exc:
        raise AssertionError(_diagnostic(child, "exit 0", f"{phase} timeout")) from exc
    if child.process.returncode != 0:
        raise AssertionError(_diagnostic(child, "exit 0", phase))


async def _stop(child: ChildProcess) -> None:
    process = child.process
    if process.stdin is not None and not process.stdin.is_closing():
        process.stdin.close()
    if process.returncode is None:
        with suppress(ProcessLookupError):
            process.terminate()
    try:
        await asyncio.wait_for(process.wait(), SHUTDOWN_TIMEOUT_SECONDS)
    except TimeoutError:
        with suppress(ProcessLookupError):
            process.kill()
        await process.wait()
    await child.stderr_task


async def _frame_line(child: ChildProcess, timeout_seconds: float) -> bytes:
    assert child.process.stdout is not None
    try:
        line = await asyncio.wait_for(child.process.stdout.readline(), timeout_seconds)
    except TimeoutError as exc:
        raise AssertionError(
            _diagnostic(child, "SSE frame", "delivery timeout")
        ) from exc
    if not line:
        with suppress(TimeoutError):
            await asyncio.wait_for(child.process.wait(), SHUTDOWN_TIMEOUT_SECONDS)
        raise AssertionError(_diagnostic(child, "SSE frame", "delivery EOF"))
    child.stdout_lines.append(line.strip()[:OUTPUT_LIMIT_BYTES])
    return line


async def _stop_all(children: list[ChildProcess]) -> None:
    # Shield cleanup so a cancelled test still reaps every process it created.
    async def stop_children() -> None:
        results = await asyncio.gather(
            *(_stop(child) for child in children), return_exceptions=True
        )
        for result in results:
            if isinstance(result, BaseException):
                raise result

    cleanup = asyncio.create_task(stop_children())
    cancelled = False
    while not cleanup.done():
        try:
            await asyncio.shield(cleanup)
        except asyncio.CancelledError:
            cancelled = True
    await cleanup
    if cancelled:
        raise asyncio.CancelledError


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
                poll_interval_seconds=(
                    HINTED_POLL_INTERVAL_SECONDS
                    if role == "reader"
                    else POLL_INTERVAL_SECONDS
                ),
            )
            if role.startswith("reader"):
                frames = service.stream_sse_frames(EventStream(session_id, 0, 12))
                frame_task = asyncio.create_task(anext(frames))
                await asyncio.sleep(0.5)
                print("READY reader", flush=True)
                frame = await asyncio.wait_for(frame_task, timeout=10)
                print(json.dumps({"frame": frame}), flush=True)
                await frames.aclose()
            else:
                print("READY writer", flush=True)
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
                print("COMMITTED writer", flush=True)
    finally:
        await notifications.close()


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
    children: list[ChildProcess] = []
    try:
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
        async with nats_connection(Settings(nats_url=NATS_URL)) as nc:
            observer = await nc.subscribe(SUBJECT)
            await nc.flush()
            writer = await _spawn(
                children,
                "writer",
                sys.executable,
                __file__,
                "writer_add" if direct else ("writer" if hinted else "writer_poll"),
                DB_URL,
                NATS_URL,
                session_id,
            )
            await _expect(
                writer, "READY writer", "writer readiness", READINESS_TIMEOUT_SECONDS
            )
            reader = await _spawn(
                children,
                "reader",
                sys.executable,
                __file__,
                "reader" if hinted else "reader_poll",
                DB_URL,
                NATS_URL,
                session_id,
            )
            await _expect(
                reader, "READY reader", "reader readiness", READINESS_TIMEOUT_SECONDS
            )
            assert writer.process.stdin is not None
            started = time.monotonic()
            writer.process.stdin.write(b"GO\n")
            await writer.process.stdin.drain()
            await _expect(
                writer, "COMMITTED writer", "writer commit", PROTOCOL_TIMEOUT_SECONDS
            )
            await _wait_exit(writer, "writer exit")
            if hinted:
                try:
                    message = await asyncio.wait_for(
                        observer.next_msg(), HINT_DELIVERY_TIMEOUT_SECONDS
                    )
                except TimeoutError as exc:
                    raise AssertionError(
                        _diagnostic(writer, "Core NATS hint", "hint delivery timeout")
                    ) from exc
                expected_hint = {
                    "v": 1,
                    "repair_session_id": session_id,
                    "sequence": 1,
                }
                try:
                    valid_body = json.loads(message.data) == expected_hint
                except ValueError:
                    valid_body = False
                if message.subject != SUBJECT or message.headers or not valid_body:
                    raise AssertionError(
                        "Core NATS hint subject, headers, or body invalid"
                    )
                for prohibited in (
                    b"private",
                    b"text",
                    b"email",
                    b"artifact",
                    b"user_id",
                    b"secret",
                ):
                    if prohibited in message.data:
                        raise AssertionError(
                            "Core NATS hint contains prohibited content"
                        )
            line = await _frame_line(
                reader,
                HINT_DELIVERY_TIMEOUT_SECONDS
                if hinted
                else POLL_DELIVERY_TIMEOUT_SECONDS,
            )
            elapsed = time.monotonic() - started
            try:
                valid_frame = json.loads(line)["frame"].startswith(
                    "id: 1\nevent: assistant.delta\n"
                )
            except (ValueError, KeyError, TypeError):
                valid_frame = False
            if not valid_frame:
                raise AssertionError(
                    _diagnostic(reader, "SSE frame", "frame validation")
                )
            assert elapsed < (
                HINT_DELIVERY_TIMEOUT_SECONDS
                if hinted
                else POLL_DELIVERY_TIMEOUT_SECONDS
            )
            await _wait_exit(reader, "reader exit")
            if not hinted:
                with pytest.raises(TimeoutError):
                    await asyncio.wait_for(observer.next_msg(), 0.2)
    finally:
        try:
            await _stop_all(children)
        finally:
            try:
                async with maker() as db:
                    await db.execute(
                        delete(RepairSessionEvent).where(
                            RepairSessionEvent.repair_session_id == session_id
                        )
                    )
                    await db.execute(
                        delete(RepairSession).where(RepairSession.id == session_id)
                    )
                    await db.execute(
                        delete(BikeProfile).where(BikeProfile.id == bike_id)
                    )
                    await db.execute(delete(User).where(User.id == user_id))
                    await db.commit()
            finally:
                await maker.kw["bind"].dispose()


@pytest.mark.nats
@pytest.mark.asyncio
async def test_protocol_failure_redacts_secrets_and_reaps_child() -> None:
    secret = "postgresql+asyncpg://user:secret-password@localhost/private"
    children: list[ChildProcess] = []
    child = await _spawn(
        children,
        "writer",
        sys.executable,
        "-c",
        "import sys; print('WRONG', flush=True); print(sys.argv[1], file=sys.stderr)",
        secret,
    )
    try:
        with pytest.raises(AssertionError) as failure:
            await _expect(
                child, "READY writer", "writer readiness", PROTOCOL_TIMEOUT_SECONDS
            )
        message = str(failure.value)
        assert "role=writer" in message
        assert "phase=writer readiness" in message
        assert "expected=READY writer" in message
        assert "stdout=['[redacted line]']" in message
        assert secret not in message
        assert "secret-password" not in message
    finally:
        await _stop_all(children)
    assert child.process.returncode is not None
    assert child.stderr_task.done()

    hung_children: list[ChildProcess] = []
    hung = await _spawn(
        hung_children,
        "reader",
        sys.executable,
        "-c",
        "import sys,time;print(sys.argv[1],file=sys.stderr,flush=True);time.sleep(60)",
        secret,
    )
    try:
        with pytest.raises(AssertionError) as failure:
            await _expect(hung, "READY reader", "reader readiness", 0.2)
        message = str(failure.value)
        assert "role=reader" in message
        assert "phase=reader readiness timeout" in message
        assert "expected=READY reader" in message
        assert secret not in message
    finally:
        await _stop_all(hung_children)
    assert hung.process.returncode is not None


if __name__ == "__main__":
    asyncio.run(_child(*sys.argv[1:]))
