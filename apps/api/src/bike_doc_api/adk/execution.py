"""Reusable diagnostic graph shared by legacy and external executors."""

from __future__ import annotations

from dataclasses import dataclass
from typing import cast

from google.adk.models.base_llm import BaseLlm
from google.adk.sessions import BaseSessionService
from sqlalchemy.ext.asyncio import AsyncSession

from bike_doc_api.adk.agents.diagnostic import (
    create_diagnostic_agent,
)
from bike_doc_api.adk.diagnostic_model import BoundedDiagnosticModel
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
from bike_doc_api.core.config import (
    Settings,
)
from bike_doc_api.providers.storage import StorageProvider
from bike_doc_api.repositories.artifacts import ArtifactRepository
from bike_doc_api.repositories.bikes import BikeRepository
from bike_doc_api.repositories.diagnostic_execution import DiagnosticExecutionFence
from bike_doc_api.repositories.events import RepairSessionEventRepository
from bike_doc_api.repositories.observation_extraction import (
    ObservationExtractionRunRepository,
)
from bike_doc_api.repositories.repair_sessions import (
    RepairPhaseSessionRepository,
    RepairSessionRepository,
    RepairTurnRepository,
)
from bike_doc_api.repositories.reports import PhaseReportRepository
from bike_doc_api.services.artifacts import ArtifactService
from bike_doc_api.services.bikes import ResolvedBikeProfileService
from bike_doc_api.services.diagnostic_completion_telemetry import (
    default_diagnostic_completion_telemetry,
)
from bike_doc_api.services.diagnostic_visual_context import (
    DiagnosticVisualContextService,
    RepairTurnRepositoryProtocol,
)
from bike_doc_api.services.events import EventService
from bike_doc_api.services.observation_extraction import (
    DiagnosticObservationExtractor,
)
from bike_doc_api.services.repair_sessions import RepairSessionService
from bike_doc_api.services.reports import CostEstimateServiceProtocol, ReportService
from bike_doc_api.services.safety import DiagnosticSafetyService
from bike_doc_api.services.turns import TurnService


@dataclass(frozen=True)
class DiagnosticResources:
    """Process-owned transports only; never retain domain sessions or rows."""

    session_service: BaseSessionService
    storage: StorageProvider
    observation_extractor: DiagnosticObservationExtractor
    cost_estimate_service: CostEstimateServiceProtocol | None = None
    model: BaseLlm | None = None


def build_diagnostic_orchestrator(
    *,
    session: AsyncSession,
    settings: Settings,
    resources: DiagnosticResources,
    fence: DiagnosticExecutionFence | None = None,
) -> DiagnosticTurnOrchestrator:
    """Rebuild the ADK orchestration graph around a fresh DB session."""

    if fence is not None:
        fence.bind()
    reserve_external = fence.reserve_external if fence is not None else None
    repair_sessions = RepairSessionRepository(session)
    phase_sessions = RepairPhaseSessionRepository(session)
    events = RepairSessionEventRepository(session)
    artifacts = ArtifactRepository(session)
    storage = resources.storage
    observation_extractor = resources.observation_extractor

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
        cost_estimate_service=resources.cost_estimate_service,
        reserve_external=fence.require_external if fence is not None else None,
        commit=session.commit,
        rollback=session.rollback,
    )
    safety_service = DiagnosticSafetyService(
        repair_sessions,
        events,
        commit=session.commit,
        rollback=session.rollback,
    )
    telemetry = default_diagnostic_completion_telemetry()
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
        telemetry=telemetry,
        execution=fence.execution if fence is not None else None,
    )
    session_service = resources.session_service
    agent = create_diagnostic_agent(tool_dependencies, settings=settings)
    if resources.model is not None:
        agent.model = BoundedDiagnosticModel(
            resources.model,
            settings.diagnostic_agent_timeout_seconds,
            reserve_external=reserve_external,
        )
    runner = DiagnosticRunner(
        agent=agent,
        session_service=session_service,
        settings=settings,
        reserve_external=reserve_external,
    )
    return DiagnosticTurnOrchestrator(
        execution=fence.execution if fence is not None else None,
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
        telemetry=telemetry,
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
            telemetry=telemetry,
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
            reserve_external=reserve_external,
        ),
        commit=session.commit,
        rollback=session.rollback,
    )
