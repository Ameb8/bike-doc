"""Diagnostic typed dispatch, preparation ordering, and process ownership."""

import asyncio
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from google.adk.sessions import InMemorySessionService

from bike_doc_api.adk import resources as resource_module
from bike_doc_api.adk.diagnostic_model import (
    BoundedDiagnosticModel,
    DiagnosticModelUnavailableError,
)
from bike_doc_api.adk.execution import DiagnosticResources
from bike_doc_api.adk.sessions import StaleInMemoryADKSessionError
from bike_doc_api.core.config import Settings
from bike_doc_api.models._ids import generate_prefixed_ulid
from bike_doc_api.repositories.background_jobs import JobError, JobOutcome, JobSnapshot
from bike_doc_api.schemas.background_jobs import DiagnosticTurnInputV1
from bike_doc_api.workers import diagnostic_application, diagnostic_role
from bike_doc_api.workers.diagnostic import (
    DiagnosticPreparationError,
    DiagnosticRecoveryPolicy,
    DiagnosticTurnHandler,
    diagnostic_policy,
    diagnostic_registry,
)
from bike_doc_api.workers.registry import ClaimedJob, EffectPolicy, HandlerRegistry


def claimed() -> ClaimedJob:
    return ClaimedJob(
        generate_prefixed_ulid("job_"),
        DiagnosticTurnInputV1(turn_id=generate_prefixed_ulid("turn_")),
        1,
        "token",
        datetime.now(UTC) + timedelta(seconds=120),
    )


def snapshot() -> JobSnapshot:
    job = claimed()
    return JobSnapshot(
        job.id,
        "diagnostic_turn",
        "diagnostic",
        1,
        job.input.model_dump(),
        "running",
        1,
        3,
        1,
        1,
        datetime.now(UTC),
        job.execution_token,
        job.execution_deadline,
        None,
        None,
    )


async def test_exact_registry_and_complete_policy() -> None:
    app = SimpleNamespace(execute=AsyncMock())
    registry = diagnostic_registry(Settings(), DiagnosticTurnHandler(app, 10))
    row = snapshot()
    definition = registry.select(row)
    assert definition.policy.effect == EffectPolicy.FENCED
    assert definition.policy.attempt_limit == 3
    assert definition.policy.hard_duration == timedelta(seconds=120)
    assert definition.policy.timeout_grace == timedelta(seconds=5)
    assert definition.policy.settlement_reserve == timedelta(seconds=5)
    assert definition.policy.timeout_outcome.state == "interrupted"
    assert await definition.invoke(row) == JobOutcome("succeeded")
    accepted = app.execute.call_args.args[0]
    assert accepted.input.turn_id == row.input["turn_id"]
    assert accepted.id == row.id and accepted.attempt == row.attempt_count
    assert accepted.execution_token == row.execution_token
    assert accepted.execution_deadline == row.execution_deadline
    assert (
        registry.validate(replace(row, input={**row.input, "text": "private"}))
        == JobError.INPUT_INVALID
    )
    assert (
        registry.validate(replace(row, input_version=2)) == JobError.VERSION_UNSUPPORTED
    )
    assert (
        registry.validate(replace(row, job_kind="diagnostic_turn.v1"))
        == JobError.DEFINITION_UNKNOWN
    )
    assert (
        registry.validate(replace(row, attempt_limit=4)) == JobError.PERMANENT_FAILURE
    )
    with pytest.raises(ValueError):
        HandlerRegistry("diagnostic", [definition, definition])
    with pytest.raises(ValueError):
        HandlerRegistry("profile_inference", [definition])
    with pytest.raises(ValueError):
        replace(definition, policy=None)
    recovery = DiagnosticRecoveryPolicy()
    assert recovery.expired_outcome(row) is None
    assert recovery.may_republish(row)
    assert not recovery.may_republish(
        replace(row, effect_boundary_at=datetime.now(UTC))
    )
    assert not recovery.may_republish(replace(row, attempt_count=3))


