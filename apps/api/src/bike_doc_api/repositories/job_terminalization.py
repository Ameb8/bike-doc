"""Registered product terminalization in the job transition transaction."""

from collections.abc import Awaitable, Callable, Mapping

from sqlalchemy.ext.asyncio import AsyncSession

from bike_doc_api.models.background_job import BackgroundJob
from bike_doc_api.repositories.profile_jobs import ProfileJobRepository

TerminalizationPolicy = Callable[[AsyncSession, BackgroundJob], Awaitable[None]]


class JobTerminalizationRepository(ProfileJobRepository):
    def __init__(
        self,
        session: AsyncSession,
        policies: Mapping[str, TerminalizationPolicy],
    ) -> None:
        super().__init__(session)
        self._terminalization_policies = policies

    async def _persist_transition(self, row: BackgroundJob) -> None:
        policy = self._terminalization_policies.get(row.job_kind)
        if policy is not None and row.state in {"dead", "interrupted"}:
            # A durable product completion may reconcile the job to success;
            # do not flush the provisional terminal state first.
            with self._session.no_autoflush:
                await policy(self._session, row)
        await super()._persist_transition(row)
