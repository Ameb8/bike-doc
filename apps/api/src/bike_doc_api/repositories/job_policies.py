"""Production product policies shared by worker and maintenance composition."""

from sqlalchemy.ext.asyncio import AsyncSession

from bike_doc_api.repositories.diagnostic_jobs import terminalize_diagnostic
from bike_doc_api.repositories.job_terminalization import JobTerminalizationRepository


def interactive_job_repository(session: AsyncSession) -> JobTerminalizationRepository:
    # Unsupported versions never execute, but a validated diagnostic turn
    # reference can still be repaired after a definition failure.
    return JobTerminalizationRepository(
        session, {"diagnostic_turn": terminalize_diagnostic}
    )