@pytest.mark.parametrize(
    "failure,outcome",
    [
        (
            DiagnosticPreparationError(JobError.PROVIDER_UNAVAILABLE, retryable=True),
            JobOutcome(
                "retrying", JobError.PROVIDER_UNAVAILABLE, timedelta(seconds=10)
            ),
        ),
        (
            DiagnosticPreparationError(JobError.PERMANENT_FAILURE, retryable=False),
            JobOutcome("dead", JobError.PERMANENT_FAILURE),
        ),
    ],
)
async def test_only_classified_preparation_failures_return_bounded_outcomes(
    failure: Exception, outcome: JobOutcome
) -> None:
    app = SimpleNamespace(execute=AsyncMock(side_effect=failure))
    assert await DiagnosticTurnHandler(app, 10)(claimed()) == outcome
    assert "private" not in repr(outcome)


@pytest.mark.parametrize(
    "error", [asyncio.CancelledError(), RuntimeError("private content")]
)
async def test_cancellation_and_unknown_failure_propagate(error: BaseException) -> None:
    app = SimpleNamespace(execute=AsyncMock(side_effect=error))
    with pytest.raises(type(error)):
        await DiagnosticTurnHandler(app, 10)(claimed())


@pytest.mark.parametrize(
    "values",
    [
        {"diagnostic_worker_handler_seconds": 30},
        {"diagnostic_worker_hard_seconds": 115},
        {"diagnostic_worker_timeout_grace_seconds": 10},
        {"diagnostic_worker_storage_seconds": 110},
        {"observation_extraction_timeout_seconds": 110},
        {"price_lookup_timeout_seconds": 110},
        {"diagnostic_worker_fetch_batch": 5},
        {"diagnostic_worker_pool_size": 5},
        {"diagnostic_worker_adk_pool_size": 3},
        {"diagnostic_worker_shutdown_seconds": 4},
        {"diagnostic_worker_progress_seconds": 15},
        {"diagnostic_worker_handler_seconds": float("nan")},
        {"diagnostic_worker_attempt_limit": 0},
    ],
)
def test_invalid_diagnostic_configuration_fails_startup(values: dict) -> None:
    with pytest.raises(ValueError):
        diagnostic_policy(Settings(**values))


@pytest.mark.parametrize(
    "failure", [None, StaleInMemoryADKSessionError(), TimeoutError("private")]
)
async def test_authoritative_loading_and_ensure_precede_orchestration(
    monkeypatch: pytest.MonkeyPatch, failure: Exception | None
) -> None:
    job = claimed()
    turn = SimpleNamespace(
        id=job.input.turn_id,
        phase="diagnostic",
        repair_session_id="rs_authoritative",
        repair_phase_session_id="phs_authoritative",
        message={"text": "accepted private text", "artifact_ids": ["art_accepted"]},
        start_event_sequence=1,
        image_analysis_mode=None,
        responds_to_input_request_id=None,
    )
    repair = SimpleNamespace(id="rs_authoritative", user_id="usr_authoritative")
    phase = SimpleNamespace(
        id="phs_authoritative",
        repair_session_id=repair.id,
        phase="diagnostic",
        diagnostic_report_schema_version="diagnostic_report.v2",
    )
    user = SimpleNamespace(id=repair.user_id)
    calls: list[str] = []
    session = SimpleNamespace(
        commit=AsyncMock(side_effect=lambda: calls.append("commit")),
        refresh=AsyncMock(),
        rollback=AsyncMock(),
    )

    @asynccontextmanager
    async def sessions():
        yield session

    turns = SimpleNamespace(
        get=AsyncMock(return_value=turn),
        count_for_phase_session_through_start_event_sequence=AsyncMock(return_value=1),
    )
    for name, row in [
        ("RepairTurnRepository", turns),
        (
            "RepairSessionRepository",
            SimpleNamespace(get=AsyncMock(return_value=repair)),
        ),
        (
            "RepairPhaseSessionRepository",
            SimpleNamespace(get=AsyncMock(return_value=phase)),
        ),
        ("UserRepository", SimpleNamespace(get=AsyncMock(return_value=user))),
    ]:
        monkeypatch.setattr(diagnostic_application, name, lambda _, row=row: row)

    async def ensure(**kwargs):
        calls.append("ensure")
        assert kwargs["phase_session"] is phase
        if failure:
            raise failure

    monkeypatch.setattr(
        diagnostic_application,
        "DiagnosticADKSessionClient",
        lambda _: SimpleNamespace(ensure_or_resume=ensure),
    )

    async def cross():
        calls.append("boundary")

    fence = SimpleNamespace(cross_boundary=cross)
    monkeypatch.setattr(
        diagnostic_application, "DiagnosticExecutionFence", lambda *_: fence
    )

    async def process(**kwargs):
        calls.append("process")
        assert kwargs == {"current_user": user, "turn": turn}
        assert turn.message == {
            "text": "accepted private text",
            "artifact_ids": ["art_accepted"],
        }
        return object()

    build = Mock(return_value=SimpleNamespace(process_turn=process))
    monkeypatch.setattr(diagnostic_application, "build_diagnostic_orchestrator", build)
    for name in ("_apply_snapshot", "_record_attempt_metrics", "_emit_completed"):
        monkeypatch.setattr(diagnostic_application, name, Mock())
    resources = DiagnosticResources(InMemorySessionService(), Mock(), Mock())
    app = diagnostic_application.DiagnosticExecutionApplication(
        sessions, Settings(), resources
    )
    outcome = await DiagnosticTurnHandler(app, 10)(job)
    if failure:
        assert calls == ["ensure"]
        build.assert_not_called()
        session.rollback.assert_awaited_once()
        assert outcome.error in {
            JobError.PERMANENT_FAILURE,
            JobError.PROVIDER_UNAVAILABLE,
        }
    else:
        assert calls == ["ensure", "commit", "boundary", "process"]
        assert outcome.state == "succeeded"
        assert build.call_args.kwargs["resources"] is resources
        assert build.call_args.kwargs["fence"] is fence


