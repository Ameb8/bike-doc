"""Observation-only NATS adapter. No job repository or product-service dependency."""

import json
from contextlib import AsyncExitStack

from nats.aio.client import Client
from nats.aio.msg import Msg

from bike_doc_api.core.config import Settings
from bike_doc_api.core.nats import jetstream, nats_connection
from bike_doc_api.core.queue_telemetry import QueueTelemetry, queue_telemetry


class QueueMonitor:
    def __init__(
        self,
        settings: Settings,
        telemetry: QueueTelemetry = queue_telemetry,
        *,
        workload: str | None = None,
    ) -> None:
        self.settings = settings
        self.telemetry = telemetry
        self._stack = AsyncExitStack()
        self._consumers = {
            settings.nats_diagnostic_consumer: "diagnostic",
            settings.nats_profile_consumer: "profile_inference",
        }

        if workload is not None:
            self._consumers = {
                k: v for k, v in self._consumers.items() if v == workload
            }

    async def snapshot(self) -> dict[str, object]:
        # No ensure_topology: observing an absent stream must never create it.
        async with nats_connection(self.settings) as client:
            js = jetstream(client)
            stream = await js.stream_info(self.settings.nats_work_stream)
            self.telemetry.observe(
                "transport_messages",
                stream.state.messages,
                consumer="work_stream",
                transport="stored",
            )
            consumers = []
            for durable, name in self._consumers.items():
                info = await js.consumer_info(self.settings.nats_work_stream, durable)
                counts = {
                    "pending": info.num_pending,
                    "ack_pending": info.num_ack_pending,
                    "redelivered": info.num_redelivered,
                    "waiting": info.num_waiting,
                }
                for field, value in counts.items():
                    self.telemetry.observe(
                        "transport_messages", value, consumer=name, transport=field
                    )
                consumers.append({"consumer": name, **counts})
            return {
                "status": "available",
                "stored": stream.state.messages,
                "consumers": consumers,
            }

    async def subscribe(self, client: Client) -> None:
        for advisory in ("MAX_DELIVERIES", "MSG_TERMINATED"):
            for durable in self._consumers:
                await client.subscribe(
                    f"$JS.EVENT.ADVISORY.CONSUMER.{advisory}.{self.settings.nats_work_stream}.{durable}",
                    cb=self._receive,
                )

    async def start(self) -> None:
        try:
            client = await self._stack.enter_async_context(
                nats_connection(self.settings)
            )
            await self.subscribe(client)
        except BaseException:
            await self._stack.aclose()
            raise

    async def _receive(self, message: Msg) -> None:
        self.advisory(message.subject, message.data)

    def advisory(self, subject: str, data: bytes) -> None:
        # Reject unknown subjects and mismatched bodies. Never record payload fields,
        # stream sequence, headers, description, identity or broker-provided reason.
        if len(data) > 16384:
            return
        for durable, consumer in self._consumers.items():
            for suffix, category in (
                ("MAX_DELIVERIES", "max_deliver"),
                ("MSG_TERMINATED", "terminated"),
            ):
                expected = (
                    f"$JS.EVENT.ADVISORY.CONSUMER.{suffix}."
                    f"{self.settings.nats_work_stream}.{durable}"
                )
                if subject != expected:
                    continue
                try:
                    body = json.loads(data)
                    if (
                        body.get("stream") != self.settings.nats_work_stream
                        or body.get("consumer") != durable
                    ):
                        return
                except (ValueError, AttributeError):
                    return
                self.telemetry.observe("advisory", consumer=consumer, advisory=category)

    async def close(self) -> None:
        await self._stack.aclose()
