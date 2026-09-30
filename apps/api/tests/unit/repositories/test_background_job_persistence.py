"""Real PostgreSQL lifecycle, constraint and independent-session race tests."""

import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from pydantic import ValidationError
from sqlalchemy import func, select, text, update
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from bike_doc_api.db.base import Base
from bike_doc_api.models._ids import generate_prefixed_ulid
from bike_doc_api.models.background_job import BackgroundJob
from bike_doc_api.repositories.background_jobs import (
    BackgroundJobRepository,
    DeliveryResolution,
    ExecutionPolicy,
    JobDefinitionConflictError,
    JobError,
    JobOutcome,
    JobRecord,
    JobSnapshot,
    ResolutionKind,
    TransportError,
)
from bike_doc_api.schemas.background_jobs import ProfileInferenceInputV1
from bike_doc_api.services.background_jobs import BackgroundJobService


async def create_job(session: AsyncSession, *, attempts: int = 3) -> JobSnapshot:
    return await BackgroundJobService(
        BackgroundJobRepository(session)
    ).record_profile_inference(
        ProfileInferenceInputV1(
            turn_id=generate_prefixed_ulid("turn_"),
            inference_schema_version="bike_profile_inference.v1",
            extractor_version="drivetrain-specifications.v1",
        ),
        attempt_limit=attempts,
    )


def validate(job: JobSnapshot) -> ExecutionPolicy | JobError:
    if job.job_kind != "profile_inference":
        return JobError.DEFINITION_UNKNOWN
    if job.input_version != 1:
        return JobError.VERSION_UNSUPPORTED
    try:
        ProfileInferenceInputV1.model_validate(job.input)
    except ValidationError:
        return JobError.INPUT_INVALID
    return ExecutionPolicy(timedelta(minutes=1))


async def resolve(
    session: AsyncSession, job: JobSnapshot, **kwargs: object
) -> DeliveryResolution:
    return await BackgroundJobRepository(session).resolve_delivery(
        job_id=job.id,
        generation=kwargs.get("generation", job.desired_generation),
        workload_class=kwargs.get("workload_class", job.workload_class),
        validate_definition=validate,
    )


async def claim(session: AsyncSession, job: JobSnapshot) -> JobSnapshot:
    result = await resolve(session, job)
    assert result.kind == ResolutionKind.EXECUTABLE
    assert result.job is not None
    assert result.job.execution_token is not None
    assert result.job.execution_deadline is not None
    return result.job


async def test_migrated_table_matches_sqlalchemy_metadata(
    db_session: AsyncSession,
) -> None:
    connection = await db_session.connection()

    def compare(sync_connection: object) -> list[object]:
        context = MigrationContext.configure(
            sync_connection,
            opts={
                "include_object": lambda obj, name, kind, reflected, compare_to: (
                    name == "background_jobs" if kind == "table" else True
                )
            },
        )
        return compare_metadata(context, Base.metadata)

    assert await connection.run_sync(compare) == []


async def test_recording_rolls_back_with_callers_transaction(
    db_session: AsyncSession,
) -> None:
    job = await create_job(db_session)
    assert await db_session.get(BackgroundJob, job.id) is not None
    await db_session.rollback()
    assert await db_session.get(BackgroundJob, job.id) is None


async def test_concurrent_creation_resolves_one_logical_job(
    db_session: AsyncSession,
) -> None:
    assert db_session.bind is not None
    sessions = async_sessionmaker(db_session.bind, expire_on_commit=False)
    model = ProfileInferenceInputV1(
        turn_id=generate_prefixed_ulid("turn_"),
        inference_schema_version="bike_profile_inference.v1",
        extractor_version="extractor.v1",
    )

    async def record() -> str:
        async with sessions() as session:
            job = await BackgroundJobService(
                BackgroundJobRepository(session)
            ).record_profile_inference(model, attempt_limit=3)
            await session.commit()
            return job.id

    ids = await asyncio.gather(*(record() for _ in range(8)))
    assert len(set(ids)) == 1
    assert await db_session.scalar(select(func.count()).select_from(BackgroundJob)) == 1


