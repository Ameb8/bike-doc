"""Thin workload host seam; the caller owns database, NATS, and provider resources."""

from bike_doc_api.core.config import Settings
from bike_doc_api.workers.registry import HandlerRegistry
from bike_doc_api.workers.runtime import (
    JobStore,
    PullTransport,
    PullWorker,
    RuntimeOptions,
)


def create_workload_worker(
    settings: Settings,
    registry: HandlerRegistry,
    store: JobStore,
    transport: PullTransport,
) -> PullWorker:
    subjects = {
        "profile_inference": settings.nats_profile_subject,
        "diagnostic": settings.nats_diagnostic_subject,
    }
    if registry.workload not in subjects:
        raise ValueError("unsupported workload role")
    diagnostic = registry.workload == "diagnostic"
    return PullWorker(
        registry,
        store,
        transport,
        RuntimeOptions(
            subject=subjects[registry.workload],
            concurrency=(
                settings.diagnostic_worker_concurrency
                if diagnostic
                else settings.worker_concurrency
            ),
            fetch_batch=(
                settings.diagnostic_worker_fetch_batch
                if diagnostic
                else settings.worker_fetch_batch
            ),
            fetch_timeout=(
                settings.diagnostic_worker_fetch_timeout_seconds
                if diagnostic
                else settings.worker_fetch_timeout_seconds
            ),
            progress_interval=(
                settings.diagnostic_worker_progress_seconds
                if diagnostic
                else settings.worker_progress_seconds
            ),
            shutdown_timeout=(
                settings.diagnostic_worker_shutdown_seconds
                if diagnostic
                else settings.worker_shutdown_seconds
            ),
        ),
    )
