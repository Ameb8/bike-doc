"""Background diagnostic turn execution wiring."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import cast

import structlog
from opentelemetry import trace
from opentelemetry.trace import Status, StatusCode
from sqlalchemy.ext.asyncio import AsyncSession

from bike_doc_api.adk.agents.diagnostic import (
    DIAGNOSTIC_PROMPT_VERSION,
    create_diagnostic_agent,
)
from bike_doc_api.adk.orchestration import DiagnosticTurnOrchestrator
from bike_doc_api.adk.runner import DiagnosticRunner
from bike_doc_api.adk.tools.artifacts import ListDiagnosticArtifactsTool
from bike_doc_api.adk.tools.bike_profile import (
    BikeProfileServiceProtocol,
    GetBikeProfileTool,
)
from bike_doc_api.adk.tools.input_requests import (
    DiagnosticInputRequestServiceProtocol,
    RequestDiagnosticInputTool,
)
from bike_doc_api.adk.tools.repair_history import (
    LookupRepairHistoryTool,
    RepairHistoryServiceProtocol,
)
from bike_doc_api.adk.tools.reports import (
    DiagnosticReportServiceProtocol,
    SaveDiagnosticReportTool,
)
from bike_doc_api.adk.tools.safety import RaiseSafetyFlagTool, SafetyServiceProtocol
from bike_doc_api.adk.tools.tool_catalog import DiagnosticAgentToolDependencies
from bike_doc_api.adk.turn_telemetry_state import DiagnosticTurnTelemetrySnapshot
from bike_doc_api.api.deps import (
    get_adk_session_service,
    get_cost_estimate_service,
    get_price_lookup_provider,
    get_storage_provider,
)
from bike_doc_api.core.config import (
    Settings,
    get_settings,
    validate_diagnostic_runtime_configuration,
    validate_observation_extraction_runtime_configuration,
    validate_profile_inference_runtime_configuration,
)
from bike_doc_api.db.session import get_session_for_database_url
from bike_doc_api.models.repair_session import RepairSession as RepairSessionModel
from bike_doc_api.models.repair_session import RepairTurn as RepairTurnModel
from bike_doc_api.models.user import User as UserModel
from bike_doc_api.providers.observation_extraction import (
    GeminiDiagnosticObservationExtractor,
)
from bike_doc_api.providers.profile_inference import GeminiProfileInferenceExtractor
from bike_doc_api.repositories.artifacts import ArtifactRepository
from bike_doc_api.repositories.bikes import BikeRepository
from bike_doc_api.repositories.events import RepairSessionEventRepository
from bike_doc_api.repositories.observation_extraction import (
    ObservationExtractionRunRepository,
)
from bike_doc_api.repositories.profile_inference import ProfileInferenceRunRepository
from bike_doc_api.repositories.repair_sessions import (
    RepairPhaseSessionRepository,
    RepairSessionRepository,
    RepairTurnRepository,
)
from bike_doc_api.repositories.reports import PhaseReportRepository
from bike_doc_api.repositories.users import UserRepository
from bike_doc_api.schemas.common import RepairSessionStatus
from bike_doc_api.schemas.event import RepairSessionEventType
from bike_doc_api.schemas.repair_session import repair_session_from_model
from bike_doc_api.services.artifacts import ArtifactService
from bike_doc_api.services.bikes import ResolvedBikeProfileService
from bike_doc_api.services.diagnostic_visual_context import (
    DiagnosticVisualContextService,
    RepairTurnRepositoryProtocol,
)
from bike_doc_api.services.events import EventService
from bike_doc_api.services.observation_extraction import (
    DiagnosticObservationExtractor,
    ObservationExtractionRequest,
    ObservationExtractionResult,
)
from bike_doc_api.services.profile_inference import (
    ProfileInferenceExtractor,
    ProfileInferenceService,
)
from bike_doc_api.services.profile_inference_resolution import ProfileResolverPolicy
from bike_doc_api.services.repair_sessions import RepairSessionService
from bike_doc_api.services.reports import CostEstimateServiceProtocol, ReportService
from bike_doc_api.services.safety import DiagnosticSafetyService
from bike_doc_api.services.turns import TurnService

logger = structlog.get_logger(__name__)
_TELEMETRY_SCHEMA_VERSION = "diagnostic_telemetry.v1"


class _UnavailableProfileInferenceExtractor:
    """Convert configuration failures into retryable run state, not turn failures."""

    def __init__(self, error: Exception) -> None:
        self._error = error

    async def extract(self, _request: object) -> dict[str, object]:
        """Raise the unavailable runtime error only inside the inference service."""

        raise self._error


class _UnavailableObservationExtractor:
    """Persist extraction failure while allowing pixels-only diagnostic fallback."""

    provider = "unavailable"
    model = "unavailable"

    def __init__(self, error: Exception) -> None:
        self._error = error

    async def extract(
        self, _request: ObservationExtractionRequest
    ) -> ObservationExtractionResult:
        """Raise only inside the shadow extraction lifecycle."""

        raise self._error


async def execute_diagnostic_turn_background(
    user_id: str,
    repair_session_id: str,
    turn_id: str,
) -> None:
    """Run accepted diagnostic turn orchestration outside the request scope."""

    # Resolve the tracer at attempt entry so an app-installed provider (or an
    # in-memory test provider) is used instead of an import-time no-op tracer.
    with trace.get_tracer(__name__).start_as_current_span(
        "bike_doc.diagnostic.turn"
    ) as span:
        span.set_attributes(
            {
                "bike_doc.repair_session.id": repair_session_id,
                "bike_doc.turn.id": turn_id,
                "bike_doc.workflow.phase": "diagnostic",
                "bike_doc.telemetry.schema_version": _TELEMETRY_SCHEMA_VERSION,
            }
        )
        context = span.get_span_context()
        structlog.contextvars.bind_contextvars(
            repair_session_id=repair_session_id,
            turn_id=turn_id,
            trace_id=f"{context.trace_id:032x}",
            span_id=f"{context.span_id:016x}",
        )
        try:
            await _execute_diagnostic_turn_attempt(
                user_id=user_id,
                repair_session_id=repair_session_id,
                turn_id=turn_id,
                span=span,
            )
        finally:
            structlog.contextvars.clear_contextvars()


async def _execute_diagnostic_turn_attempt(
    *, user_id: str, repair_session_id: str, turn_id: str, span: trace.Span
) -> None:
    """Execute one attempt while its app-owned root span is current."""

    settings = get_settings()
    snapshot: DiagnosticTurnTelemetrySnapshot | None = None
    loaded_fields: dict[str, object] = {"environment": settings.environment}
    try:
        validate_diagnostic_runtime_configuration(settings)
    except Exception:
        # Configuration exceptions can contain provider details.
        _safe_log("error", "diagnostic_background_runtime_configuration_invalid")
        async for session in get_session_for_database_url(settings.database_url):
            await _handle_background_setup_failure(
                session=session,
                user_id=user_id,
                repair_session_id=repair_session_id,
                turn_id=turn_id,
            )
            break
        span.set_status(Status(StatusCode.ERROR, "runtime_configuration_invalid"))
        _emit_completed(
            logger,
            snapshot=None,
            fields=loaded_fields,
            terminal_status="unknown",
            outcome="terminal_error",
        )
        return

    async for session in get_session_for_database_url(settings.database_url):
        user: UserModel | None = None
        turn: RepairTurnModel | None = None
        repair_session: RepairSessionModel | None = None
        try:
            users = UserRepository(session)
            turns = RepairTurnRepository(session)
            repair_sessions = RepairSessionRepository(session)

            turn = await turns.get(turn_id)
            repair_session = await repair_sessions.get(repair_session_id)
            user = await users.get(user_id)
            if (
                user is None
                or turn is None
                or repair_session is None
                or turn.repair_session_id != repair_session_id
                or repair_session.user_id != user_id
            ):
                await _handle_background_setup_failure(
                    session=session,
                    user_id=user_id,
                    repair_session_id=repair_session_id,
                    turn_id=turn_id if turn is not None else None,
                )
                span.set_status(Status(StatusCode.ERROR, "accepted_turn_invalid"))
                _emit_completed(
                    logger,
                    snapshot=None,
                    fields=loaded_fields,
                    terminal_status="unknown",
                    outcome="terminal_error",
                )
                return

            phase_session = await RepairPhaseSessionRepository(session).get(
                turn.repair_phase_session_id
            )
            if (
                phase_session is None
                or phase_session.repair_session_id != repair_session_id
                or phase_session.phase != "diagnostic"
            ):
                await _handle_background_setup_failure(
                    session=session,
                    user_id=user_id,
                    repair_session_id=repair_session_id,
                    turn_id=turn_id,
                )
                span.set_status(Status(StatusCode.ERROR, "phase_session_not_found"))
                _emit_completed(
                    logger,
                    snapshot=None,
                    fields=loaded_fields,
                    terminal_status="unknown",
                    outcome="terminal_error",
                )
                return
            turn_index = (
                await turns.count_for_phase_session_through_start_event_sequence(
                    repair_phase_session_id=phase_session.id,
                    start_event_sequence=turn.start_event_sequence,
                )
            )
            # Legacy rows predate the snapshot column; orchestration applies the
            # same V2 compatibility rule when constructing the runner request.
            report_schema_version = (
                phase_session.diagnostic_report_schema_version or "diagnostic_report.v2"
            )
            image_mode = turn.image_analysis_mode or settings.image_analysis_mode
            has_text = isinstance(turn.message.get("text"), str) and bool(
                turn.message["text"]
            )
            loaded_fields = {
                "diagnostic_session_id": phase_session.id,
                "turn_index": turn_index,
                "provider": "google",
                "model": settings.diagnostic_agent_model,
                "prompt_version": DIAGNOSTIC_PROMPT_VERSION,
                "report_schema_version": report_schema_version,
                "image_analysis_mode": image_mode,
                "artifact_count": len(turn.message.get("artifact_ids", [])),
                "has_text_input": has_text,
                "responds_to_input_request": turn.responds_to_input_request_id
                is not None,
            }
            structlog.contextvars.bind_contextvars(
                diagnostic_session_id=phase_session.id
            )
            span.set_attributes(
                {
                    "bike_doc.diagnostic_session.id": phase_session.id,
                    "bike_doc.turn.index": turn_index,
                    "bike_doc.report.schema_version": report_schema_version,
                    "bike_doc.agent.provider": "google",
                    "bike_doc.agent.model": settings.diagnostic_agent_model,
                    "bike_doc.agent.prompt_version": DIAGNOSTIC_PROMPT_VERSION,
                    "bike_doc.image_analysis.mode": image_mode,
                    "bike_doc.input.artifact_count": len(
                        turn.message.get("artifact_ids", [])
                    ),
                    "bike_doc.input.has_text": has_text,
                }
            )
            _safe_log(
                "info",
                "diagnostic_turn_started",
                event_family="diagnostic_flow",
                component="background",
                telemetry_schema_version=_TELEMETRY_SCHEMA_VERSION,
                **loaded_fields,
            )

            orchestrator = _build_background_orchestrator(
                session=session,
                settings=settings,
            )
            snapshot = await orchestrator.process_turn(current_user=user, turn=turn)
        except asyncio.CancelledError:
            # A cancellation must remain visible without changing propagation.
            span.set_attributes(
                {
                    "bike_doc.turn.outcome": "cancelled",
                    "bike_doc.turn.terminal_status": "cancelled",
                }
            )
            _emit_completed(
                logger,
                snapshot=None,
                fields=loaded_fields,
                terminal_status="cancelled",
                outcome="cancelled",
            )
            raise
        except Exception:
            _safe_log("error", "diagnostic_background_turn_failed")
            await _handle_background_setup_failure(
                session=session,
                user_id=user_id,
                repair_session_id=repair_session_id,
                turn_id=turn_id if turn is not None else None,
            )
            span.set_status(Status(StatusCode.ERROR, "background_processing_failed"))
            _emit_completed(
                logger,
                snapshot=None,
                fields=loaded_fields,
                terminal_status="unknown",
                outcome="terminal_error",
            )
            return
        if snapshot is not None:
            _apply_snapshot(span, snapshot)
            _emit_completed(logger, snapshot=snapshot, fields=loaded_fields)
        return


def _apply_snapshot(
    span: trace.Span, snapshot: DiagnosticTurnTelemetrySnapshot
) -> None:
    """Apply only bounded immutable orchestration facts to the root span."""
    span.set_attributes(
        {
            "bike_doc.turn.outcome": snapshot.outcome,
            "bike_doc.turn.terminal_status": snapshot.terminal_status.value,
            "bike_doc.turn.duration_ms": snapshot.duration_ms,
            "bike_doc.output.delta_count": snapshot.assistant_delta_count,
            "bike_doc.output.message_count": snapshot.assistant_message_count,
            "bike_doc.terminal_action_count": snapshot.terminal_action_count,
            "bike_doc.safety.escalated": snapshot.safety.escalated,
        }
    )
    if snapshot.time_to_first_output_ms is not None:
        span.set_attribute(
            "bike_doc.turn.time_to_first_output_ms", snapshot.time_to_first_output_ms
        )
    if snapshot.error_code is not None:
        span.set_attributes(
            {
                "bike_doc.error.code": snapshot.error_code,
                "bike_doc.error.retryable": bool(snapshot.error_retryable),
            }
        )
        span.add_event(
            "diagnostic.recoverable_error",
            {
                "error_code": snapshot.error_code,
                "retryable": bool(snapshot.error_retryable),
            },
        )
    for action in snapshot.terminal_action_kinds:
        span.add_event("diagnostic.terminal_action", {"action_kind": action})
    for stage in snapshot.validation.stages:
        span.add_event(
            "diagnostic.report_validation_failed", {"validation_stage": stage}
        )
    if snapshot.safety.escalated:
        span.add_event(
            "diagnostic.safety_escalated",
            {
                "safety_state": snapshot.safety.safety_state or "unknown",
                "escalation_count": snapshot.safety.escalation_count,
            },
        )
    if snapshot.report.completed:
        span.set_attributes(
            {
                "bike_doc.report.completed": True,
                "bike_doc.report.observed_finding_count": (
                    snapshot.report.observed_finding_count or 0
                ),
                "bike_doc.report.contributing_factor_count": (
                    snapshot.report.contributing_factor_count or 0
                ),
                "bike_doc.report.alternate_hypothesis_count": (
                    snapshot.report.alternate_hypothesis_count or 0
                ),
            }
        )
    if snapshot.outcome in {"recoverable_error", "terminal_error"}:
        span.set_status(
            Status(StatusCode.ERROR, snapshot.error_code or snapshot.outcome)
        )


def _emit_completed(
    logger: structlog.stdlib.BoundLogger,
    *,
    snapshot: DiagnosticTurnTelemetrySnapshot | None,
    fields: dict[str, object],
    terminal_status: str | None = None,
    outcome: str | None = None,
) -> None:
    """Emit the one privacy-safe terminal lifecycle log for an attempt."""
    if snapshot is None:
        outcome = outcome or "terminal_error"
        data: dict[str, object] = {
            **fields,
            "outcome": outcome,
            "terminal_status": terminal_status or "unknown",
        }
    else:
        data = {
            **fields,
            "outcome": snapshot.outcome,
            "terminal_status": snapshot.terminal_status.value,
            "duration_ms": snapshot.duration_ms,
            "artifact_count": snapshot.artifact_count,
            "current_image_count": snapshot.current_image_count,
            "current_observation_count": snapshot.current_observation_count,
            "prior_observation_count": snapshot.prior_observation_count,
            "assistant_delta_count": snapshot.assistant_delta_count,
            "assistant_message_count": snapshot.assistant_message_count,
            "terminal_action_count": snapshot.terminal_action_count,
            "safety_escalated": snapshot.safety.escalated,
            "safety_state": snapshot.safety.safety_state,
            "report_completed": snapshot.report.completed,
        }
        if snapshot.agent_run_duration_ms is not None:
            data["agent_run_duration_ms"] = snapshot.agent_run_duration_ms
        if snapshot.time_to_first_output_ms is not None:
            data["time_to_first_output_ms"] = snapshot.time_to_first_output_ms
        if snapshot.input_request is not None:
            data.update(
                input_request_type=snapshot.input_request.request_type,
                input_required=snapshot.input_request.required,
            )
        if snapshot.report.completed:
            data.update(
                completion_reason=snapshot.report.completion_reason,
                observed_finding_count=snapshot.report.observed_finding_count,
                contributing_factor_count=snapshot.report.contributing_factor_count,
                alternate_hypothesis_count=snapshot.report.alternate_hypothesis_count,
            )
        if snapshot.error_code is not None:
            data["error_code"] = snapshot.error_code
    common = {
        "event_family": "diagnostic_flow",
        "component": "background",
        "telemetry_schema_version": _TELEMETRY_SCHEMA_VERSION,
    }
    if outcome in {"terminal_error"}:
        _safe_log("error", "diagnostic_turn_completed", **common, **data)
    elif outcome in {"recoverable_error", "no_terminal_action"} or (
        snapshot is not None
        and snapshot.outcome in {"recoverable_error", "no_terminal_action"}
    ):
        _safe_log("warning", "diagnostic_turn_completed", **common, **data)
    else:
        _safe_log("info", "diagnostic_turn_completed", **common, **data)


def _safe_log(level: str, event: str, **fields: object) -> None:
    """Keep lifecycle logging observational when a renderer is unavailable."""
    try:
        getattr(logger, level)(event, **fields)
    except Exception:
        return


async def execute_profile_inference_background(turn_id: str) -> None:
    """Run profile inference without affecting diagnostic turn processing."""

    settings = get_settings()
    async for session in get_session_for_database_url(settings.database_url):
        try:
            service = _build_profile_inference_service(
                session=session,
                settings=settings,
            )
            outcome = await service.process_submitted_profile_evidence(turn_id)
            logger.info(
                "profile_inference_background_finished",
                status=outcome.status.value,
                claim_count=outcome.claim_count,
                policy_mode=outcome.policy_mode,
                schema_version="bike_profile_inference.v1",
                extractor_version=settings.profile_inference_extractor_version,
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.error("profile_inference_background_failed")
        return


def _build_profile_inference_service(
    *,
    session: AsyncSession,
    settings: Settings,
) -> ProfileInferenceService:
    """Build the deep inference service around a fresh background DB session."""

    try:
        extractor: ProfileInferenceExtractor = _build_profile_inference_extractor(
            settings,
        )
    except Exception as exc:
        logger.exception("profile_inference_runtime_configuration_invalid")
        extractor = _UnavailableProfileInferenceExtractor(exc)
    return ProfileInferenceService(
        turns=RepairTurnRepository(session),
        repair_sessions=RepairSessionRepository(session),
        bikes=BikeRepository(session),
        artifacts=ArtifactRepository(session),
        runs=ProfileInferenceRunRepository(session),
        storage=get_storage_provider(settings),
        extractor=extractor,
        extractor_version=settings.profile_inference_extractor_version,
        running_lease_seconds=settings.profile_inference_timeout_seconds + 30.0,
        max_attempts=settings.profile_inference_max_attempts,
        resolver_policy=ProfileResolverPolicy.from_deployment(
            mode=settings.profile_inference_policy_mode,
            policies=settings.profile_inference_policies,
        ),
        commit=session.commit,
        rollback=session.rollback,
    )


def _build_profile_inference_extractor(
    settings: Settings,
) -> ProfileInferenceExtractor:
    """Build the configured isolated structured extractor adapter."""

    validate_profile_inference_runtime_configuration(settings)
    if settings.profile_inference_llm_provider == "vertex_ai":
        return GeminiProfileInferenceExtractor.from_vertex_ai(
            model=settings.profile_inference_model,
            timeout_seconds=settings.profile_inference_timeout_seconds,
        )
    return GeminiProfileInferenceExtractor.from_google_ai(
        model=settings.profile_inference_model,
        timeout_seconds=settings.profile_inference_timeout_seconds,
    )


def _build_background_orchestrator(
    *,
    session: AsyncSession,
    settings: Settings,
) -> DiagnosticTurnOrchestrator:
    """Rebuild the ADK orchestration graph around a fresh DB session."""

    repair_sessions = RepairSessionRepository(session)
    phase_sessions = RepairPhaseSessionRepository(session)
    events = RepairSessionEventRepository(session)
    artifacts = ArtifactRepository(session)
    storage = get_storage_provider(settings)
    observation_extractor = _build_observation_extractor_or_unavailable(settings)

    turn_service = TurnService(
        repair_sessions,
        phase_sessions,
        RepairTurnRepository(session),
        events,
        artifacts,
        commit=session.commit,
        rollback=session.rollback,
    )
    repair_session_service = RepairSessionService(
        BikeRepository(session),
        repair_sessions,
        phase_sessions=phase_sessions,
        rollback=session.rollback,
    )
    bike_profile_service = ResolvedBikeProfileService(
        BikeRepository(session),
        repair_sessions=repair_sessions,
        phase_sessions=phase_sessions,
    )
    artifact_service = ArtifactService(
        artifacts,
        repair_sessions,
        storage,
        max_upload_bytes=settings.artifact_max_upload_bytes,
        commit=session.commit,
        rollback=session.rollback,
    )
    report_service = ReportService(
        repair_sessions,
        phase_sessions,
        PhaseReportRepository(session),
        events,
        artifacts,
        cost_estimate_service=_build_cost_estimate_service(settings),
        commit=session.commit,
        rollback=session.rollback,
    )
    safety_service = DiagnosticSafetyService(
        repair_sessions,
        events,
        commit=session.commit,
        rollback=session.rollback,
    )
    tool_dependencies = DiagnosticAgentToolDependencies(
        bike_profile_service=cast(BikeProfileServiceProtocol, bike_profile_service),
        repair_history_service=cast(
            RepairHistoryServiceProtocol,
            repair_session_service,
        ),
        artifact_service=artifact_service,
        input_request_service=cast(
            DiagnosticInputRequestServiceProtocol,
            turn_service,
        ),
        safety_service=cast(SafetyServiceProtocol, safety_service),
        report_service=cast(DiagnosticReportServiceProtocol, report_service),
    )
    session_service = get_adk_session_service()
    runner = DiagnosticRunner(
        agent=create_diagnostic_agent(tool_dependencies, settings=settings),
        session_service=session_service,
    )
    return DiagnosticTurnOrchestrator(
        phase_sessions=phase_sessions,
        turns=RepairTurnRepository(session),
        repair_sessions=repair_sessions,
        events=events,
        artifacts=artifacts,
        event_service=EventService(
            events,
            repair_sessions,
            commit=session.commit,
            rollback=session.rollback,
        ),
        runner=runner,
        get_bike_profile=GetBikeProfileTool(
            cast(BikeProfileServiceProtocol, bike_profile_service),
        ),
        lookup_repair_history=LookupRepairHistoryTool(
            cast(RepairHistoryServiceProtocol, repair_session_service),
        ),
        list_diagnostic_artifacts=ListDiagnosticArtifactsTool(artifact_service),
        request_diagnostic_input=RequestDiagnosticInputTool(
            cast(DiagnosticInputRequestServiceProtocol, turn_service),
        ),
        raise_safety_flag=RaiseSafetyFlagTool(
            cast(SafetyServiceProtocol, safety_service),
        ),
        save_diagnostic_report=SaveDiagnosticReportTool(
            cast(DiagnosticReportServiceProtocol, report_service),
        ),
        visual_context=DiagnosticVisualContextService(
            turns=cast(RepairTurnRepositoryProtocol, RepairTurnRepository(session)),
            repair_sessions=repair_sessions,
            artifacts=artifacts,
            storage=storage,
            runs=ObservationExtractionRunRepository(session),
            extractor=observation_extractor,
            extractor_version=settings.observation_extraction_extractor_version,
            prompt_version=settings.observation_extraction_prompt_version,
        ),
        commit=session.commit,
        rollback=session.rollback,
    )


def _build_observation_extractor_or_unavailable(
    settings: Settings,
) -> DiagnosticObservationExtractor:
    """Keep a bad extraction configuration from blocking pixel diagnosis."""

    if settings.image_analysis_mode not in {"shadow", "enabled"}:
        return _UnavailableObservationExtractor(
            ValueError("observation extraction is inactive for this mode"),
        )
    try:
        validate_observation_extraction_runtime_configuration(settings)
        if settings.observation_extraction_llm_provider == "vertex_ai":
            return GeminiDiagnosticObservationExtractor.from_vertex_ai(
                model=settings.observation_extraction_model,
                timeout_seconds=settings.observation_extraction_timeout_seconds,
            )
        return GeminiDiagnosticObservationExtractor.from_google_ai(
            model=settings.observation_extraction_model,
            timeout_seconds=settings.observation_extraction_timeout_seconds,
        )
    except Exception as exc:
        logger.exception("observation_extraction_runtime_configuration_invalid")
        return _UnavailableObservationExtractor(exc)


def _build_cost_estimate_service(
    settings: Settings,
) -> CostEstimateServiceProtocol | None:
    """Build optional price lookup dependencies without blocking diagnostics."""

    try:
        return get_cost_estimate_service(get_price_lookup_provider(settings))
    except Exception:
        logger.info("diagnostic_background_cost_estimate_unavailable", exc_info=True)
        return None


async def _handle_background_setup_failure(
    *,
    session: AsyncSession,
    user_id: str,
    repair_session_id: str,
    turn_id: str | None,
) -> None:
    """Restore public session state and emit safe terminal events when possible."""

    repair_sessions = RepairSessionRepository(session)
    events = RepairSessionEventRepository(session)
    repair_session = await repair_sessions.get_for_update(repair_session_id)
    if repair_session is None:
        return
    if repair_session.user_id != user_id:
        return
    if turn_id is None:
        repair_session.status = RepairSessionStatus.AWAITING_USER.value
        repair_session.updated_at = datetime.now(UTC)
        await session.commit()
        return

    event_service = EventService(
        events,
        repair_sessions,
        commit=session.commit,
        rollback=session.rollback,
    )
    await event_service.append_event(
        repair_session_id=repair_session.id,
        turn_id=turn_id,
        event_type=RepairSessionEventType.ERROR,
        data={
            "code": "diagnostic_processing_error",
            "message": "Diagnostic processing could not be started.",
            "retryable": True,
        },
    )

    repair_session = await repair_sessions.get_for_update(repair_session_id)
    if repair_session is None or repair_session.user_id != user_id:
        return
    repair_session.status = RepairSessionStatus.AWAITING_USER.value
    repair_session.updated_at = datetime.now(UTC)
    await event_service.append_event(
        repair_session_id=repair_session.id,
        turn_id=turn_id,
        event_type=RepairSessionEventType.TURN_COMPLETED,
        data={
            "turn_id": turn_id,
            "session": repair_session_from_model(repair_session).model_dump(
                mode="json",
            ),
        },
    )
