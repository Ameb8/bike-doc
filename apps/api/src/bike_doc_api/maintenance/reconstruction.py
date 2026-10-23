"""Finite operator operation: restore topology and durable notification intent."""

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from contextlib import asynccontextmanager

import structlog
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from bike_doc_api.core.config import Settings, get_settings
from bike_doc_api.core.nats import ensure_work_topology, jetstream, nats_connection
from bike_doc_api.maintenance.hosting import recovery_policies
from bike_doc_api.maintenance.runtime import (
    Clock,
    ReconciliationPolicy,
    SystemClock,
    Transaction,
)
from bike_doc_api.repositories.background_jobs import BackgroundJobRepository

logger = structlog.get_logger(__name__)


async def reconstruct(
    settings: Settings,
    transaction: Transaction,
    provision: Callable[[], Awaitable[None]],
    policies: Mapping[tuple[str, int], ReconciliationPolicy],
    *,
    clock: Clock | None = None,
) -> int:
    """Validate topology before touching truth; commit each bounded page.

    Interrupted operations restart from the beginning. Already-pending intent
    coalesces, conditional generation checks protect overlap, and publication
    remains exclusively owned by the existing API loop. No broker inventory or
    advisory is consulted to decide application transitions.
    """
    await provision()
    started = (clock or SystemClock()).now()
    after = ""
    advanced = 0
    while True:
        async with transaction() as repository:
            page = await repository.reconstruction_page(
                after=after,
                limit=settings.job_reconstruction_page_limit,
                accepted_before=started,
            )
            changed = 0
            for job in page:
                policy = policies.get((job.job_kind, job.input_version))
                if policy is not None and policy.may_republish(job):
                    changed += await repository.advance_publication(
                        job_id=job.id,
                        observed_generation=job.desired_generation,
                        no_progress_before=None,
                        coalesce_pending=True,
                    )
        advanced += changed
        logger.info("job_reconstruction_page", scanned=len(page), advanced=changed)
        if not page:
            break
        after = page[-1].id
    logger.info("job_reconstruction_completed", advanced=advanced)
    return advanced


async def run(settings: Settings) -> int:
    """Own resources only for this explicit operation; never start maintenance."""
    engine = create_async_engine(settings.database_url, pool_size=1, max_overflow=0)
    sessions = async_sessionmaker(engine, expire_on_commit=False)

    @asynccontextmanager
    async def transaction() -> AsyncIterator[BackgroundJobRepository]:
        async with sessions() as session, session.begin():
            yield BackgroundJobRepository(session)

    try:
        async with nats_connection(settings) as client:

            async def provision() -> None:
                await ensure_work_topology(jetstream(client), settings)

            return await reconstruct(
                settings, transaction, provision, recovery_policies(settings)
            )
    finally:
        await engine.dispose()


def main() -> None:
    """Bounded failures deliberately omit dependency exception text and URLs."""
    try:
        asyncio.run(run(get_settings()))
    except (Exception, KeyboardInterrupt):
        logger.error("job_reconstruction_failed")
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
