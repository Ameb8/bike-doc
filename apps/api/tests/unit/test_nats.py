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