@pytest.mark.parametrize(
    "changed",
    [
        {"input": {"turn_id": "different"}},
        {"workload_class": "other"},
        {"input_version": 2},
        {"attempt_limit": 4},
    ],
)
async def test_identity_conflicts_do_not_silently_reuse_immutable_definition(
    db_session: AsyncSession, changed: dict[str, object]
) -> None:
    job = await create_job(db_session)
    row = await db_session.get(BackgroundJob, job.id)
    assert row is not None
    original = JobRecord(
        row.job_kind,
        row.workload_class,
        row.input_version,
        row.input,
        row.deduplication_key,
        row.attempt_limit,
    )
    with pytest.raises(
        JobDefinitionConflictError, match=r"^logical job definition conflict$"
    ):
        await BackgroundJobRepository(db_session).record(replace(original, **changed))
    assert (await resolve(db_session, job)).job.input == job.input


async def test_only_one_concurrent_delivery_consumes_an_attempt(
    db_session: AsyncSession,
) -> None:
    job = await create_job(db_session)
    await db_session.commit()
    assert db_session.bind is not None
    sessions = async_sessionmaker(db_session.bind, expire_on_commit=False)

    async def deliver() -> DeliveryResolution:
        async with sessions() as session:
            result = await resolve(session, job)
            await session.commit()
            return result

    results = await asyncio.gather(*(deliver() for _ in range(8)))
    assert sum(r.kind == ResolutionKind.EXECUTABLE for r in results) == 1
    assert sum(r.kind == ResolutionKind.RUNNING for r in results) == 7
    assert {r.job.attempt_count for r in results} == {1}
    assert len({r.job.execution_token for r in results}) == 1
    assert all(
        r.wait_until == r.job.execution_deadline
        for r in results
        if r.kind == ResolutionKind.RUNNING
    )


async def test_stale_future_and_wrong_workload_do_not_mutate_job(
    db_session: AsyncSession,
) -> None:
    job = await create_job(db_session)
    repo = BackgroundJobRepository(db_session)
    assert await repo.advance_publication(
        job_id=job.id, observed_generation=1, no_progress_before=datetime.now(UTC)
    )
    assert (await resolve(db_session, job)).kind == ResolutionKind.STALE
    for kwargs in (
        {"generation": 3},
        {"generation": 0},
        {"generation": 2, "workload_class": "interactive"},
    ):
        result = await resolve(db_session, job, **kwargs)
        assert result.kind == ResolutionKind.PROHIBITED
        assert result.job.attempt_count == 0
        assert result.job.desired_generation == 2
        assert result.job.confirmed_generation == 0
    assert not await repo.advance_publication(
        job_id=job.id, observed_generation=1, no_progress_before=datetime.now(UTC)
    )
    missing = await repo.resolve_delivery(
        job_id="unknown",
        generation=1,
        workload_class=job.workload_class,
        validate_definition=validate,
    )
    assert missing.kind == ResolutionKind.MISSING
    assert missing.job is None


async def test_retry_eligibility_and_terminal_duplicates_are_attempt_free(
    db_session: AsyncSession,
) -> None:
    job = await create_job(db_session)
    running = await claim(db_session, job)
    repo = BackgroundJobRepository(db_session)
    assert await repo.apply_outcome(
        job_id=job.id,
        execution_token=running.execution_token,
        outcome=JobOutcome(
            "retrying", JobError.PROVIDER_UNAVAILABLE, timedelta(minutes=1)
        ),
    )
    early = await resolve(db_session, job)
    assert early.kind == ResolutionKind.EARLY
    assert early.wait_until == early.job.eligible_at
    assert early.job.attempt_count == 1
    assert not await repo.apply_outcome(
        job_id=job.id,
        execution_token=running.execution_token,
        outcome=JobOutcome("succeeded"),
    )
    await db_session.execute(
        update(BackgroundJob)
        .where(BackgroundJob.id == job.id)
        .values(eligible_at=func.clock_timestamp())
    )
    second = await claim(db_session, job)
    assert second.attempt_count == 2
    assert second.execution_token != running.execution_token
    assert not await repo.apply_outcome(
        job_id=job.id,
        execution_token=running.execution_token,
        outcome=JobOutcome("dead"),
    )
    assert await repo.apply_outcome(
        job_id=job.id,
        execution_token=second.execution_token,
        outcome=JobOutcome("succeeded"),
    )
    terminal = await resolve(db_session, job)
    assert terminal.kind == ResolutionKind.TERMINAL
    assert terminal.job.attempt_count == 2


