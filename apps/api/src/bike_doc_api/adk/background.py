"""Background diagnostic turn execution wiring."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import structlog
from opentelemetry import trace
from opentelemetry.trace import Status, StatusCode
from sqlalchemy.ext.asyncio import AsyncSession

from bike_doc_api.adk.agents.diagnostic import (
    DIAGNOSTIC_PROMPT_VERSION,
)
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
from bike_doc_api.adk.orchestration import DiagnosticTurnOrchestrator
from bike_doc_api.adk.sessions import DiagnosticADKSessionClient
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
from bike_doc_api.repositories.diagnostic_terminalization import (
    append_diagnostic_completion,
)
from bike_doc_api.repositories.events import RepairSessionEventRepository
from bike_doc_api.repositories.profile_inference import ProfileInferenceRunRepository
from bike_doc_api.repositories.repair_sessions import (
    RepairPhaseSessionRepository,
    RepairSessionRepository,
    RepairTurnRepository,
)
from bike_doc_api.repositories.users import UserRepository
from bike_doc_api.schemas.common import RepairSessionStatus
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
from bike_doc_api.services.reports import CostEstimateServiceProtocol

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
            await DiagnosticADKSessionClient(
                get_adk_session_service()
            ).ensure_or_resume(
                phase_session=phase_session,
                phase_sessions=RepairPhaseSessionRepository(session),
            )
            await session.commit()
            await session.refresh(phase_session)
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
            await session.rollback()
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
            _record_attempt_metrics(snapshot=snapshot, fields=loaded_fields)
            _emit_completed(logger, snapshot=snapshot, fields=loaded_fields)
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
    *, session: AsyncSession, settings: Settings
) -> DiagnosticTurnOrchestrator:
    return build_diagnostic_orchestrator(
        session=session,
        settings=settings,
        resources=DiagnosticResources(
            session_service=get_adk_session_service(),
            storage=get_storage_provider(settings),
            observation_extractor=_build_observation_extractor_or_unavailable(settings),
            cost_estimate_service=_build_cost_estimate_service(settings),
        ),
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

    await append_diagnostic_completion(
        events,
        repair_session,
        turn_id=turn_id,
        status="awaiting_user",
        now=datetime.now(UTC),
        processing_error=True,
    )
    await session.commit()
