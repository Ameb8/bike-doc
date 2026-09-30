"""Official JetStream adapter with lazy connection and bounded acknowledgement."""

import asyncio
from contextlib import AsyncExitStack

from nats.js import JetStreamContext

from bike_doc_api.core.config import Settings
from bike_doc_api.core.nats import ensure_work_topology, jetstream, nats_connection


class NatsJobPublisher:
    """Share one reconnecting connection; never connect during API startup."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._stack = AsyncExitStack()
        self._js: JetStreamContext | None = None
        self._lock = asyncio.Lock()

    async def publish(self, subject: str, payload: bytes, message_id: str) -> None:
        async with self._lock:
            if self._js is None:
                # Keep partially initialized resources owned for cancellation.
                client = await self._stack.enter_async_context(
                    nats_connection(self._settings)
                )
                js = jetstream(client)
                try:
                    await ensure_work_topology(js, self._settings)
                except BaseException:
                    await self._stack.aclose()
                    raise
                self._js = js
            js = self._js
        ack = await js.publish(
            subject,
            payload,
            headers={"Nats-Msg-Id": message_id},
            timeout=self._settings.job_publish_timeout_seconds,
        )
        if ack.stream != self._settings.nats_work_stream or ack.seq < 1:
            raise ValueError("unexpected publication acknowledgement")

    async def close(self) -> None:
        await self._stack.aclose()
        self._js = None