async def test_old_confirmation_keeps_newer_intent_publishable(
    db_session: AsyncSession,
) -> None:
    job = await create_job(db_session)
    repo = BackgroundJobRepository(db_session)
    (first,) = await repo.claim_publications(
        limit=1, claim_duration=timedelta(seconds=30)
    )
    await db_session.commit()
    assert db_session.bind is not None
    sessions = async_sessionmaker(db_session.bind, expire_on_commit=False)

    # Independent transactions race an old acknowledgement against newer intent.
    async def advance() -> bool:
        async with sessions() as session:
            result = await BackgroundJobRepository(session).advance_publication(
                job_id=job.id,
                observed_generation=1,
                no_progress_before=datetime.now(UTC) + timedelta(seconds=1),
            )
            await session.commit()
            return result

    async def confirm() -> bool:
        async with sessions() as session:
            result = await BackgroundJobRepository(session).confirm_publication(first)
            await session.commit()
            return result

    assert await asyncio.gather(advance(), confirm()) == [True, True]
    (second,) = await repo.claim_publications(
        limit=1, claim_duration=timedelta(seconds=30)
    )
    assert second.generation == 2
    assert not await repo.confirm_publication(first)
    assert not await repo.confirm_publication(replace(second, generation=1))
    assert await repo.confirm_publication(second)
    assert (
        await repo.claim_publications(limit=1, claim_duration=timedelta(seconds=30))
        == []
    )
    snapshot = (await resolve(db_session, job, generation=2)).job
    assert snapshot.desired_generation == snapshot.confirmed_generation == 2


async def test_concurrent_publication_claims_are_exclusive(
    db_session: AsyncSession,
) -> None:
    await create_job(db_session)
    await db_session.commit()
    assert db_session.bind is not None
    sessions = async_sessionmaker(db_session.bind, expire_on_commit=False)

    async def publish() -> list[object]:
        async with sessions() as session:
            result = await BackgroundJobRepository(session).claim_publications(
                limit=10, claim_duration=timedelta(seconds=30)
            )
            await session.commit()
            return result

    batches = await asyncio.gather(publish(), publish())
    assert sum(len(b) for b in batches) == 1


async def test_expired_publication_claim_and_transport_failure_preserve_intent(
    db_session: AsyncSession,
) -> None:
    job = await create_job(db_session)
    repo = BackgroundJobRepository(db_session)
    (old,) = await repo.claim_publications(
        limit=1, claim_duration=timedelta(seconds=30)
    )
    await db_session.execute(
        update(BackgroundJob)
        .where(BackgroundJob.id == job.id)
        .values(publication_claim_until=func.clock_timestamp())
    )
    assert not await repo.confirm_publication(old)
    (new,) = await repo.claim_publications(
        limit=1, claim_duration=timedelta(seconds=30)
    )
    assert new.token != old.token
    assert not await repo.fail_publication(
        old, error=TransportError.TIMEOUT, retry_after=timedelta(seconds=1)
    )
    assert await repo.fail_publication(
        new, error=TransportError.UNAVAILABLE, retry_after=timedelta(seconds=30)
    )
    assert (
        await repo.claim_publications(limit=1, claim_duration=timedelta(seconds=30))
        == []
    )
    row = await db_session.get(BackgroundJob, job.id, populate_existing=True)
    assert row.desired_generation == 1
    assert row.confirmed_generation == 0
    assert row.transport_error == "unavailable"


