"""Reusable NATS connection and fixed V1 JetStream topology."""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import nats
from nats.aio.client import Client
from nats.errors import ConnectionReconnectingError
from nats.js import JetStreamContext
from nats.js.api import (
    AckPolicy,
    ConsumerConfig,
    DeliverPolicy,
    RetentionPolicy,
    StorageType,
    StreamConfig,
)
from nats.js.errors import NotFoundError

from bike_doc_api.core.config import Settings


@asynccontextmanager
async def nats_connection(settings: Settings) -> AsyncIterator[Client]:
    """Connect with automatic reconnect and bounded graceful shutdown."""

    async def ignore_transport_error(_error: Exception) -> None:
        # The client default logs connection errors with full broker endpoints.
        # Callers inspect connection state or publish/fetch failures instead.
        return

    try:
        client = await nats.connect(
            servers=settings.nats_url.get_secret_value(),
            allow_reconnect=True,
            max_reconnect_attempts=-1,
            reconnect_time_wait=1,
            connect_timeout=2,
            drain_timeout=5,
            name="bike-doc-api",
            error_cb=ignore_transport_error,
        )
    except Exception:
        raise ConnectionError("NATS connection failed") from None
    try:
        yield client
    finally:
        try:
            await asyncio.wait_for(client.drain(), timeout=6)
        except (ConnectionReconnectingError, TimeoutError):
            pass
        finally:
            if not client.is_closed:
                await asyncio.wait_for(client.close(), timeout=2)


def jetstream(client: Client) -> JetStreamContext:
    """Return the JetStream API of a connected client."""
    return client.jetstream(timeout=3)


async def ensure_work_topology(
    js: JetStreamContext,
    settings: Settings,
    *,
    ack_wait: float = 30,
    max_deliver: int = 32,
) -> None:
    """Create or verify the allowlisted stream and durable pull consumers."""
    stream = StreamConfig(
        name=settings.nats_work_stream,
        subjects=[settings.nats_diagnostic_subject, settings.nats_profile_subject],
        storage=StorageType.FILE,
        retention=RetentionPolicy.WORK_QUEUE,
    )
    try:
        existing = (await js.stream_info(stream.name or "")).config
    except NotFoundError:
        await js.add_stream(config=stream)
    else:
        if (
            existing.subjects != stream.subjects
            or existing.storage != stream.storage
            or existing.retention != stream.retention
        ):
            raise ValueError("existing NATS work stream has incompatible configuration")

    for subject, durable in (
        (settings.nats_diagnostic_subject, settings.nats_diagnostic_consumer),
        (settings.nats_profile_subject, settings.nats_profile_consumer),
    ):
        config = ConsumerConfig(
            durable_name=durable,
            filter_subject=subject,
            ack_policy=AckPolicy.EXPLICIT,
            deliver_policy=DeliverPolicy.ALL,
            ack_wait=ack_wait,
            max_deliver=max_deliver,
            max_ack_pending=128,
        )
        try:
            existing_consumer = (
                await js.consumer_info(settings.nats_work_stream, durable)
            ).config
        except NotFoundError:
            await js.add_consumer(settings.nats_work_stream, config=config)
        else:
            if any(
                (
                    existing_consumer.filter_subject != config.filter_subject,
                    existing_consumer.ack_policy != config.ack_policy,
                    existing_consumer.deliver_policy != config.deliver_policy,
                    existing_consumer.ack_wait != config.ack_wait,
                    existing_consumer.max_deliver != config.max_deliver,
                    existing_consumer.max_ack_pending != config.max_ack_pending,
                    existing_consumer.deliver_subject is not None,
                )
            ):
                raise ValueError("existing NATS durable has incompatible configuration")
