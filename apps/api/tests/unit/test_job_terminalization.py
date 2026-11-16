"""Job transition composition dispatches only registered terminal policies."""

from contextlib import nullcontext
from unittest.mock import AsyncMock, MagicMock

import pytest

from bike_doc_api.models.background_job import BackgroundJob
from bike_doc_api.repositories.job_terminalization import JobTerminalizationRepository


@pytest.mark.asyncio
async def test_registered_policy_runs_before_terminal_flush() -> None:
    session = MagicMock()
    session.no_autoflush = nullcontext()
    session.flush = AsyncMock()
    policy = AsyncMock()
    row = BackgroundJob(job_kind="interactive", input_version=1, state="dead")
    repository = JobTerminalizationRepository(session, {"interactive": policy})

    async def reconcile(*args: object) -> None:
        assert not session.flush.await_count
        row.state = "succeeded"

    policy.side_effect = reconcile
    await repository._persist_transition(row)
    policy.assert_awaited_once_with(session, row)
    session.flush.assert_awaited_once()
    assert row.state == "succeeded"


@pytest.mark.asyncio
async def test_unregistered_and_nonterminal_transitions_do_not_call_policy() -> None:
    session = MagicMock()
    session.no_autoflush = nullcontext()
    session.flush = AsyncMock()
    policy = AsyncMock()
    repository = JobTerminalizationRepository(session, {"interactive": policy})
    await repository._persist_transition(
        BackgroundJob(job_kind="unknown", input_version=1, state="dead")
    )
    await repository._persist_transition(
        BackgroundJob(job_kind="interactive", input_version=1, state="retrying")
    )
    policy.assert_not_awaited()
    assert session.flush.await_count == 2
