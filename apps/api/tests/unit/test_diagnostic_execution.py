"""Fencing authority, transaction hooks and stale-error propagation without I/O."""

from dataclasses import FrozenInstanceError
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from bike_doc_api.adk.tools.common import DiagnosticToolContext, normalize_tool_errors
from bike_doc_api.core.diagnostic_execution import (
    DiagnosticExecution,
    StaleDiagnosticExecutionError,
)
from bike_doc_api.models.repair_session import RepairSession
from bike_doc_api.repositories.diagnostic_execution import DiagnosticExecutionFence
from bike_doc_api.schemas.common import RepairSessionStatus
from bike_doc_api.schemas.repair_session import InputRequestType
from bike_doc_api.services.events import EventService
from bike_doc_api.services.reports import ReportService
from bike_doc_api.services.safety import DiagnosticSafetyService
from bike_doc_api.services.turns import TurnService


def payload(phase_id: str) -> dict:
    return {
        "schema_version": "diagnostic_report.v2",
        "diagnostic_outcome": "diagnosis_supported",
        "reported_symptoms": ["Chain skips"],
        "primary_diagnosis": {
            "component": "chain",
            "issue": "Wear",
            "confidence": "medium",
            "diy_suitability": "caution",
            "supporting_finding_ids": ["symptom"],
        },
        "contributing_factors": [],
        "observed_findings": [
            {
                "finding_id": "symptom",
                "component": "chain",
                "finding": "Skipping",
                "evidence_source": "user_report",
                "evidence_source_detail": None,
                "relationship_to_symptoms": "supports_primary_diagnosis",
                "artifact_ids": [],
            }
        ],
        "alternate_hypotheses": [],
        "unresolved_uncertainties": [],
        "evidence_summary": "Reported skipping",
        "key_artifact_ids": [],
        "user_skill_level": "beginner",
        "safety_flags": [],
        "diagnostic_session_id": phase_id,
    }


def test_execution_is_immutable_and_private() -> None:
    execution = DiagnosticExecution("job", "secret-token", datetime.now(UTC))
    assert execution.token not in repr(execution)
    with pytest.raises(FrozenInstanceError):
        execution.token = "changed"
    context = DiagnosticToolContext(
        user_id="user", repair_session_id="repair", diagnostic_session_id="phase"
    )
    context._execution = execution
    assert "secret-token" not in context.model_dump_json()
    assert "_execution" not in context.model_json_schema()["properties"]
    assert str(StaleDiagnosticExecutionError()) == "diagnostic_execution_stale"


@pytest.mark.parametrize(
    "change",
    [
        {"execution_token": "replaced"},
        {"state": "interrupted"},
        {"job_kind": "profile_inference"},
        {"effect_boundary_at": None},
        {"effect_boundary_token": "other"},
        {"execution_deadline": None},
        {"execution_deadline": datetime(2020, 1, 1, tzinfo=UTC)},
    ],
)
def test_checks_locked_authority_and_rejects_without_sql_mutation(change: dict) -> None:
    now = datetime.now(UTC)
    execution = DiagnosticExecution("job", "token", now + timedelta(seconds=1))
    row = dict(
        job_kind="diagnostic_turn",
        state="running",
        execution_token="token",
        execution_deadline=execution.deadline,
        effect_boundary_at=now,
        effect_boundary_token="token",
    )
    row.update(change)
    connection = Mock()
    connection.execute.return_value.mappings.return_value.first.return_value = row
    connection.scalar.return_value = now
    session = Mock(connection=Mock(return_value=connection))
    fence = DiagnosticExecutionFence(Mock(), execution)
    with pytest.raises(StaleDiagnosticExecutionError):
        fence._require(session)
    assert connection.mock_calls[0][0] == "execute"
    assert "FOR UPDATE" in str(connection.execute.call_args.args[0])
    assert connection.execute.call_count == 1
    # Rejection is sticky, including after a caller attempts rollback/retry.
    with pytest.raises(StaleDiagnosticExecutionError):
        fence._require(session)
    assert connection.execute.call_count == 1


def test_expiry_without_replacement_and_exact_deadline() -> None:
    now = datetime.now(UTC)
    execution = DiagnosticExecution("job", "token", now)
    connection = Mock()
    connection.execute.return_value.mappings.return_value.first.return_value = dict(
        job_kind="diagnostic_turn",
        state="running",
        execution_token="token",
        execution_deadline=now,
        effect_boundary_at=now,
        effect_boundary_token="token",
    )
    connection.scalar.return_value = now
    with pytest.raises(StaleDiagnosticExecutionError):
        DiagnosticExecutionFence(Mock(), execution)._require(
            Mock(connection=Mock(return_value=connection))
        )


@pytest.mark.parametrize("operation", ["flush", "commit", "sql"])
async def test_session_hooks_guard_every_mutation_route(operation: str) -> None:
    async with AsyncSession() as session:
        fence = DiagnosticExecutionFence(
            session, DiagnosticExecution("job", "secret", datetime.now(UTC))
        )
        fence._require = Mock(side_effect=StaleDiagnosticExecutionError())
        fence.bind()
        if operation == "flush":
            session.add(RepairSession(user_id="user", bike_id="bike"))
        with pytest.raises(StaleDiagnosticExecutionError):
            if operation == "sql":
                from sqlalchemy import update

                await session.execute(
                    update(RepairSession).values(status="awaiting_user")
                )
            else:
                await getattr(session, operation)()
        await session.rollback()


