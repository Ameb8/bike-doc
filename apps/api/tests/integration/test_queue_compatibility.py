"""Explicit locked dependency/resource checks on the tuned real topology."""

import json
import os
import tomllib
import uuid
from importlib.metadata import version
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from bike_doc_api.adk.storage import open_adk_session_service
from bike_doc_api.core.config import Settings
from bike_doc_api.core.nats import ensure_work_topology, jetstream, nats_connection

ROOT = Path(__file__).resolve().parents[4]

DB_URL = os.getenv("BIKE_DOC_API_MAINTENANCE_TEST_DATABASE_URL")
NATS_URL = os.getenv("BIKE_DOC_API_MAINTENANCE_TEST_NATS_URL")
pytestmark = [
    pytest.mark.nats,
    pytest.mark.skipif(
        not DB_URL or not NATS_URL, reason="requires real infrastructure"
    ),
]


async def test_locked_clients_driver_resources_and_tuned_topology() -> None:
    assert DB_URL and NATS_URL
    locked = {
        package["name"]: package["version"]
        for package in tomllib.loads((ROOT / "uv.lock").read_text())["package"]
    }
    for package in ("nats-py", "asyncpg", "sqlalchemy", "google-adk"):
        assert version(package) == locked[package]
    assert version("nats-py") == "2.12.0"
    assert version("google-adk") == "2.3.0"
    budget = json.loads((ROOT / "deploy/capacity.json").read_text())
    settings = Settings(environment="test", database_url=DB_URL, nats_url=NATS_URL)
    for role in budget.values():
        for name, value in role.items():
            assert getattr(settings, name) == value
    engine = create_async_engine(
        DB_URL,
        pool_size=settings.maintenance_pool_size,
        max_overflow=0,
        pool_timeout=settings.database_pool_timeout_seconds,
    )
    service = await open_adk_session_service(settings)
    try:
        assert engine.dialect.driver == "asyncpg"
        async with engine.connect() as connection:
            assert (
                int(await connection.scalar(text("SHOW server_version_num"))) // 10000
                == 16
            )
            assert await connection.scalar(text("SELECT 1")) == 1
        assert engine.sync_engine.pool.checkedout() == 0
        async with service.db_engine.connect() as connection:
            assert (
                await connection.scalar(
                    text(
                        "SELECT value FROM adk_internal_metadata "
                        "WHERE key='schema_version'"
                    )
                )
                == "1"
            )
        assert service.db_engine.sync_engine.pool.checkedout() == 0
        suffix = uuid.uuid4().hex[:12]
        topology = settings.model_copy(
            update={
                "nats_work_stream": f"CERTIFICATION_{suffix}",
                "nats_diagnostic_subject": f"certification.{suffix}.diagnostic",
                "nats_profile_subject": f"certification.{suffix}.profile",
                "nats_diagnostic_consumer": f"diagnostic_{suffix}",
                "nats_profile_consumer": f"profile_{suffix}",
            }
        )
        async with nats_connection(topology) as client:
            server = client.connected_server_version
            assert (server.major, server.minor, server.patch) == (2, 12, 1)
            js = jetstream(client)
            await ensure_work_topology(js, topology)
            try:
                await ensure_work_topology(js, topology)
                stream = await js.stream_info(topology.nats_work_stream)
                assert stream.config.storage == "file"
                assert stream.config.max_age == settings.nats_work_max_age_seconds
                assert stream.state.messages == 0
                for consumer_name, limit in (
                    (
                        topology.nats_diagnostic_consumer,
                        settings.nats_diagnostic_max_ack_pending,
                    ),
                    (
                        topology.nats_profile_consumer,
                        settings.nats_profile_max_ack_pending,
                    ),
                ):
                    consumer = await js.consumer_info(
                        topology.nats_work_stream, consumer_name
                    )
                    assert consumer.config.max_ack_pending == limit
                    assert consumer.config.ack_wait == settings.nats_ack_wait_seconds
                    assert consumer.num_pending == consumer.num_ack_pending == 0
            finally:
                await js.delete_stream(topology.nats_work_stream)
        assert client.is_closed
    finally:
        await service.close()
        await engine.dispose()
    assert engine.sync_engine.pool.checkedout() == 0
    assert service.db_engine.sync_engine.pool.checkedout() == 0
