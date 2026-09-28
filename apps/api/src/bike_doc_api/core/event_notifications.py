"""Core NATS event-hint transport, owned by the API process."""

import asyncio
import json
from contextlib import suppress

import structlog
from nats.aio.client import Client
from nats.aio.msg import Msg

from bike_doc_api.core.config import Settings
from bike_doc_api.core.event_wakeups import EventWakeups
from bike_doc_api.core.nats import nats_connection

SUBJECT = "bikedoc.events.wakeup.v1"
logger = structlog.get_logger(__name__)


class NatsEventNotifications:
    """One reconnecting connection and subscription for all local streams."""

    def __init__(self, settings: Settings, wakeups: EventWakeups) -> None:
        self._settings = settings
        self._wakeups = wakeups
        self._client: Client | None = None
        self._task: asyncio.Task[None] | None = None

    def start(self) -> None:
        self._task = asyncio.create_task(self._run())

    async def close(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with suppress(asyncio.CancelledError):
                await self._task

    async def publish(self, repair_session_id: str, sequence: int) -> None:
        client = self._client
        if client is None or not client.is_connected:
            return
        payload = json.dumps(
            {"v": 1, "repair_session_id": repair_session_id, "sequence": sequence},
            separators=(",", ":"),
        ).encode()
        try:
            await client.publish(SUBJECT, payload)
        except Exception:
            logger.warning("event_wakeup_publish_failed")

    async def _receive(self, message: Msg) -> None:
        try:
            hint = json.loads(message.data)
            if (
                isinstance(hint, dict)
                and set(hint) == {"v", "repair_session_id", "sequence"}
                and type(hint["v"]) is int
                and hint["v"] == 1
                and isinstance(hint["repair_session_id"], str)
                and hint["repair_session_id"].startswith("rs_")
                and type(hint["sequence"]) is int
                and hint["sequence"] > 0
            ):
                self._wakeups.receive(hint["repair_session_id"])
        except (ValueError, TypeError):
            return

    async def _run(self) -> None:
        while True:
            try:
                async with nats_connection(self._settings) as client:
                    self._client = client
                    await client.subscribe(SUBJECT, cb=self._receive)
                    await client.flush(timeout=2)
                    # The client reconnects subscriptions internally. The
                    # lifespan cancellation closes this process-owned client.
                    await asyncio.Future[None]()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.warning("event_wakeup_connection_unavailable")
            finally:
                self._client = None
            await asyncio.sleep(2)