@pytest.mark.parametrize("fail_client", [False, True])
async def test_process_transports_close_even_on_partial_construction(
    monkeypatch: pytest.MonkeyPatch, fail_client: bool
) -> None:
    adk = SimpleNamespace(close=AsyncMock())
    client = SimpleNamespace(aio=SimpleNamespace(aclose=AsyncMock()), close=Mock())
    open_adk = AsyncMock(return_value=adk)
    monkeypatch.setattr(resource_module, "open_adk_session_service", open_adk)
    monkeypatch.setattr(
        resource_module, "validate_diagnostic_runtime_configuration", lambda _: None
    )
    monkeypatch.setattr(
        resource_module.genai,
        "Client",
        Mock(
            side_effect=RuntimeError("private") if fail_client else None,
            return_value=client,
        ),
    )
    if fail_client:
        with pytest.raises(RuntimeError):
            async with resource_module.diagnostic_resources(Settings()):
                pytest.fail("construction should fail")
    else:
        async with resource_module.diagnostic_resources(Settings()) as resources:
            assert resources.session_service is adk
            assert resources.model.api_client is client
        client.aio.aclose.assert_awaited_once()
        client.close.assert_called_once()
    adk.close.assert_awaited_once()
    assert open_adk.call_args.kwargs["pool_size"] == 4