@pytest.mark.parametrize("family", ["event", "input", "safety", "report", "completion"])
async def test_service_seams_preserve_stale_authority_failure(family: str) -> None:
    stale = StaleDiagnosticExecutionError()
    repo = AsyncMock()
    repo.get_owned_for_update.side_effect = stale
    repo.append_for_session.side_effect = stale
    rollback = AsyncMock()
    commit = AsyncMock()
    user = SimpleNamespace(id="user")
    with pytest.raises(StaleDiagnosticExecutionError):
        if family == "event":
            await EventService(
                repo, repo, commit=commit, rollback=rollback
            ).append_event(
                repair_session_id="repair",
                event_type="assistant.delta",
                data={"text": "x"},
            )
        elif family == "input":
            await TurnService(
                repo,
                repo,
                repo,
                repo,
                repo,
                background_jobs=AsyncMock(),
                commit=commit,
                rollback=rollback,
            ).request_diagnostic_input(
                current_user=user,
                repair_session_id="repair",
                diagnostic_session_id="phase",
                request_type=InputRequestType.TEXT,
                prompt="Describe noise",
                required=True,
                accepted_media_types=[],
                choices=[],
                min_artifacts=None,
                max_artifacts=None,
            )
        elif family == "safety":
            await DiagnosticSafetyService(
                repo, repo, commit=commit, rollback=rollback
            ).raise_safety_flag(
                current_user=user, repair_session_id="repair", safety_flag={}
            )
        elif family == "report":
            await ReportService(
                repo, repo, repo, repo, repo, commit=commit, rollback=rollback
            ).persist_diagnostic_report(
                current_user=user,
                repair_session_id="repair",
                summary="Wear",
                payload=payload("phase"),
                safety_flags=[],
                source_artifact_ids=[],
            )
        else:
            from bike_doc_api.adk.orchestration import DiagnosticTurnOrchestrator

            orchestrator = Mock(repair_sessions=repo, rollback=rollback)
            orchestrator._rollback_if_configured = AsyncMock(side_effect=rollback)
            await DiagnosticTurnOrchestrator._append_turn_completed(
                orchestrator,
                user_id="user",
                repair_session_id="repair",
                turn_id="turn",
                status=RepairSessionStatus.AWAITING_USER,
            )
    commit.assert_not_awaited()
    rollback.assert_awaited_once()
    repo.add.assert_not_awaited()


async def test_tool_normalization_does_not_feed_stale_error_back_to_model() -> None:
    with pytest.raises(StaleDiagnosticExecutionError):
        await normalize_tool_errors(
            AsyncMock(side_effect=StaleDiagnosticExecutionError())
        )


@pytest.mark.parametrize("seam", ["prepare", "agent_start", "extraction"])
async def test_visual_service_stale_failure_never_invokes_provider(seam: str) -> None:
    from bike_doc_api.services.diagnostic_visual_context import (
        DiagnosticVisualContextService,
    )

    stale = StaleDiagnosticExecutionError()
    repo, runs, extractor = AsyncMock(), AsyncMock(), AsyncMock()
    repo.get.side_effect = stale
    runs.get_by_turn_id.side_effect = stale
    runs.append_attempt.side_effect = stale
    service = DiagnosticVisualContextService(
        turns=repo,
        repair_sessions=repo,
        artifacts=repo,
        storage=AsyncMock(),
        runs=runs,
        extractor=extractor,
    )
    with pytest.raises(StaleDiagnosticExecutionError):
        if seam == "prepare":
            await service.prepare_turn(user_id="user", turn_id="turn")
        elif seam == "agent_start":
            await service.mark_diagnostic_agent_started(turn_id="turn")
        else:
            await service._extract_once(
                run=SimpleNamespace(id="run", provider="fake", model="fake"),
                images=(),
                turn_started=0,
            )
    extractor.extract.assert_not_awaited()
    runs.mark_failed.assert_not_awaited()
    runs.finish_attempt.assert_not_awaited()


async def test_runner_and_model_reject_before_external_invocation() -> None:
    from bike_doc_api.adk.diagnostic_model import BoundedDiagnosticModel
    from bike_doc_api.adk.runner import DiagnosticRunner

    reserve = AsyncMock(side_effect=StaleDiagnosticExecutionError())
    invoker = Mock()
    runner = DiagnosticRunner(invoker=invoker, reserve_external=reserve)
    with pytest.raises(StaleDiagnosticExecutionError):
        async for _ in runner.stream(Mock()):
            pytest.fail("stale authority must not become a runner event")
    invoker.assert_not_called()
    delegate = Mock(model="fake")
    model = BoundedDiagnosticModel(delegate, 1, reserve_external=reserve)
    with pytest.raises(StaleDiagnosticExecutionError):
        async for _ in model.generate_content_async(Mock()):
            pytest.fail("stale authority must not call the model")
    delegate.generate_content_async.assert_not_called()


def test_runner_content_and_adk_state_omit_execution_authority() -> None:
    from bike_doc_api.adk.runner import (
        DiagnosticRunnerRequest,
        _content_from_request,
        _state_delta_from_request,
    )

    execution = DiagnosticExecution("job", "secret-token", datetime.now(UTC))
    request = DiagnosticRunnerRequest(
        user_id="user",
        user_skill_level="unknown",
        repair_session_id="repair",
        turn_id="turn",
        diagnostic_session_id="phase",
        adk_session_id="adk",
        message_text="Chain noise",
        artifact_ids=(),
        bike_profile=None,
        execution=execution,
    )
    assert "secret-token" not in repr(request)
    assert "secret-token" not in str(_state_delta_from_request(request))
    assert "execution" not in _state_delta_from_request(request)["app_context"]
    assert "secret-token" not in _content_from_request(request).model_dump_json()
