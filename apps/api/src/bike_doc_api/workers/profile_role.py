"""Independent profile process: resources and registry over the shared runtime."""

import asyncio
import signal
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from bike_doc_api.core.config import (
    Settings,
    get_settings,
    validate_profile_inference_runtime_configuration,
)
from bike_doc_api.core.logging import configure_logging
from bike_doc_api.core.nats import ensure_work_topology, jetstream, nats_connection
from bike_doc_api.models.background_job import BackgroundJob
from bike_doc_api.providers.profile_inference import GeminiProfileInferenceExtractor
from bike_doc_api.providers.storage import GCSStorageProvider, LocalStorageProvider
from bike_doc_api.repositories.artifacts import ArtifactRepository
from bike_doc_api.repositories.background_jobs import JobError, JobOutcome
from bike_doc_api.repositories.bikes import BikeRepository
from bike_doc_api.repositories.profile_inference import ProfileInferenceRunRepository
from bike_doc_api.repositories.profile_jobs import ProfileJobRepository
from bike_doc_api.repositories.repair_sessions import (
    RepairSessionRepository,
    RepairTurnRepository,
)
from bike_doc_api.schemas.background_jobs import ProfileInferenceInputV1
from bike_doc_api.services.profile_inference import (
    ProfileInferenceExtractor,
    ProfileInferenceService,
    StorageProviderProtocol,
)
from bike_doc_api.services.profile_inference_resolution import ProfileResolverPolicy
from bike_doc_api.workers.adapters import (
    NatsPullTransport,
    PostgresJobStore,
    subscribe_work_advisories,
)
from bike_doc_api.workers.profile_inference import (
    ProfileInferenceHandler,
    profile_policy,
    profile_registry,
)
from bike_doc_api.workers.registry import ClaimedJob
from bike_doc_api.workers.role import create_workload_worker
from bike_doc_api.workers.runtime import PullWorker


class ProfileApplication:
    """Fresh domain session per attempt; the job remains execution authority."""

    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        settings: Settings,
        storage: StorageProviderProtocol,
        extractor: ProfileInferenceExtractor,
        resolver_policy: ProfileResolverPolicy,
    ) -> None:
        self.sessions, self.settings = sessions, settings
        self.storage, self.extractor = storage, extractor
        self.resolver_policy = resolver_policy

    async def handle(self, job: ClaimedJob[ProfileInferenceInputV1]) -> JobOutcome:
        async with self.sessions() as session:

            async def before_write() -> None:
                await ProfileJobRepository(session).require_execution(
                    job_id=job.id, execution_token=job.execution_token
                )

            async def commit() -> None:
                await before_write()
                await session.commit()

            service = ProfileInferenceService(
                turns=RepairTurnRepository(session),
                repair_sessions=RepairSessionRepository(session),
                bikes=BikeRepository(session),
                artifacts=ArtifactRepository(session),
                runs=ProfileInferenceRunRepository(session),
                storage=self.storage,
                extractor=self.extractor,
                extractor_version=job.input.extractor_version,
                max_attempts=self.settings.profile_inference_max_attempts,
                resolver_policy=self.resolver_policy,
                commit=commit,
                rollback=session.rollback,
                before_write=before_write,
            )
            try:
                return await ProfileInferenceHandler(
                    service, self.settings.profile_worker_retry_seconds
                )(job)
            except Exception:
                await session.rollback()
                return JobOutcome(
                    "retrying",
                    JobError.PROVIDER_UNAVAILABLE,
                    profile_policy(self.settings).max_retry_delay,
                )


@asynccontextmanager
async def profile_worker(
    settings: Settings,
    *,
    extractor: ProfileInferenceExtractor,
    storage: StorageProviderProtocol,
) -> AsyncIterator[PullWorker]:
    """Own database and NATS; injected process-owned adapters allow real integration."""
    profile_policy(settings)
    resolver_policy = ProfileResolverPolicy.from_deployment(
        mode=settings.profile_inference_policy_mode,
        policies=settings.profile_inference_policies,
    )
    engine = create_async_engine(
        settings.database_url,
        pool_pre_ping=True,
        pool_size=settings.profile_worker_pool_size,
        max_overflow=0,
    )
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    application = ProfileApplication(
        sessions, settings, storage, extractor, resolver_policy
    )
    registry = profile_registry(settings, application.handle)
    try:
        # Readiness requires migrated job storage, even while producers are disabled.
        async with sessions() as session:
            await session.execute(select(BackgroundJob.id).limit(0))
        async with nats_connection(settings) as client:
            js = jetstream(client)
            await ensure_work_topology(js, settings)
            subscription = await js.pull_subscribe(
                settings.nats_profile_subject,
                durable=settings.nats_profile_consumer,
                stream=settings.nats_work_stream,
            )
            await subscribe_work_advisories(
                client, settings.nats_work_stream, settings.nats_profile_consumer
            )
            runtime = create_workload_worker(
                settings,
                registry,
                PostgresJobStore(sessions, repository_factory=ProfileJobRepository),
                NatsPullTransport(subscription, client=client),
            )
            try:
                yield runtime
            finally:
                await runtime.close()
    finally:
        async with asyncio.timeout(5):
            await engine.dispose()


async def run_profile_role(settings: Settings) -> None:
    """Run until SIGINT/SIGTERM, then use bounded shared-runtime shutdown."""
    profile_policy(settings)
    validate_profile_inference_runtime_configuration(settings)
    factory = (
        GeminiProfileInferenceExtractor.from_vertex_ai
        if settings.profile_inference_llm_provider == "vertex_ai"
        else GeminiProfileInferenceExtractor.from_google_ai
    )
    if settings.artifact_storage_provider == "gcs" and not settings.artifact_gcs_bucket:
        raise ValueError("profile worker storage bucket required")
    async with AsyncExitStack() as resources:
        extractor = factory(
            model=settings.profile_inference_model,
            timeout_seconds=settings.profile_inference_timeout_seconds,
        )

        async def close_extractor() -> None:
            async with asyncio.timeout(5):
                await extractor.close()

        resources.push_async_callback(close_extractor)
        storage = (
            LocalStorageProvider(settings.artifact_local_storage_root)
            if settings.artifact_storage_provider == "local"
            else GCSStorageProvider(bucket_name=settings.artifact_gcs_bucket or "")
        )
        if isinstance(storage, GCSStorageProvider):

            async def close_storage() -> None:
                async with asyncio.timeout(5):
                    await storage.close()

            resources.push_async_callback(close_storage)
        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, stop.set)
        try:
            async with profile_worker(
                settings, extractor=extractor, storage=storage
            ) as runtime:
                runtime.start()
                structlog.get_logger(__name__).info(
                    "profile_worker_ready",
                    workload="profile_inference",
                    input_version=1,
                )
                await stop.wait()
        finally:
            for sig in (signal.SIGINT, signal.SIGTERM):
                loop.remove_signal_handler(sig)


def main() -> None:
    try:
        settings = get_settings()
        configure_logging(
            environment=settings.environment,
            log_level=settings.log_level,
            log_format=settings.log_format,
        )
        asyncio.run(run_profile_role(settings))
    except Exception:
        structlog.get_logger(__name__).error("profile_worker_process_failed")
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
