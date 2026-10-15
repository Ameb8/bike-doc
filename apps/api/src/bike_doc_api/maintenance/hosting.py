"""API composition of maintenance resources, independent of route handlers."""

from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager

from bike_doc_api.core.config import Settings
from bike_doc_api.db.session import get_sessionmaker
from bike_doc_api.maintenance.nats_publisher import NatsJobPublisher
from bike_doc_api.maintenance.runtime import JobMaintenance, ReconciliationPolicy
from bike_doc_api.repositories.background_jobs import BackgroundJobRepository
from bike_doc_api.repositories.diagnostic_jobs import DiagnosticJobRepository
from bike_doc_api.workers.diagnostic import DiagnosticRecoveryPolicy
from bike_doc_api.workers.profile_inference import ProfileRecoveryPolicy


def create_job_maintenance(
    settings: Settings,
    policies: Mapping[tuple[str, int], ReconciliationPolicy] | None = None,
) -> JobMaintenance:
    """Wire short independent sessions and an explicitly registered policy set."""
    sessions = get_sessionmaker(settings.database_url)

    @asynccontextmanager
    async def transaction() -> AsyncIterator[BackgroundJobRepository]:
        async with sessions() as session, session.begin():
            yield DiagnosticJobRepository(session)

    return JobMaintenance(
        settings,
        transaction,
        NatsJobPublisher(settings),
        policies
        if policies is not None
        else {
            ("diagnostic_turn", 1): DiagnosticRecoveryPolicy(
                settings.diagnostic_worker_retry_seconds
            ),
            ("profile_inference", 1): ProfileRecoveryPolicy(
                settings.profile_worker_retry_seconds
            ),
        },
    )
