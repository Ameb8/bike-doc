"""Read-only projections: deliberately never materialize a BackgroundJob entity."""

from datetime import UTC, datetime, timedelta
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from bike_doc_api.core.queue_contract import DIMENSIONS
from bike_doc_api.models.background_job import BackgroundJob as Job
from bike_doc_api.repositories.background_jobs import TERMINAL_STATES


class InspectionFilter(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    kind: Literal["diagnostic_turn", "profile_inference"] | None = None
    workload: Literal["diagnostic", "profile_inference"] | None = None
    version: int | None = Field(default=None, ge=1, le=32767)
    state: (
        Literal["queued", "running", "retrying", "succeeded", "dead", "interrupted"]
        | None
    ) = None
    terminal: bool | None = None
    minimum_age_seconds: int = Field(default=0, ge=0, le=315360000)
    limit: int = Field(default=50, ge=1, le=200)
    after: str | None = Field(
        default=None, pattern=r"^job_[0-7][0-9A-HJKMNP-TV-Z]{25}$"
    )


COLUMNS = (
    "id",
    "job_kind",
    "workload_class",
    "input_version",
    "state",
    "attempt_count",
    "attempt_limit",
    "desired_generation",
    "confirmed_generation",
    "eligible_at",
    "publication_eligible_at",
    "publication_confirmed_at",
    "first_started_at",
    "execution_started_at",
    "execution_deadline",
    "effect_boundary_at",
    "terminal_at",
    "created_at",
    "updated_at",
    "latest_error_category",
    "transport_error",
)


class JobInspectionRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def page(self, filters: InspectionFilter) -> dict[str, object]:
        query = select(*(getattr(Job, name) for name in COLUMNS))
        for column, value in (
            (Job.job_kind, filters.kind),
            (Job.workload_class, filters.workload),
            (Job.input_version, filters.version),
            (Job.state, filters.state),
        ):
            if value is not None:
                query = query.where(column == value)
        if filters.terminal is not None:
            predicate = Job.state.in_(TERMINAL_STATES)
            query = query.where(predicate if filters.terminal else ~predicate)
        query = query.where(
            Job.created_at
            <= datetime.now(UTC) - timedelta(seconds=filters.minimum_age_seconds)
        )
        if filters.after:
            query = query.where(Job.id > filters.after)
        rows = (
            (
                await self._session.execute(
                    query.order_by(Job.id).limit(filters.limit + 1)
                )
            )
            .mappings()
            .all()
        )
        jobs = [dict(row) for row in rows[: filters.limit]]
        for job in jobs:
            for field, dimension in (
                ("job_kind", "kind"),
                ("workload_class", "workload"),
            ):
                if job[field] not in DIMENSIONS[dimension]:
                    job[field] = "unregistered"
        return {
            "jobs": jobs,
            "next_after": rows[filters.limit - 1]["id"]
            if len(rows) > filters.limit
            else None,
        }

    async def publication_pressure(self) -> tuple[int, float]:
        count, oldest = (
            await self._session.execute(
                select(func.count(), func.min(Job.created_at)).where(
                    Job.desired_generation > Job.confirmed_generation
                )
            )
        ).one()
        return int(count), max(
            0, (datetime.now(UTC) - oldest).total_seconds()
        ) if oldest else 0
