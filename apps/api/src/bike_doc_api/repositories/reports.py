"""Report repository."""

from datetime import UTC, datetime
from typing import Any, cast

from sqlalchemy import or_, select, update
from sqlalchemy.engine import CursorResult
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
        result = await self._session.execute(
            select(PhaseReport).where(
                PhaseReport.id == report_id,
                PhaseReport.evidence_redacted_at.is_(None),
            )
        )
        return result.scalar_one_or_none()

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
                PhaseReport.evidence_redacted_at.is_(None),
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
            PhaseReport.evidence_redacted_at.is_(None),
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
            .where(
                PhaseReport.repair_phase_session_id == repair_phase_session_id,
                PhaseReport.evidence_redacted_at.is_(None),
            )
            .order_by(PhaseReport.created_at.desc(), PhaseReport.id.desc())
            .limit(limit),
        )
        return list(result.scalars().all())

    async def invalidate_citing_artifact(self, *, artifact_id: str, reason: str) -> int:
        """Make reports with inaccessible image evidence ineligible for reads."""

        result = await self._session.execute(
            update(PhaseReport)
            .where(
                PhaseReport.source_artifact_ids.contains([artifact_id]),
                PhaseReport.evidence_redacted_at.is_(None),
            )
            .values(
                evidence_redacted_at=datetime.now(UTC),
                evidence_redaction_reason=reason,
            ),
        )
        await self._session.flush()
        return int(cast(CursorResult[Any], result).rowcount or 0)