async def expire_execution(session: AsyncSession, job: JobSnapshot) -> None:
    # Put a genuinely past, bounded execution into the fixture, without renewing
    # the claimed token/deadline (which the database correctly forbids).
    await session.execute(
        update(BackgroundJob)
        .where(BackgroundJob.id == job.id)
        .values(
            execution_token=generate_prefixed_ulid("tok_"),
            execution_started_at=func.clock_timestamp(),
            execution_deadline=func.clock_timestamp()
            + text("interval '1 millisecond'"),
        )
    )
    await session.commit()
    await asyncio.sleep(0.02)


async def test_expired_deadline_rejects_current_and_replaced_tokens(
    db_session: AsyncSession,
) -> None:
    job = await create_job(db_session)
    original = await claim(db_session, job)
    await expire_execution(db_session, job)
    repo = BackgroundJobRepository(db_session)
    lost = await resolve(db_session, job)
    assert lost.kind == ResolutionKind.RECOVERY_WAIT
    assert not await repo.apply_outcome(
        job_id=job.id,
        execution_token=original.execution_token,
        outcome=JobOutcome("succeeded"),
    )
    assert not await repo.apply_outcome(
        job_id=job.id,
        execution_token=lost.job.execution_token,
        outcome=JobOutcome("succeeded"),
    )
    assert await repo.recover_expired(
        job_id=job.id,
        execution_token=lost.job.execution_token,
        outcome=JobOutcome(
            "retrying", JobError.EXECUTION_LOST, timedelta(milliseconds=1)
        ),
    )
    await db_session.commit()
    await asyncio.sleep(0.01)
    recovered = await claim(db_session, job)
    assert recovered.attempt_count == 2
    assert recovered.execution_token != lost.job.execution_token
    assert not await repo.apply_outcome(
        job_id=job.id,
        execution_token=lost.job.execution_token,
        outcome=JobOutcome("succeeded"),
    )


@pytest.mark.parametrize("outcome", ["succeeded", "interrupted", "dead"])
async def test_terminal_outcomes_cannot_be_rewritten(
    db_session: AsyncSession, outcome: str
) -> None:
    job = await create_job(db_session)
    running = await claim(db_session, job)
    repo = BackgroundJobRepository(db_session)
    assert await repo.apply_outcome(
        job_id=job.id,
        execution_token=running.execution_token,
        outcome=JobOutcome(outcome),
    )
    assert not await repo.apply_outcome(
        job_id=job.id,
        execution_token=running.execution_token,
        outcome=JobOutcome("retrying", retry_after=timedelta(seconds=1)),
    )
    assert not await repo.advance_publication(
        job_id=job.id, observed_generation=1, no_progress_before=datetime.now(UTC)
    )
    with pytest.raises(DBAPIError):
        async with db_session.begin_nested():
            await db_session.execute(
                update(BackgroundJob)
                .where(BackgroundJob.id == job.id)
                .values(state="queued", terminal_at=None)
            )


async def test_last_retry_terminalizes_exhaustion(db_session: AsyncSession) -> None:
    job = await create_job(db_session, attempts=1)
    running = await claim(db_session, job)
    assert await BackgroundJobRepository(db_session).apply_outcome(
        job_id=job.id,
        execution_token=running.execution_token,
        outcome=JobOutcome("retrying", retry_after=timedelta(seconds=1)),
    )
    result = await resolve(db_session, job)
    assert result.kind == ResolutionKind.TERMINAL
    assert result.job.state == "dead"
    assert result.job.latest_error_category == "attempts_exhausted"
    assert result.job.attempt_count == 1


@pytest.mark.parametrize(
    "mutation,error",
    [
        ({"job_kind": "unknown"}, JobError.DEFINITION_UNKNOWN),
        ({"input_version": 2}, JobError.VERSION_UNSUPPORTED),
        ({"input": {"turn_id": "malformed"}}, JobError.INPUT_INVALID),
    ],
)
async def test_trusted_invalid_definitions_die_without_attempts(
    db_session: AsyncSession, mutation: dict[str, object], error: JobError
) -> None:
    definition = JobRecord(
        "profile_inference",
        "profile_inference",
        1,
        ProfileInferenceInputV1(
            turn_id=generate_prefixed_ulid("turn_"),
            inference_schema_version="bike_profile_inference.v1",
            extractor_version="extractor.v1",
        ).model_dump(),
        "fixture-identity",
        3,
    )
    job = await BackgroundJobRepository(db_session).record(
        replace(definition, **mutation)
    )
    result = await resolve(db_session, job)
    assert result.kind == ResolutionKind.DEFINITION_FAILURE
    assert result.job.state == "dead"
    assert result.job.latest_error_category == error.value
    assert result.job.attempt_count == 0


