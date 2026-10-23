"""API composition of maintenance resources, independent of route handlers."""

import asyncio
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager

from bike_doc_api.core.config import Settings
from bike_doc_api.core.queue_telemetry import queue_telemetry
from bike_doc_api.db.session import get_sessionmaker
from bike_doc_api.maintenance.nats_publisher import NatsJobPublisher
from bike_doc_api.maintenance.runtime import JobMaintenance, ReconciliationPolicy
from bike_doc_api.operations.monitoring import QueueMonitor
from bike_doc_api.repositories.background_jobs import BackgroundJobRepository
from bike_doc_api.repositories.diagnostic_jobs import DiagnosticJobRepository
from bike_doc_api.repositories.job_inspection import JobInspectionRepository
from bike_doc_api.workers.diagnostic import DiagnosticRecoveryPolicy
from bike_doc_api.workers.profile_inference import ProfileRecoveryPolicy


def create_job_maintenance(
    settings: Settings,
    policies: Mapping[tuple[str, int], ReconciliationPolicy] | None = None,
) -> JobMaintenance:
    """Wire short independent sessions and an explicitly registered policy set."""
    sessions = get_sessionmaker(
        settings.database_url,
        settings.maintenance_pool_size,
        settings.database_pool_timeout_seconds,
        "maintenance",
    )

    @asynccontextmanager
    async def transaction() -> AsyncIterator[BackgroundJobRepository]:
        async with sessions() as session, session.begin():
            yield DiagnosticJobRepository(session)

    monitor = QueueMonitor(settings)
    monitoring = False

    async def observe() -> None:
        nonlocal monitoring
        async with sessions() as session:
            count, oldest = await JobInspectionRepository(
                session
            ).publication_pressure()
        queue_telemetry.observe("publication_pending", count)
        queue_telemetry.observe("publication_oldest", oldest)
        async with asyncio.timeout(8):
            if not monitoring:
                await monitor.start()
                monitoring = True
            await monitor.snapshot()

    return JobMaintenance(
        settings,
        transaction,
        NatsJobPublisher(settings),
        policies if policies is not None else recovery_policies(settings),
        observe=observe,
        close_observer=monitor.close,
    )


def recovery_policies(
    settings: Settings,
) -> Mapping[tuple[str, int], ReconciliationPolicy]:
    """One complete initial-definition allowlist for maintenance and reconstruction."""
    return {
        ("diagnostic_turn", 1): DiagnosticRecoveryPolicy(
            max(
                settings.diagnostic_worker_retry_seconds,
                settings.job_recovery_backoff_seconds,
            )
        ),
        ("profile_inference", 1): ProfileRecoveryPolicy(
            max(
                settings.profile_worker_retry_seconds,
                settings.job_recovery_backoff_seconds,
            )
        ),
    }
