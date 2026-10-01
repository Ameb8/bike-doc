"""Profile definitions, policy, and one-call application attempts through fakes."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest

from bike_doc_api.core.config import Settings
from bike_doc_api.models._ids import generate_prefixed_ulid
from bike_doc_api.repositories.background_jobs import JobError, JobSnapshot
from bike_doc_api.schemas.background_jobs import ProfileInferenceInputV1
from bike_doc_api.services.profile_inference import (
    ProfileInferenceOutcome,
    ProfileInferenceStatus,
)
from bike_doc_api.services.profile_inference_resolution import ProfileResolverPolicy
from bike_doc_api.workers.profile_inference import (
    ProfileInferenceHandler,
    profile_policy,
    profile_registry,
)
from bike_doc_api.workers.profile_role import ProfileApplication
from bike_doc_api.workers.registry import ClaimedJob, EffectPolicy


def snapshot() -> JobSnapshot:
    now = datetime.now(UTC)
    return JobSnapshot(
        generate_prefixed_ulid("job_"),
        "profile_inference",
        "profile_inference",
        1,
        ProfileInferenceInputV1(
            turn_id=generate_prefixed_ulid("turn_"),
            inference_schema_version="bike_profile_inference.v1",
            extractor_version=Settings().profile_inference_extractor_version,
        ).model_dump(),
        "running",
        1,
        3,
        1,
        1,
        now,
        "token",
        now + timedelta(seconds=60),
        None,
        None,
    )


@pytest.mark.parametrize(
    "status,state,error",
    [
        (ProfileInferenceStatus.COMPLETED, "succeeded", None),
        (ProfileInferenceStatus.ABSTAINED, "succeeded", None),
        (ProfileInferenceStatus.SKIPPED, "succeeded", None),
        (
            ProfileInferenceStatus.RETRYABLE_FAILURE,
            "retrying",
            JobError.PROVIDER_UNAVAILABLE,
        ),
        (ProfileInferenceStatus.EXHAUSTED, "dead", JobError.ATTEMPTS_EXHAUSTED),
        (ProfileInferenceStatus.TERMINAL_FAILURE, "dead", JobError.PERMANENT_FAILURE),
    ],
)
async def test_maps_committed_application_result(
    status: ProfileInferenceStatus, state: str, error: JobError | None
) -> None:
    application = AsyncMock()
    application.process_submitted_profile_evidence.return_value = (
        ProfileInferenceOutcome(status, None)
    )
    handler = ProfileInferenceHandler(application, 10)
    row = snapshot()
    outcome = await profile_registry(Settings(), handler).select(row).invoke(row)
    assert (outcome.state, outcome.error) == (state, error)
    application.process_submitted_profile_evidence.assert_awaited_once_with(
        row.input["turn_id"],
        application_attempt=1,
        inference_schema_version="bike_profile_inference.v1",
    )


async def test_unexpected_application_failure_is_not_reclassified_as_provider_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from bike_doc_api.workers import profile_role

    session = AsyncMock()

    @asynccontextmanager
    async def sessions() -> AsyncIterator[Any]:
        yield session

    failure = RuntimeError("private user content")
    handler = AsyncMock(side_effect=failure)
    monkeypatch.setattr(
        profile_role,
        "ProfileInferenceHandler",
        lambda application, retry_seconds: handler,
    )
    application = ProfileApplication(
        cast(Any, sessions),
        Settings(),
        AsyncMock(),
        AsyncMock(),
        ProfileResolverPolicy.production(),
    )
    row = snapshot()
    claimed = ClaimedJob(
        row.id,
        ProfileInferenceInputV1.model_validate(row.input),
        row.attempt_count,
        cast(str, row.execution_token),
        cast(datetime, row.execution_deadline),
    )

    with pytest.raises(RuntimeError) as raised:
        await application.handle(claimed)

    assert raised.value is failure
    session.rollback.assert_awaited_once_with()


def test_definition_rejects_unpinned_or_invalid_behavior_before_attempt() -> None:
    registry = profile_registry(Settings(), AsyncMock())
    row = snapshot()
    for field, value in (
        ("extractor_version", "unknown.v1"),
        ("inference_schema_version", "bike_profile_inference.v2"),
    ):
        assert (
            registry.validate(replace(row, input={**row.input, field: value}))
            == JobError.VERSION_UNSUPPORTED
        )
    assert (
        registry.validate(replace(row, input={**row.input, "artifact_path": "secret"}))
        == JobError.INPUT_INVALID
    )
    assert (
        registry.validate(replace(row, attempt_limit=4)) == JobError.PERMANENT_FAILURE
    )
    assert (
        registry.validate(replace(row, input_version=2)) == JobError.VERSION_UNSUPPORTED
    )


@pytest.mark.parametrize(
    "values",
    [
        {"profile_worker_handler_seconds": 20},
        {"profile_worker_hard_seconds": 55},
        {"worker_concurrency": 5},
        {"worker_fetch_batch": 5},
    ],
)
def test_incompatible_profile_timing_or_capacity_fails_startup(
    values: dict[str, object],
) -> None:
    with pytest.raises(ValueError):
        profile_policy(Settings(**values))


def test_profile_recovery_has_no_effect_boundary_and_one_attempt_budget() -> None:
    policy = profile_policy(Settings())
    assert policy.effect == EffectPolicy.IDEMPOTENT
    row = snapshot()
    assert policy.reconciliation.expired_outcome(row).state == "retrying"
    assert (
        policy.reconciliation.expired_outcome(replace(row, attempt_count=3)).error
        == JobError.ATTEMPTS_EXHAUSTED
    )
    assert (
        policy.reconciliation.expired_outcome(
            replace(row, effect_boundary_at=datetime.now(UTC))
        )
        is None
    )
    assert not policy.reconciliation.may_republish(replace(row, attempt_count=3))


@pytest.mark.parametrize("fail_startup", [False, True])
async def test_process_role_owns_provider_and_uses_shared_shutdown(
    monkeypatch: pytest.MonkeyPatch, fail_startup: bool
) -> None:
    import asyncio
    from contextlib import asynccontextmanager
    from unittest.mock import Mock

    from bike_doc_api.workers import profile_role

    extractor = AsyncMock()
    monkeypatch.setattr(
        profile_role.GeminiProfileInferenceExtractor,
        "from_google_ai",
        Mock(return_value=extractor),
    )
    handlers = []
    loop = asyncio.get_running_loop()
    monkeypatch.setattr(
        loop, "add_signal_handler", lambda sig, callback: handlers.append(callback)
    )
    monkeypatch.setattr(loop, "remove_signal_handler", lambda sig: True)
    closed = []
    runtime = Mock()
    runtime.start.side_effect = lambda: handlers[0]()

    @asynccontextmanager
    async def worker(
        settings: Settings, *, extractor: object, storage: object
    ) -> AsyncIterator[object]:
        if fail_startup:
            raise ConnectionError("private endpoint")
        try:
            yield runtime
        finally:
            closed.append(True)

    monkeypatch.setattr(profile_role, "profile_worker", worker)
    if fail_startup:
        with pytest.raises(ConnectionError):
            await profile_role.run_profile_role(Settings(environment="test"))
        runtime.start.assert_not_called()
    else:
        await profile_role.run_profile_role(Settings(environment="test"))
        runtime.start.assert_called_once()
        assert closed == [True]
    extractor.close.assert_awaited_once()


def test_process_startup_error_never_logs_config_or_exception_content(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from bike_doc_api.workers import profile_role

    def bad_settings() -> Settings:
        raise ValueError("nats://user:private@host:4222/private artifact/path user_id")

    monkeypatch.setattr(profile_role, "get_settings", bad_settings)
    with pytest.raises(SystemExit) as error:
        profile_role.main()
    assert error.value.code == 1
    captured = capsys.readouterr()
    assert "private" not in captured.out + captured.err
    assert "nats://" not in captured.out + captured.err


def test_unsupported_extractor_configuration_fails_startup() -> None:
    with pytest.raises(ValueError, match="unsupported profile extractor"):
        profile_registry(
            Settings(profile_inference_extractor_version="unknown.v1"), AsyncMock()
        )


async def test_profile_role_validates_resolver_policy_before_opening_resources(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from unittest.mock import Mock

    from bike_doc_api.core.config import ProfileInferenceFieldPolicySettings
    from bike_doc_api.workers import profile_role

    engine = Mock()
    monkeypatch.setattr(profile_role, "create_async_engine", engine)
    settings = Settings(
        profile_inference_policy_mode="evaluated",
        profile_inference_policies=[
            ProfileInferenceFieldPolicySettings(
                field_path="unknown.field",
                evidence_class="direct_visual",
                calibration_key="test",
                policy_version="test.v1",
                auto_fill_threshold=0.9,
            )
        ],
    )
    with pytest.raises(ValueError):
        async with profile_role.profile_worker(
            settings, extractor=AsyncMock(), storage=AsyncMock()
        ):
            pytest.fail("incompatible role started")
    engine.assert_not_called()
