"""Isolated lifecycle coverage with a fake client."""

import pytest

from bike_doc_api.core.config import Settings
from bike_doc_api.core.nats import nats_connection


class FakeClient:
    is_closed = False
    drained = False

    async def drain(self) -> None:
        self.drained = True
        self.is_closed = True

    async def close(self) -> None:
        self.is_closed = True


@pytest.mark.asyncio
async def test_connection_drains_on_exit(monkeypatch: pytest.MonkeyPatch) -> None:
    client = FakeClient()

    async def connect(**_kwargs: object) -> FakeClient:
        return client

    monkeypatch.setattr("bike_doc_api.core.nats.nats.connect", connect)
    async with nats_connection(Settings()) as connected:
        assert connected is client
    assert client.drained
    assert client.is_closed


@pytest.mark.asyncio
async def test_connection_error_does_not_reveal_endpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def connect(**_kwargs: object) -> FakeClient:
        raise OSError("nats://secret:password@example.com:4222")

    monkeypatch.setattr("bike_doc_api.core.nats.nats.connect", connect)
    with pytest.raises(ConnectionError) as exc:
        async with nats_connection(
            Settings(nats_url="nats://secret:password@example.com:4222")
        ):
            pass
    assert "password" not in str(exc.value)


async def test_connection_context_accepts_worker_owned_drain(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = FakeClient()
    calls = 0

    async def drain() -> None:
        nonlocal calls
        calls += 1
        client.is_closed = True

    client.drain = drain

    async def connect(**kwargs: object) -> FakeClient:
        return client

    monkeypatch.setattr("bike_doc_api.core.nats.nats.connect", connect)
    async with nats_connection(Settings()) as connected:
        await connected.drain()
    assert calls == 1 and client.is_closed


async def test_worker_verification_is_scoped_and_cannot_provision() -> None:
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from nats.js.api import (
        AckPolicy,
        ConsumerConfig,
        DeliverPolicy,
        RetentionPolicy,
        StorageType,
        StreamConfig,
    )
    from nats.js.errors import NotFoundError

    from bike_doc_api.core.nats import ensure_work_topology

    settings = Settings()
    js = AsyncMock()
    js.stream_info.return_value = SimpleNamespace(
        config=StreamConfig(
            subjects=[settings.nats_diagnostic_subject, settings.nats_profile_subject],
            storage=StorageType.FILE,
            retention=RetentionPolicy.WORK_QUEUE,
            max_age=settings.nats_work_max_age_seconds,
        )
    )
    js.consumer_info.return_value = SimpleNamespace(
        config=ConsumerConfig(
            filter_subject=settings.nats_profile_subject,
            ack_policy=AckPolicy.EXPLICIT,
            deliver_policy=DeliverPolicy.ALL,
            ack_wait=30,
            max_deliver=32,
            max_ack_pending=settings.nats_profile_max_ack_pending,
        )
    )
    await ensure_work_topology(js, settings, workload="profile_inference")
    js.consumer_info.assert_awaited_once_with(
        settings.nats_work_stream, settings.nats_profile_consumer
    )
    js.add_stream.assert_not_awaited()
    js.add_consumer.assert_not_awaited()
    js.consumer_info.side_effect = NotFoundError
    with pytest.raises(ValueError, match="provisioned before workers"):
        await ensure_work_topology(js, settings, workload="profile_inference")
    js.add_consumer.assert_not_awaited()
    js.stream_info.side_effect = NotFoundError
    with pytest.raises(ValueError, match="provisioned before workers"):
        await ensure_work_topology(js, settings, workload="profile_inference")
    js.add_stream.assert_not_awaited()


async def test_read_only_probe_can_close_without_draining_and_limit_retries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = FakeClient()
    options = {}

    async def connect(**kwargs: object) -> FakeClient:
        options.update(kwargs)
        return client

    monkeypatch.setattr("bike_doc_api.core.nats.nats.connect", connect)
    async with nats_connection(
        Settings(), max_reconnect_attempts=1, drain_on_close=False
    ):
        pass
    assert options["max_reconnect_attempts"] == 1
    assert client.is_closed and not client.drained
