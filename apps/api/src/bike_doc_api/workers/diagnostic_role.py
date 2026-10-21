"""Independent diagnostic process host over the shared pull runtime."""

import asyncio
import signal
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from bike_doc_api.adk.execution import DiagnosticResources
from bike_doc_api.adk.resources import diagnostic_resources
from bike_doc_api.core.config import Settings, get_settings
from bike_doc_api.core.event_notifications import NatsEventPublisher
from bike_doc_api.core.event_wakeups import (
    EventWakeups,
    get_event_wakeups,
    install_event_wakeups,
)
from bike_doc_api.core.logging import configure_logging
from bike_doc_api.core.nats import ensure_work_topology, jetstream, nats_connection
from bike_doc_api.core.telemetry import initialize_telemetry
from bike_doc_api.db.pool_observation import observe_pool
from bike_doc_api.models.background_job import BackgroundJob
from bike_doc_api.operations.health import dependencies, health_server
from bike_doc_api.operations.monitoring import QueueMonitor
from bike_doc_api.repositories.diagnostic_jobs import DiagnosticJobRepository
from bike_doc_api.workers.adapters import (
    NatsPullTransport,
    PostgresJobStore,
)
from bike_doc_api.workers.diagnostic import (
    DiagnosticTurnHandler,
    diagnostic_policy,
    diagnostic_registry,
)
from bike_doc_api.workers.diagnostic_application import DiagnosticExecutionApplication
from bike_doc_api.workers.role import create_workload_worker
from bike_doc_api.workers.runtime import PullWorker


@asynccontextmanager
async def diagnostic_worker(
    settings: Settings,
    *,
    resources: DiagnosticResources,
) -> AsyncIterator[PullWorker]:
    """Own product pool and NATS; transports may be faked without replacing runtime."""
    diagnostic_policy(settings)
    engine = create_async_engine(
        settings.database_url,
        pool_pre_ping=True,
        pool_size=settings.diagnostic_worker_pool_size,
        max_overflow=0,
        pool_timeout=settings.database_pool_timeout_seconds,
    )
    try:
        observe_pool(engine, "worker")
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        application = DiagnosticExecutionApplication(sessions, settings, resources)
        registry = diagnostic_registry(
            settings,
            DiagnosticTurnHandler(
                application, settings.diagnostic_worker_retry_seconds
            ),
        )
        async with sessions() as session:
            await session.execute(select(BackgroundJob.id).limit(0))
        async with nats_connection(settings) as client:
            js = jetstream(client)
            await ensure_work_topology(js, settings, workload="diagnostic")
            subscription = await js.pull_subscribe(
                settings.nats_diagnostic_subject,
                durable=settings.nats_diagnostic_consumer,
                stream=settings.nats_work_stream,
                inbox_prefix=settings.nats_inbox_prefix.encode() + b".",
            )
            await QueueMonitor(settings, workload="diagnostic").subscribe(client)
            runtime = create_workload_worker(
                settings,
                registry,
                PostgresJobStore(sessions, repository_factory=DiagnosticJobRepository),
                NatsPullTransport(subscription, client=client),
            )
            previous_wakeups = get_event_wakeups()
            wakeups = EventWakeups(NatsEventPublisher(client))
            install_event_wakeups(wakeups)
            try:
                async with health_server(
                    settings,
                    lambda: dependencies(
                        sessions,
                        settings,
                        client=client,
                        workload="diagnostic",
                        worker=runtime,
                    ),
                ):
                    yield runtime
            finally:
                try:
                    await runtime.close()
                finally:
                    await wakeups.drain()
                    install_event_wakeups(previous_wakeups)
    finally:
        async with asyncio.timeout(5):
            await engine.dispose()


async def run_diagnostic_role(settings: Settings) -> None:
    diagnostic_policy(settings)
    telemetry = initialize_telemetry(settings)
    try:
        async with diagnostic_resources(settings) as resources:
            stop = asyncio.Event()
            loop = asyncio.get_running_loop()
            for sig in (signal.SIGINT, signal.SIGTERM):
                loop.add_signal_handler(sig, stop.set)
            try:
                async with diagnostic_worker(settings, resources=resources) as runtime:
                    runtime.start()
                    structlog.get_logger(__name__).info(
                        "diagnostic_worker_ready",
                        workload="diagnostic",
                        input_version=1,
                    )
                    await stop.wait()
            finally:
                for sig in (signal.SIGINT, signal.SIGTERM):
                    loop.remove_signal_handler(sig)
    finally:
        await telemetry.shutdown()


def main() -> None:
    try:
        settings = get_settings()
        configure_logging(
            environment=settings.environment,
            log_level=settings.log_level,
            log_format=settings.log_format,
        )
        asyncio.run(run_diagnostic_role(settings))
    except Exception:
        structlog.get_logger(__name__).error("diagnostic_worker_process_failed")
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
