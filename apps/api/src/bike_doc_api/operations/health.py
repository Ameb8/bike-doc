"""Private process-local readiness, independent of model/provider availability."""

import asyncio
import json
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

from nats.aio.client import Client
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from bike_doc_api.core.config import Settings
from bike_doc_api.core.nats import jetstream, nats_connection, permission_denied
from bike_doc_api.maintenance.runtime import JobMaintenance
from bike_doc_api.models.background_job import BackgroundJob
from bike_doc_api.workers.runtime import PullWorker


async def dependencies(
    sessions: async_sessionmaker[AsyncSession],
    settings: Settings,
    *,
    client: Client | None = None,
    workload: str | None = None,
    worker: PullWorker | None = None,
    maintenance: JobMaintenance | None = None,
) -> dict[str, bool]:
    checks = {"process": True, "database": False, "broker": False, "consumer": False}
    try:
        async with asyncio.timeout(2), sessions() as session:
            await session.execute(select(BackgroundJob.id).limit(0))
        checks["database"] = True
    except Exception:
        pass

    async def broker(connection: Client) -> None:
        if not connection.is_connected:
            return
        async with asyncio.timeout(3):
            await connection.flush(timeout=1)
            checks["broker"] = True
            # A prohibited pull and a successful empty pull both time out.
            # Historical connection errors survive reconnect in the client.
            if permission_denied(connection):
                return
            js = jetstream(connection)
            durables = (
                [settings.nats_diagnostic_consumer]
                if workload == "diagnostic"
                else [settings.nats_profile_consumer]
                if workload == "profile_inference"
                else [settings.nats_diagnostic_consumer, settings.nats_profile_consumer]
            )
            for durable in durables:
                await js.consumer_info(settings.nats_work_stream, durable)
            checks["consumer"] = True

    try:
        async with asyncio.timeout(5):
            if client is not None:
                await broker(client)
            else:
                # Read-only probes use finite initial retries and close promptly;
                # long-lived process connections retain unlimited reconnect.
                async with nats_connection(
                    settings, max_reconnect_attempts=1, drain_on_close=False
                ) as connection:
                    await broker(connection)
    except Exception:
        pass
    if worker is not None:
        checks["worker"] = worker.healthy()
    if maintenance is not None:
        checks["maintenance"] = maintenance.healthy()
    return checks


@asynccontextmanager
async def health_server(
    settings: Settings, probe: Callable[[], Awaitable[dict[str, bool]]]
) -> AsyncIterator[None]:
    """Loopback only; /live ignores dependencies, /ready returns bounded booleans."""
    if not settings.health_port:
        yield
        return

    async def respond(
        reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        try:
            async with asyncio.timeout(12):
                request = await reader.readuntil(b"\r\n\r\n")
                if len(request) > 4096:
                    return
                checks = (
                    {"process": True}
                    if request.startswith(b"GET /live ")
                    else await probe()
                )
                body = json.dumps(checks).encode()
                status = "200 OK" if all(checks.values()) else "503 Service Unavailable"
                writer.write(
                    (
                        f"HTTP/1.1 {status}\r\n"
                        "Content-Type: application/json\r\n"
                        f"Content-Length: {len(body)}\r\n"
                        "Connection: close\r\n\r\n"
                    ).encode()
                    + body
                )
                await writer.drain()
        except (Exception, asyncio.CancelledError):
            pass
        finally:
            writer.close()
            await writer.wait_closed()

    server = await asyncio.start_server(
        respond, "127.0.0.1", settings.health_port, limit=4096
    )
    try:
        yield
    finally:
        server.close()
        await server.wait_closed()
