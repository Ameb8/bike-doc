"""Transactional fencing for the complete diagnostic product unit of work.

Session-local hooks deliberately cover ORM autoflush, direct DML, and commit,
including writes added later to the composed graph. ADK owns separate storage;
its external execution is reserved by the irreversible whole-turn boundary.
"""

from datetime import datetime
from typing import Any, cast

from sqlalchemy import event, func, select, update
from sqlalchemy.engine import RowMapping
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import ORMExecuteState, Session

from bike_doc_api.core.diagnostic_execution import (
    DiagnosticExecution,
    StaleDiagnosticExecutionError,
)
from bike_doc_api.models.background_job import BackgroundJob


class DiagnosticExecutionFence:
    def __init__(self, session: AsyncSession, execution: DiagnosticExecution) -> None:
        self.session = session
        self.execution = execution
        self._rejected = False

    def _require(self, session: Session, *, boundary: bool = True) -> RowMapping:
        if self._rejected:
            raise StaleDiagnosticExecutionError()
        # Core connection calls avoid autoflush and recursive ORM hooks. Always
        # lock the job before product rows; read wall time AFTER the lock wait.
        connection = session.connection()
        row = (
            connection.execute(
                select(BackgroundJob.__table__)
                .where(BackgroundJob.id == self.execution.job_id)
                .with_for_update()
            )
            .mappings()
            .first()
        )
        now = cast(datetime, connection.scalar(select(func.clock_timestamp())))
        if (
            row is None
            or row["job_kind"] != "diagnostic_turn"
            or row["state"] != "running"
            or row["execution_token"] != self.execution.token
            or row["execution_deadline"] != self.execution.deadline
            or row["execution_deadline"] <= now
            or (
                boundary
                and (
                    row["effect_boundary_at"] is None
                    or row["effect_boundary_token"] != self.execution.token
                )
            )
        ):
            self._rejected = True
            raise StaleDiagnosticExecutionError()
        return row

    async def cross_boundary(self) -> None:
        """CAS and commit the irreversible reservation without renewing validity."""

        def cross(session: Session) -> None:
            row = self._require(session, boundary=False)
            if row["effect_boundary_at"] is not None:
                if row["effect_boundary_token"] != self.execution.token:
                    self._rejected = True
                    raise StaleDiagnosticExecutionError()
                return
            session.connection().execute(
                update(BackgroundJob)
                .where(
                    BackgroundJob.id == self.execution.job_id,
                    BackgroundJob.execution_token == self.execution.token,
                    BackgroundJob.execution_deadline > func.clock_timestamp(),
                    BackgroundJob.effect_boundary_at.is_(None),
                )
                .values(
                    effect_boundary_at=func.clock_timestamp(),
                    effect_boundary_token=self.execution.token,
                )
            )
            self._require(session)

        try:
            await self.session.run_sync(cross)
            await self.session.commit()
        except StaleDiagnosticExecutionError:
            await self.session.rollback()
            raise

    def bind(self) -> None:
        """Carry the immutable context through every composed service/repository.

        Install only on the executor-owned product session, after preparation
        and boundary commit. Never install on HTTP, profile, or ADK sessions.
        """
        sync = self.session.sync_session
        if "diagnostic_execution_fence" in sync.info:
            raise ValueError("diagnostic session already fenced")
        sync.info["diagnostic_execution_fence"] = self
        event.listen(sync, "do_orm_execute", self._before_sql)
        event.listen(sync, "before_flush", self._before_flush)
        event.listen(sync, "after_flush_postexec", self._before_commit)
        event.listen(sync, "before_commit", self._before_commit)

    def _before_sql(self, state: ORMExecuteState) -> None:
        self._require(state.session)

    def _before_flush(self, session: Session, context: Any, instances: Any) -> None:
        self._require(session)

    def _before_commit(self, session: Session, *args: Any) -> None:
        self._require(session)

    async def require_external(self) -> None:
        """Validate the durable reservation without releasing product locks."""
        await self.session.run_sync(self._before_commit)

    async def reserve_external(self) -> None:
        """Commit fenced pending markers before an external invocation.

        The boundary reserves this turn's external calls; it is never replayed.
        In-flight calls may continue after expiry, but their results cannot write.
        """
        await self.session.run_sync(self._before_commit)
        await self.session.commit()