@pytest.mark.parametrize(
    "values",
    [
        {"attempt_count": -1},
        {"attempt_count": 4},
        {"desired_generation": 0},
        {"confirmed_generation": 2},
        {"state": "cancelled"},
        {"state": "dead"},
        {"state": "running"},
        {"terminal_at": datetime.now(UTC)},
        {
            "publication_claim_token": "token",
            "publication_claim_until": datetime.now(UTC),
        },
        {"publication_claim_generation": 1},
        {"latest_error_category": "credential secret"},
        {"transport_error": "raw exception"},
        {"eligible_at": datetime(2000, 1, 1, tzinfo=UTC)},
    ],
)
async def test_database_rejects_invalid_lifecycle_combinations(
    db_session: AsyncSession, values: dict[str, object]
) -> None:
    job = await create_job(db_session)
    with pytest.raises(IntegrityError):
        async with db_session.begin_nested():
            await db_session.execute(
                update(BackgroundJob).where(BackgroundJob.id == job.id).values(**values)
            )


@pytest.mark.parametrize(
    "values",
    [
        {"input": {}},
        {"attempt_limit": 2},
        {"deduplication_key": "other"},
        {"workload_class": "other"},
    ],
)
async def test_database_protects_immutable_definition(
    db_session: AsyncSession, values: dict[str, object]
) -> None:
    job = await create_job(db_session)
    with pytest.raises(DBAPIError):
        async with db_session.begin_nested():
            await db_session.execute(
                update(BackgroundJob).where(BackgroundJob.id == job.id).values(**values)
            )


async def test_scans_are_bounded_skip_locked_and_preserve_retention_rules(
    db_session: AsyncSession,
) -> None:
    first = await create_job(db_session)
    second = await create_job(db_session)
    repo = BackgroundJobRepository(db_session)
    before = datetime.now(UTC) + timedelta(seconds=1)
    assert (
        len(await repo.claim_scan(purpose="no_progress", before=before, limit=1)) == 1
    )
    assert await repo.claim_scan(purpose="retention", before=before, limit=10) == []
    running = await claim(db_session, first)
    assert await repo.apply_outcome(
        job_id=first.id,
        execution_token=running.execution_token,
        outcome=JobOutcome("succeeded"),
    )
    assert await repo.claim_scan(purpose="retention", before=before, limit=10) == []
    claims = await repo.claim_publications(
        limit=10, claim_duration=timedelta(seconds=30)
    )
    for publication in claims:
        assert await repo.confirm_publication(publication)
    assert [
        j.id
        for j in await repo.claim_scan(purpose="retention", before=before, limit=10)
    ] == [first.id]
    assert (
        await repo.claim_scan(
            purpose="retention", before=datetime(2000, 1, 1, tzinfo=UTC), limit=10
        )
        == []
    )
    assert [
        j.id
        for j in await repo.claim_scan(purpose="no_progress", before=before, limit=10)
    ] == [second.id]
    await db_session.commit()
    assert db_session.bind is not None
    sessions = async_sessionmaker(db_session.bind, expire_on_commit=False)
    async with sessions() as other:
        assert (
            len(await repo.claim_scan(purpose="no_progress", before=before, limit=10))
            == 1
        )
        assert (
            await BackgroundJobRepository(other).claim_scan(
                purpose="no_progress", before=before, limit=10
            )
            == []
        )