async def test_process_role_delegates_runtime_and_closes_resources(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = []

    @asynccontextmanager
    async def resources(settings):
        calls.append("resources_open")
        try:
            yield object()
        finally:
            calls.append("resources_close")

    runtime = SimpleNamespace(start=Mock(side_effect=lambda: calls.append("start")))

    @asynccontextmanager
    async def worker(settings, *, resources):
        calls.append("worker_open")
        try:
            yield runtime
        finally:
            calls.append("worker_close")

    class Loop:
        def add_signal_handler(self, sig, callback):
            callback()

        def remove_signal_handler(self, sig):
            pass

    telemetry = SimpleNamespace(shutdown=AsyncMock())
    monkeypatch.setattr(diagnostic_role, "diagnostic_resources", resources)
    monkeypatch.setattr(diagnostic_role, "diagnostic_worker", worker)
    monkeypatch.setattr(diagnostic_role, "initialize_telemetry", lambda _: telemetry)
    monkeypatch.setattr(diagnostic_role.asyncio, "get_running_loop", lambda: Loop())
    await diagnostic_role.run_diagnostic_role(Settings())
    assert calls == [
        "resources_open",
        "worker_open",
        "start",
        "worker_close",
        "resources_close",
    ]
    telemetry.shutdown.assert_awaited_once()


async def test_optional_provider_and_storage_transports_are_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adk = SimpleNamespace(close=AsyncMock())
    client = SimpleNamespace(aio=SimpleNamespace(aclose=AsyncMock()), close=Mock())
    observation = SimpleNamespace(close=AsyncMock())
    price = SimpleNamespace(close=AsyncMock())

    class Storage:
        provider_name = "gcs"

        def __init__(self, **kwargs):
            self.close = AsyncMock()
            self.get_object = AsyncMock(return_value=b"image")
            self.put_object = AsyncMock()

    monkeypatch.setattr(
        resource_module, "open_adk_session_service", AsyncMock(return_value=adk)
    )
    for name in (
        "validate_diagnostic_runtime_configuration",
        "validate_observation_extraction_runtime_configuration",
        "validate_price_lookup_runtime_configuration",
    ):
        monkeypatch.setattr(resource_module, name, lambda _: None)
    monkeypatch.setattr(resource_module.genai, "Client", Mock(return_value=client))
    monkeypatch.setattr(
        resource_module.GeminiDiagnosticObservationExtractor,
        "from_google_ai",
        Mock(return_value=observation),
    )
    monkeypatch.setattr(
        resource_module.GeminiGroundedPriceProvider,
        "from_google_ai",
        Mock(return_value=price),
    )
    monkeypatch.setattr(resource_module, "GCSStorageProvider", Storage)
    # Retain the constructed instance to inspect its close callback.
    constructed = []
    original = Storage.__init__

    def initialize(self, **kwargs):
        original(self, **kwargs)
        constructed.append(self)

    monkeypatch.setattr(Storage, "__init__", initialize)
    async with resource_module.diagnostic_resources(
        Settings(
            environment="test",
            image_analysis_mode="enabled",
            artifact_storage_provider="gcs",
            artifact_gcs_bucket="test",
            price_lookup_provider="gemini_grounded",
        )
    ) as resources:
        assert resources.observation_extractor is observation
        assert resources.cost_estimate_service is not None
        assert (
            await resources.storage.get_object(path="private", bucket=None) == b"image"
        )
    for owned in (adk, observation, price, constructed[0]):
        owned.close.assert_awaited_once()
    client.aio.aclose.assert_awaited_once()
    client.close.assert_called_once()


async def test_storage_and_model_calls_are_bounded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    storage = resource_module.BoundedStorage(
        SimpleNamespace(
            provider_name="fake",
            get_object=AsyncMock(side_effect=lambda **kwargs: None),
        ),
        0.01,
    )

    async def pending(**kwargs):
        await asyncio.Future()

    storage.storage.get_object = pending
    with pytest.raises(TimeoutError):
        await storage.get_object(path="private", bucket=None)

    async def generate(self, request, stream=False):
        await asyncio.Future()
        yield None

    monkeypatch.setattr(resource_module.Gemini, "generate_content_async", generate)
    model = BoundedDiagnosticModel(
        resource_module.ProcessGemini(model="fake", client=object()), 0.01
    )
    from google.adk.models.llm_request import LlmRequest

    with pytest.raises(
        DiagnosticModelUnavailableError, match="diagnostic_model_unavailable"
    ):
        async for _ in model.generate_content_async(LlmRequest()):
            pytest.fail("timed out model must not emit")


@pytest.mark.parametrize("fail_topology", [False, True])
async def test_worker_host_disposes_pool_and_nats_on_failure(
    monkeypatch: pytest.MonkeyPatch, fail_topology: bool
) -> None:
    calls = []
    engine = SimpleNamespace(
        dispose=AsyncMock(side_effect=lambda: calls.append("pool_close"))
    )
    construct = Mock(return_value=engine)
    session = SimpleNamespace(execute=AsyncMock())

    @asynccontextmanager
    async def sessions():
        yield session

    client = object()

    @asynccontextmanager
    async def connection(settings):
        calls.append("nats_open")
        try:
            yield client
        finally:
            calls.append("nats_close")

    js = SimpleNamespace(pull_subscribe=AsyncMock(return_value=object()))
    runtime = SimpleNamespace(
        close=AsyncMock(side_effect=lambda: calls.append("runtime_close"))
    )
    create = Mock(return_value=runtime)
    monkeypatch.setattr(diagnostic_role, "create_async_engine", construct)
    monkeypatch.setattr(
        diagnostic_role, "async_sessionmaker", lambda *args, **kwargs: sessions
    )
    monkeypatch.setattr(diagnostic_role, "nats_connection", connection)
    monkeypatch.setattr(diagnostic_role, "jetstream", lambda _: js)
    monkeypatch.setattr(
        diagnostic_role,
        "ensure_work_topology",
        AsyncMock(side_effect=RuntimeError("private") if fail_topology else None),
    )
    monkeypatch.setattr(diagnostic_role, "subscribe_work_advisories", AsyncMock())
    monkeypatch.setattr(
        diagnostic_role, "NatsPullTransport", lambda *args, **kwargs: object()
    )
    monkeypatch.setattr(diagnostic_role, "create_workload_worker", create)
    resources = DiagnosticResources(InMemorySessionService(), Mock(), Mock())
    if fail_topology:
        with pytest.raises(RuntimeError):
            async with diagnostic_role.diagnostic_worker(
                Settings(), resources=resources
            ):
                pytest.fail("failed startup")
        assert calls == ["nats_open", "nats_close", "pool_close"]
    else:
        with pytest.raises(RuntimeError, match="test exit"):
            async with diagnostic_role.diagnostic_worker(
                Settings(), resources=resources
            ) as running:
                assert running is runtime
                registry = create.call_args.args[1]
                assert registry.workload == "diagnostic"
                assert registry.select(snapshot()).kind == "diagnostic_turn"
                raise RuntimeError("test exit")
        assert calls == ["nats_open", "runtime_close", "nats_close", "pool_close"]
    assert construct.call_args.kwargs["pool_size"] == 6
    assert construct.call_args.kwargs["max_overflow"] == 0


def test_diagnostic_role_uses_its_own_runtime_capacity_and_timings() -> None:
    from bike_doc_api.workers.role import create_workload_worker

    settings = Settings(
        diagnostic_worker_concurrency=2,
        diagnostic_worker_fetch_batch=1,
        diagnostic_worker_pool_size=4,
        diagnostic_worker_adk_pool_size=2,
        diagnostic_worker_progress_seconds=0.5,
        diagnostic_worker_fetch_timeout_seconds=0.25,
        diagnostic_worker_shutdown_seconds=10,
    )
    app = SimpleNamespace(execute=AsyncMock())
    registry = diagnostic_registry(settings, DiagnosticTurnHandler(app, 10))
    runtime = create_workload_worker(settings, registry, Mock(), Mock())
    assert runtime.options.subject == settings.nats_diagnostic_subject
    assert runtime.options.concurrency == 2 and runtime.options.fetch_batch == 1
    assert runtime.options.progress_interval == 0.5
    assert runtime.options.fetch_timeout == 0.25
    assert runtime.options.shutdown_timeout == 10


async def test_orchestration_failure_spans_do_not_export_exception_content(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
        InMemorySpanExporter,
    )

    from bike_doc_api.adk import orchestration

    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    tracer = provider.get_tracer("privacy-test")
    monkeypatch.setattr(orchestration.trace, "get_tracer", lambda _: tracer)
    orchestrator = SimpleNamespace(
        visual_context=SimpleNamespace(
            prepare_turn=AsyncMock(
                side_effect=RuntimeError("private user content /private/artifact/path")
            )
        )
    )
    with pytest.raises(RuntimeError):
        await orchestration.DiagnosticTurnOrchestrator._prepare_visual_context(
            orchestrator, user_id="usr_private", turn_id="turn_test"
        )
    span = exporter.get_finished_spans()[0]
    assert span.status.description == "visual_context_failed"
    assert span.events == ()
    assert "private" not in repr(span.attributes)
    provider.shutdown()
