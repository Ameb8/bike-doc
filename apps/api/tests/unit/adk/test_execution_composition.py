"""Durable diagnostic composition preserves visual context and telemetry."""

from unittest.mock import AsyncMock

import pytest

from bike_doc_api.adk import execution
from bike_doc_api.core.config import Settings


def test_durable_composition_builds_visual_context_with_fresh_dependencies(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fresh_session = AsyncMock()
    settings = Settings(
        environment="test",
        database_url="postgresql+asyncpg://test/test",
    )
    captured: dict[str, object] = {}
    storage = object()

    class _VisualContextService:
        def __init__(self, **kwargs: object) -> None:
            captured["visual_context_kwargs"] = kwargs

    class _Orchestrator:
        def __init__(self, **kwargs: object) -> None:
            captured["orchestrator_kwargs"] = kwargs

    class _SaveDiagnosticReportTool:
        def __init__(self, *args: object, **kwargs: object) -> None:
            captured["save_report_args"] = args
            captured["save_report_kwargs"] = kwargs

    monkeypatch.setattr(execution, "TurnService", lambda *args, **kwargs: object())
    monkeypatch.setattr(
        execution,
        "RepairSessionService",
        lambda *args, **kwargs: object(),
    )
    monkeypatch.setattr(
        execution,
        "ResolvedBikeProfileService",
        lambda *args, **kwargs: object(),
    )
    monkeypatch.setattr(execution, "ArtifactService", lambda *args, **kwargs: object())
    monkeypatch.setattr(execution, "ReportService", lambda *args, **kwargs: object())
    monkeypatch.setattr(
        execution,
        "DiagnosticSafetyService",
        lambda *args, **kwargs: object(),
    )
    monkeypatch.setattr(execution, "DiagnosticRunner", lambda *args, **kwargs: object())
    monkeypatch.setattr(
        execution, "create_diagnostic_agent", lambda *args, **kwargs: object()
    )
    monkeypatch.setattr(execution, "GetBikeProfileTool", lambda *args: object())
    monkeypatch.setattr(execution, "LookupRepairHistoryTool", lambda *args: object())
    monkeypatch.setattr(
        execution, "ListDiagnosticArtifactsTool", lambda *args: object()
    )
    monkeypatch.setattr(execution, "RequestDiagnosticInputTool", lambda *args: object())
    monkeypatch.setattr(execution, "RaiseSafetyFlagTool", lambda *args: object())
    monkeypatch.setattr(
        execution, "SaveDiagnosticReportTool", _SaveDiagnosticReportTool
    )
    monkeypatch.setattr(
        execution, "DiagnosticVisualContextService", _VisualContextService
    )
    monkeypatch.setattr(execution, "DiagnosticTurnOrchestrator", _Orchestrator)

    execution.build_diagnostic_orchestrator(
        session=fresh_session,
        settings=settings,
        resources=execution.DiagnosticResources(AsyncMock(), storage, AsyncMock()),
    )

    visual_context_kwargs = captured["visual_context_kwargs"]
    assert isinstance(visual_context_kwargs, dict)
    assert visual_context_kwargs["storage"] is storage
    assert isinstance(visual_context_kwargs["turns"], execution.RepairTurnRepository)
    assert isinstance(
        visual_context_kwargs["repair_sessions"],
        execution.RepairSessionRepository,
    )
    assert isinstance(visual_context_kwargs["artifacts"], execution.ArtifactRepository)
    orchestrator_kwargs = captured["orchestrator_kwargs"]
    assert isinstance(orchestrator_kwargs, dict)
    assert orchestrator_kwargs["visual_context"] is not None
    save_report_kwargs = captured["save_report_kwargs"]
    assert isinstance(save_report_kwargs, dict)
    assert save_report_kwargs["telemetry"] is orchestrator_kwargs["telemetry"]
