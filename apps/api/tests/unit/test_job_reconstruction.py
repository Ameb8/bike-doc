"""Finite reconstruction and retention policy decisions without infrastructure."""

from datetime import timedelta
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError
from structlog.testing import capture_logs
from test_job_maintenance_runtime import (
    NOW,
    FakeClock,
    FakePublisher,
    FakeRepository,
    maintenance,
    snapshot,
)

from bike_doc_api.core.config import Settings
from bike_doc_api.maintenance.hosting import recovery_policies
from bike_doc_api.maintenance.reconstruction import reconstruct
from bike_doc_api.workers.diagnostic import DiagnosticRecoveryPolicy
from bike_doc_api.workers.profile_inference import ProfileRecoveryPolicy


@pytest.mark.parametrize(
    "changes",
    [
        {"nats_work_max_age_seconds": 604800},
        {"job_audit_horizon_seconds": 604800},
        {"job_terminal_retention_seconds": 86400},
        {
            "job_terminal_retention_seconds": 100,
            "nats_work_max_age_seconds": 1,
            "job_audit_horizon_seconds": 0,
        },
        {"job_recovery_backoff_seconds": float("nan")},
        {"job_reconstruction_page_limit": 1001},
        {"job_cleanup_batch_limit": 0},
    ],
)
def test_invalid_retention_and_limits(changes: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        Settings(**changes)


def test_retention_strictly_exceeds_each_horizon() -> None:
    assert Settings(job_terminal_retention_seconds=172801)
    policies = recovery_policies(Settings(job_recovery_backoff_seconds=60))
    for policy in policies.values():
        assert policy.expired_outcome(snapshot()).retry_after == timedelta(seconds=60)


@pytest.mark.parametrize(
    "policy", [DiagnosticRecoveryPolicy(30), ProfileRecoveryPolicy(30)]
)
def test_recovery_exhausts_budget_and_preserves_attempt(policy: object) -> None:
    job = snapshot(state="running", attempt_count=3)
    assert policy.expired_outcome(job).state == "dead"
    assert job.attempt_count == 3


async def test_cleanup_uses_retention_cutoff() -> None:
    repo = FakeRepository()
    repo.scans["retention"] = [snapshot(state="succeeded")]
    repo.delete_retained = AsyncMock(return_value=True)
    assert await maintenance(repo, FakePublisher()).cleanup_once() == 1
    repo.delete_retained.assert_awaited_once_with(
        job_id=snapshot().id,
        before=NOW - timedelta(days=7),
    )


async def test_reconstruction_pages_after_topology_and_never_publishes() -> None:
    repo = FakeRepository()
    repo.reconstruction_page = AsyncMock(
        side_effect=[
            [
                snapshot(),
                snapshot(job_kind="unsupported"),
                snapshot(effect_boundary_at=NOW),
            ],
            [],
        ]
    )
    provision = AsyncMock()
    assert (
        await reconstruct(
            Settings(),
            repo.transaction,
            provision,
            recovery_policies(Settings()),
            clock=FakeClock(),
        )
        == 1
    )
    provision.assert_awaited_once()
    assert repo.advanced == [
        {
            "job_id": snapshot().id,
            "observed_generation": 3,
            "no_progress_before": None,
            "coalesce_pending": True,
        }
    ]
    assert repo.reconstruction_page.await_count == 2


async def test_incompatible_topology_does_not_touch_database() -> None:
    transaction = AsyncMock()
    with pytest.raises(ValueError):
        await reconstruct(
            Settings(), transaction, AsyncMock(side_effect=ValueError()), {}
        )
    transaction.assert_not_called()


def test_operator_failure_evidence_omits_dependency_content(monkeypatch) -> None:
    import bike_doc_api.maintenance.reconstruction as command

    monkeypatch.setattr(command, "get_settings", lambda: Settings(environment="test"))
    monkeypatch.setattr(
        command,
        "run",
        AsyncMock(
            side_effect=RuntimeError(
                "private content nats://credential:secret@internal:4222 /artifact/path",
            )
        ),
    )
    with capture_logs() as events, pytest.raises(SystemExit) as failed:
        command.main()
    assert failed.value.code == 1
    assert events == [{"event": "job_reconstruction_failed", "log_level": "error"}]
    for prohibited in ["private content", "credential", "secret", "/artifact/path"]:
        assert prohibited not in str(events)
