"""Report repository."""

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from bike_doc_api.models.phase_report import PhaseReport


class PhaseReportRepository:
    """Persistence operations for phase report envelopes."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, report: PhaseReport) -> PhaseReport:
        """Add a phase report to the current transaction."""
        self._session.add(report)
        await self._session.flush()
        return report

    async def get(self, report_id: str) -> PhaseReport | None:
        """Return a phase report by ID."""
        return await self._session.get(PhaseReport, report_id)

    async def get_for_session(
        self,
        *,
        repair_session_id: str,
        report_id: str,
    ) -> PhaseReport | None:
        """Return a report owned by a repair session."""
        result = await self._session.execute(
            select(PhaseReport).where(
                PhaseReport.id == report_id,
                PhaseReport.repair_session_id == repair_session_id,
            ),
        )
        return result.scalar_one_or_none()

    async def list_for_session(
        self,
        repair_session_id: str,
        *,
        report_type: str | None = None,
        limit: int = 50,
        cursor_report: PhaseReport | None = None,
    ) -> list[PhaseReport]:
        """Return reports for a repair session."""
        statement = select(PhaseReport).where(
            PhaseReport.repair_session_id == repair_session_id,
        )
        if report_type is not None:
            statement = statement.where(PhaseReport.type == report_type)
        if cursor_report is not None:
            statement = statement.where(
                or_(
                    PhaseReport.created_at < cursor_report.created_at,
                    (
                        (PhaseReport.created_at == cursor_report.created_at)
                        & (PhaseReport.id < cursor_report.id)
                    ),
                ),
            )
        result = await self._session.execute(
            statement.order_by(
                PhaseReport.created_at.desc(),
                PhaseReport.id.desc(),
            ).limit(limit),
        )
        return list(result.scalars().all())

    async def list_for_phase_session(
        self,
        repair_phase_session_id: str,
        *,
        limit: int = 50,
    ) -> list[PhaseReport]:
        """Return reports for a phase session."""
        result = await self._session.execute(
            select(PhaseReport)
            .where(PhaseReport.repair_phase_session_id == repair_phase_session_id)
            .order_by(PhaseReport.created_at.desc(), PhaseReport.id.desc())
            .limit(limit),
        )
        return list(result.scalars().all())