async def test_effect_boundary_blocks_replay_and_deadline_is_not_renewable(
    db_session: AsyncSession,
) -> None:
    job = await create_job(db_session)
    running = await claim(db_session, job)
    await db_session.execute(
        update(BackgroundJob)
        .where(BackgroundJob.id == job.id)
        .values(effect_boundary_at=func.clock_timestamp())
    )
    assert not await BackgroundJobRepository(db_session).apply_outcome(
        job_id=job.id,
        execution_token=running.execution_token,
        outcome=JobOutcome("retrying", retry_after=timedelta(seconds=1)),
    )
    with pytest.raises(DBAPIError):
        async with db_session.begin_nested():
            await db_session.execute(
                update(BackgroundJob)
                .where(BackgroundJob.id == job.id)
                .values(
                    execution_deadline=func.clock_timestamp()
                    + text("interval '2 minutes'")
                )
            )


async def test_concurrent_outcome_writes_accept_only_the_current_execution(
    db_session: AsyncSession,
) -> None:
    job = await create_job(db_session)
    running = await claim(db_session, job)
    await db_session.commit()
    assert db_session.bind is not None
    sessions = async_sessionmaker(db_session.bind, expire_on_commit=False)

    async def finish(outcome: JobOutcome) -> bool:
        async with sessions() as session:
            accepted = await BackgroundJobRepository(session).apply_outcome(
                job_id=job.id,
                execution_token=running.execution_token,
                outcome=outcome,
            )
            await session.commit()
            return accepted

    results = await asyncio.gather(
        finish(JobOutcome("succeeded")),
        finish(
            JobOutcome("retrying", JobError.PROVIDER_UNAVAILABLE, timedelta(minutes=1))
        ),
    )
    assert sorted(results) == [False, True]
    result = await resolve(db_session, job)
    assert result.kind in {ResolutionKind.TERMINAL, ResolutionKind.EARLY}
    assert result.job.attempt_count == 1


async def test_concurrent_conflicting_creators_cannot_change_winning_definition(
    db_session: AsyncSession,
) -> None:
    assert db_session.bind is not None
    sessions = async_sessionmaker(db_session.bind, expire_on_commit=False)
    model = ProfileInferenceInputV1(
        turn_id=generate_prefixed_ulid("turn_"),
        inference_schema_version="bike_profile_inference.v1",
        extractor_version="extractor.v1",
    )

    async def record(attempts: int) -> JobSnapshot | None:
        async with sessions() as session:
            try:
                job = await BackgroundJobService(
                    BackgroundJobRepository(session)
                ).record_profile_inference(model, attempt_limit=attempts)
                await session.commit()
                return job
            except JobDefinitionConflictError:
                await session.rollback()
                return None

    results = await asyncio.gather(record(2), record(3))
    assert sum(job is not None for job in results) == 1
    assert await db_session.scalar(select(func.count()).select_from(BackgroundJob)) == 1


async def test_publication_and_attempt_counters_cannot_decrease(
    db_session: AsyncSession,
) -> None:
    job = await create_job(db_session)
    repo = BackgroundJobRepository(db_session)
    (publication,) = await repo.claim_publications(
        limit=1, claim_duration=timedelta(seconds=30)
    )
    assert await repo.confirm_publication(publication)
    assert await repo.advance_publication(
        job_id=job.id, observed_generation=1, no_progress_before=datetime.now(UTC)
    )
    running = await claim(db_session, replace(job, desired_generation=2))
    assert await repo.apply_outcome(
        job_id=job.id,
        execution_token=running.execution_token,
        outcome=JobOutcome("retrying", retry_after=timedelta(seconds=30)),
    )
    for values in (
        {"desired_generation": 1},
        {"confirmed_generation": 0, "publication_confirmed_at": None},
        {"attempt_count": 0, "first_started_at": None},
    ):
        with pytest.raises(DBAPIError):
            async with db_session.begin_nested():
                await db_session.execute(
                    update(BackgroundJob)
                    .where(BackgroundJob.id == job.id)
                    .values(**values)
                )


