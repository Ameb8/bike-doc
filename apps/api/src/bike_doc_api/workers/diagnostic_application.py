"""Fresh authoritative diagnostic application state for each claimed attempt."""

import asyncio

import structlog
from opentelemetry import trace
from opentelemetry.trace import Status, StatusCode
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from bike_doc_api.adk.agents.diagnostic import DIAGNOSTIC_PROMPT_VERSION
from bike_doc_api.adk.execution import (
    DiagnosticResources,
    build_diagnostic_orchestrator,
)
from bike_doc_api.adk.execution_telemetry import (
    _apply_snapshot,
    _emit_completed,
    _record_attempt_metrics,
    _safe_log,
)
from bike_doc_api.adk.sessions import (
    DiagnosticADKSessionClient,
    StaleInMemoryADKSessionError,
)
from bike_doc_api.core.config import Settings
from bike_doc_api.repositories.background_jobs import JobError
from bike_doc_api.repositories.repair_sessions import (
    RepairPhaseSessionRepository,
    RepairSessionRepository,
    RepairTurnRepository,
)
from bike_doc_api.repositories.users import UserRepository
from bike_doc_api.schemas.background_jobs import DiagnosticTurnInputV1
from bike_doc_api.workers.diagnostic import DiagnosticPreparationError
from bike_doc_api.workers.registry import ClaimedJob


class DiagnosticExecutionApplication:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        settings: Settings,
        resources: DiagnosticResources,
    ) -> None:
        self.sessions, self.settings, self.resources = sessions, settings, resources

    async def execute(self, job: ClaimedJob[DiagnosticTurnInputV1]) -> None:
        with trace.get_tracer(__name__).start_as_current_span(
            "bike_doc.diagnostic.turn",
            record_exception=False,
            set_status_on_exception=False,
        ) as span:
            span.set_attributes(
                {
                    "bike_doc.turn.id": job.input.turn_id,
                    "bike_doc.workflow.phase": "diagnostic",
                    "bike_doc.telemetry.schema_version": "diagnostic_telemetry.v1",
                }
            )
            context = span.get_span_context()
            with structlog.contextvars.bound_contextvars(
                turn_id=job.input.turn_id,
                trace_id=f"{context.trace_id:032x}",
                span_id=f"{context.span_id:016x}",
            ):
                try:
                    await self._execute(job, span)
                except asyncio.CancelledError:
                    _emit_completed(
                        structlog.get_logger(__name__),
                        snapshot=None,
                        fields={},
                        terminal_status="unknown",
                        outcome="cancelled",
                    )
                    raise
                except Exception:
                    span.set_status(
                        Status(StatusCode.ERROR, "diagnostic_execution_failed")
                    )
                    _emit_completed(
                        structlog.get_logger(__name__),
                        snapshot=None,
                        fields={},
                        terminal_status="unknown",
                        outcome="terminal_error",
                    )
                    raise

    async def _execute(
        self, job: ClaimedJob[DiagnosticTurnInputV1], span: trace.Span
    ) -> None:
        async with self.sessions() as session:
            try:
                turn = await RepairTurnRepository(session).get(job.input.turn_id)
                if turn is None or turn.phase != "diagnostic":
                    raise DiagnosticPreparationError(
                        JobError.PERMANENT_FAILURE, retryable=False
                    )
                repair = await RepairSessionRepository(session).get(
                    turn.repair_session_id
                )
                phase = await RepairPhaseSessionRepository(session).get(
                    turn.repair_phase_session_id
                )
                if (
                    repair is None
                    or phase is None
                    or phase.repair_session_id != repair.id
                    or phase.phase != "diagnostic"
                ):
                    raise DiagnosticPreparationError(
                        JobError.PERMANENT_FAILURE, retryable=False
                    )
                user = await UserRepository(session).get(repair.user_id)
                if user is None:
                    raise DiagnosticPreparationError(
                        JobError.PERMANENT_FAILURE, retryable=False
                    )
                await DiagnosticADKSessionClient(
                    self.resources.session_service
                ).ensure_or_resume(
                    phase_session=phase,
                    phase_sessions=RepairPhaseSessionRepository(session),
                )
                await session.commit()
                # Reload after binding; keep ORM state inside this execution scope.
                await session.refresh(phase)
                index = await RepairTurnRepository(
                    session
                ).count_for_phase_session_through_start_event_sequence(
                    repair_phase_session_id=phase.id,
                    start_event_sequence=turn.start_event_sequence,
                )
            except StaleInMemoryADKSessionError:
                await session.rollback()
                raise DiagnosticPreparationError(
                    JobError.PERMANENT_FAILURE, retryable=False
                ) from None
            except (TimeoutError, ConnectionError, OperationalError):
                await session.rollback()
                raise DiagnosticPreparationError(
                    JobError.PROVIDER_UNAVAILABLE, retryable=True
                ) from None
            fields: dict[str, object] = {
                "diagnostic_session_id": phase.id,
                "turn_index": index,
                "provider": "google",
                "model": self.settings.diagnostic_agent_model,
                "prompt_version": DIAGNOSTIC_PROMPT_VERSION,
                "report_schema_version": phase.diagnostic_report_schema_version
                or "diagnostic_report.v2",
                "image_analysis_mode": turn.image_analysis_mode
                or self.settings.image_analysis_mode,
                "artifact_count": len(turn.message.get("artifact_ids", [])),
                "has_text_input": bool(turn.message.get("text")),
                "responds_to_input_request": turn.responds_to_input_request_id
                is not None,
            }
            with structlog.contextvars.bound_contextvars(
                repair_session_id=repair.id,
                diagnostic_session_id=phase.id,
            ):
                span.set_attributes(
                    {
                        "bike_doc.repair_session.id": repair.id,
                        "bike_doc.diagnostic_session.id": phase.id,
                    }
                )
                _safe_log("info", "diagnostic_turn_started", **fields)
                orchestrator = build_diagnostic_orchestrator(
                    session=session,
                    settings=self.settings,
                    resources=self.resources,
                )
                snapshot = await orchestrator.process_turn(current_user=user, turn=turn)
                _apply_snapshot(span, snapshot)
                _record_attempt_metrics(snapshot=snapshot, fields=fields)
                _emit_completed(
                    structlog.get_logger(__name__), snapshot=snapshot, fields=fields
                )
