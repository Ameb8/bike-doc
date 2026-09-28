"""Opt-in real-broker compatibility contract. Run with task test:nats."""

import asyncio
import socket
import subprocess
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager

import pytest

from bike_doc_api.core.config import Settings
from bike_doc_api.core.nats import ensure_work_topology, jetstream, nats_connection

IMAGE = "nats:2.12.1-alpine"


def docker(*args: str) -> str:
    result = subprocess.run(
        ["docker", *args], capture_output=True, text=True, timeout=30, check=False
    )
    if result.returncode:
        raise RuntimeError("Docker broker operation failed")
    return result.stdout.strip()


@contextmanager
def broker() -> Iterator[tuple[str, str]]:
    name = f"bike-doc-nats-compat-{uuid.uuid4().hex[:12]}"
    volume = f"{name}-data"
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    docker("volume", "create", volume)
    try:
        docker(
            "run",
            "-d",
            "--name",
            name,
            "-p",
            f"127.0.0.1:{port}:4222",
            "-v",
            f"{volume}:/data",
            IMAGE,
            "-js",
            "-sd",
            "/data",
            "-m",
            "8222",
        )
        try:
            yield name, f"nats://127.0.0.1:{port}"
        finally:
            docker("rm", "-f", name)
    finally:
        docker("volume", "rm", volume)


async def wait_connected(url: str) -> None:
    for _ in range(40):
        try:
            async with nats_connection(Settings(nats_url=url)):
                return
        except (ConnectionError, TimeoutError):
            await asyncio.sleep(0.1)
    raise AssertionError("broker did not become available")


@pytest.mark.nats
@pytest.mark.asyncio
async def test_pinned_jetstream_transport_contract() -> None:
    with broker() as (container, url):
        await wait_connected(url)
        suffix = uuid.uuid4().hex[:10]
        settings = Settings(
            nats_url=url,
            nats_work_stream=f"BIKEDOC_TEST_{suffix}",
            nats_diagnostic_subject=f"bikedoc.test.{suffix}.diagnostic",
            nats_profile_subject=f"bikedoc.test.{suffix}.profile",
            nats_diagnostic_consumer=f"diagnostic_{suffix}",
            nats_profile_consumer=f"profile_{suffix}",
        )
        async with nats_connection(settings) as nc:
            js = jetstream(nc)
            await ensure_work_topology(js, settings, ack_wait=0.4, max_deliver=4)
            await ensure_work_topology(js, settings, ack_wait=0.4, max_deliver=4)
            info = await js.stream_info(settings.nats_work_stream)
            assert info.config.storage == "file"
            assert set(info.config.subjects or []) == {
                settings.nats_diagnostic_subject,
                settings.nats_profile_subject,
            }
            with pytest.raises(ValueError, match="incompatible"):
                await ensure_work_topology(js, settings, ack_wait=0.5, max_deliver=4)
            with pytest.raises(ValueError, match="incompatible"):
                await ensure_work_topology(
                    js,
                    settings.model_copy(
                        update={"nats_profile_subject": "bikedoc.test.other"}
                    ),
                    ack_wait=0.4,
                    max_deliver=4,
                )
            ack = await js.publish(settings.nats_diagnostic_subject, b"v1")
            assert ack.stream == settings.nats_work_stream
            assert ack.seq > 0

            await asyncio.to_thread(docker, "restart", container)
            for _ in range(60):
                if nc.is_connected:
                    break
                await asyncio.sleep(0.1)
            assert nc.is_connected
            js = jetstream(nc)
            assert (await js.stream_info(settings.nats_work_stream)).state.messages == 1
            sub = await js.pull_subscribe(
                settings.nats_diagnostic_subject,
                durable=settings.nats_diagnostic_consumer,
                stream=settings.nats_work_stream,
            )
            first = (await sub.fetch(1, timeout=3))[0]
            assert first.data == b"v1"
            assert first.metadata.num_delivered == 1
            await first.ack()
            await asyncio.sleep(0.1)
            assert (
                await js.consumer_info(
                    settings.nats_work_stream, settings.nats_diagnostic_consumer
                )
            ).num_ack_pending == 0

            await js.publish(settings.nats_diagnostic_subject, b"nak")
            delayed = (await sub.fetch(1, timeout=2))[0]
            nak_at = time.monotonic()
            await delayed.nak(delay=0.3)
            redelivered = (await sub.fetch(1, timeout=2))[0]
            assert time.monotonic() - nak_at >= 0.25
            assert redelivered.data == b"nak"
            assert redelivered.metadata.num_delivered == 2
            await redelivered.ack_sync(timeout=2)

            await js.publish(settings.nats_diagnostic_subject, b"progress")
            progress = (await sub.fetch(1, timeout=2))[0]
            for _ in range(2):
                await asyncio.sleep(0.25)
                await progress.in_progress()
            assert (
                await js.consumer_info(
                    settings.nats_work_stream, settings.nats_diagnostic_consumer
                )
            ).num_ack_pending == 1
            await progress.ack_sync(timeout=2)

            await js.publish(settings.nats_diagnostic_subject, b"limit")
            for delivery in range(1, 5):
                msg = (await sub.fetch(1, timeout=2))[0]
                assert msg.data == b"limit"
                assert msg.metadata.num_delivered == delivery
            await asyncio.sleep(0.5)
            consumer = await js.consumer_info(
                settings.nats_work_stream, settings.nats_diagnostic_consumer
            )
            assert consumer.delivered.consumer_seq >= 7
            with pytest.raises(TimeoutError):
                await sub.fetch(1, timeout=0.4)
            await js.delete_stream(settings.nats_work_stream)
        assert nc.is_closed