async def test_maintenance_does_not_bypass_eligibility_active_execution_or_budget(
    db_session: AsyncSession,
) -> None:
    job = await create_job(db_session)
    repo = BackgroundJobRepository(db_session)
    before = datetime.now(UTC) + timedelta(seconds=1)
    assert not await repo.advance_publication(
        job_id=job.id,
        observed_generation=1,
        no_progress_before=datetime(2000, 1, 1, tzinfo=UTC),
    )
    running = await claim(db_session, job)
    assert not await repo.advance_publication(
        job_id=job.id, observed_generation=1, no_progress_before=before
    )
    assert not await repo.recover_expired(
        job_id=job.id,
        execution_token=running.execution_token,
        outcome=JobOutcome("dead"),
    )
    assert await repo.claim_scan(purpose="expired", before=before, limit=10) == []
    assert await repo.apply_outcome(
        job_id=job.id,
        execution_token=running.execution_token,
        outcome=JobOutcome("retrying", retry_after=timedelta(minutes=1)),
    )
    assert not await repo.advance_publication(
        job_id=job.id, observed_generation=1, no_progress_before=before
    )
    assert await repo.claim_scan(purpose="no_progress", before=before, limit=10) == []
    await db_session.execute(
        update(BackgroundJob)
        .where(BackgroundJob.id == job.id)
        .values(
            eligible_at=func.clock_timestamp(),
            effect_boundary_at=func.clock_timestamp(),
        )
    )
    assert not await repo.advance_publication(
        job_id=job.id, observed_generation=1, no_progress_before=before
    )


@pytest.mark.parametrize("values", [{"input": []}, {"input": {"large": "a" * 4096}}])
async def test_database_bounds_json_input(
    db_session: AsyncSession, values: dict[str, object]
) -> None:
    row = BackgroundJob(
        job_kind="profile_inference",
        workload_class="profile_inference",
        input_version=1,
        deduplication_key="fixture",
        attempt_limit=3,
        **values,
    )
    with pytest.raises(IntegrityError):
        async with db_session.begin_nested():
            db_session.add(row)
            await db_session.flush()


async def test_expired_scan_and_recovery_obey_single_attempt_budget(
    db_session: AsyncSession,
) -> None:
    job = await create_job(db_session, attempts=1)
    repo = BackgroundJobRepository(db_session)
    result = await repo.resolve_delivery(
        job_id=job.id,
        generation=1,
        workload_class=job.workload_class,
        validate_definition=lambda job: ExecutionPolicy(timedelta(milliseconds=1)),
    )
    assert result.kind == ResolutionKind.EXECUTABLE
    await db_session.commit()
    await asyncio.sleep(0.02)
    assert not await repo.apply_outcome(
        job_id=job.id,
        execution_token=result.job.execution_token,
        outcome=JobOutcome("succeeded"),
    )
    expired = await repo.claim_scan(
        purpose="expired", before=datetime.now(UTC), limit=1
    )
    assert [item.id for item in expired] == [job.id]
    assert not await repo.recover_expired(
        job_id=job.id, execution_token="replaced", outcome=JobOutcome("dead")
    )
    assert await repo.recover_expired(
        job_id=job.id,
        execution_token=result.job.execution_token,
        outcome=JobOutcome("retrying", JobError.EXECUTION_LOST, timedelta(seconds=1)),
    )
    terminal = await resolve(db_session, job)
    assert terminal.kind == ResolutionKind.TERMINAL
    assert terminal.job.state == "dead"
    assert terminal.job.attempt_count == 1
    assert terminal.job.latest_error_category == "attempts_exhausted"
    assert not await repo.recover_expired(
        job_id=job.id,
        execution_token=result.job.execution_token,
        outcome=JobOutcome("interrupted"),
    )


async def test_eligible_exhausted_delivery_terminalizes_without_an_attempt(
    db_session: AsyncSession,
) -> None:
    job = await create_job(db_session, attempts=1)
    await db_session.execute(
        update(BackgroundJob)
        .where(BackgroundJob.id == job.id)
        .values(attempt_count=1, first_started_at=func.clock_timestamp())
    )
    resolution = await resolve(db_session, job)
    assert resolution.kind == ResolutionKind.EXHAUSTED
    assert resolution.job.attempt_count == 1
    assert resolution.job.state == "dead"
    assert resolution.job.latest_error_category == "attempts_exhausted"
