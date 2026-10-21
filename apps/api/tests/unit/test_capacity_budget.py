"""Resource-budget regressions without infrastructure or provider calls."""

import importlib.util
import json
from pathlib import Path

import pytest

from bike_doc_api.core.config import Settings
from bike_doc_api.db.session import get_sessionmaker
from bike_doc_api.workers.diagnostic import diagnostic_policy
from bike_doc_api.workers.profile_inference import profile_policy


def test_v1_topology_reserves_database_and_acknowledgement_capacity() -> None:
    settings = Settings(environment="test")
    api = (
        settings.api_pool_size
        + settings.api_adk_pool_size
        + settings.maintenance_pool_size
    )
    diagnostic = (
        settings.diagnostic_worker_pool_size + settings.diagnostic_worker_adk_pool_size
    )
    profile = settings.profile_worker_pool_size
    assert 2 * (api + diagnostic + profile) == 50
    assert (
        2 * settings.diagnostic_worker_concurrency * 2
        <= settings.nats_diagnostic_max_ack_pending
    )
    assert 2 * settings.worker_concurrency * 2 <= settings.nats_profile_max_ack_pending
    # Delayed retry NAKs retain transport slots after releasing the semaphore.
    # At 0.25 jobs/s and ~25s retry occupancy, seven slots plus margin need eight.
    assert settings.nats_profile_max_ack_pending == 8
    for policy in (diagnostic_policy(settings), profile_policy(settings)):
        assert policy.hard_duration.total_seconds() > 0


def test_default_and_explicit_api_pools_share_one_bounded_engine() -> None:
    settings = Settings(environment="test")
    url = "postgresql+asyncpg://capacity:capacity@localhost/capacity"
    implicit = get_sessionmaker(url)
    explicit = get_sessionmaker(
        url, settings.api_pool_size, settings.database_pool_timeout_seconds
    )
    reserved = get_sessionmaker(
        url,
        settings.maintenance_pool_size,
        settings.database_pool_timeout_seconds,
        "maintenance",
    )
    assert implicit is explicit
    assert implicit is not reserved
    pool = implicit.kw["bind"].sync_engine.pool
    assert pool.size() == 13 and pool._max_overflow == 0


@pytest.mark.parametrize("kind", ["diagnostic", "profile"])
@pytest.mark.parametrize("fault", ["ack_capacity", "progress", "shutdown", "deadline"])
def test_incoherent_workload_budget_is_rejected(kind: str, fault: str) -> None:
    changes = {}
    diagnostic = kind == "diagnostic"
    if fault == "ack_capacity":
        changes[
            "nats_diagnostic_max_ack_pending"
            if diagnostic
            else "nats_profile_max_ack_pending"
        ] = 1
        if not diagnostic:
            changes.update(worker_concurrency=2, profile_worker_pool_size=4)
    elif fault == "progress":
        changes["nats_ack_wait_seconds"] = 15
    elif fault == "shutdown":
        changes[
            "diagnostic_worker_shutdown_seconds"
            if diagnostic
            else "worker_shutdown_seconds"
        ] = 1
    else:
        changes[
            "diagnostic_worker_hard_seconds"
            if diagnostic
            else "profile_worker_hard_seconds"
        ] = 40
    settings = Settings(environment="test", **changes)
    with pytest.raises(ValueError):
        (diagnostic_policy if diagnostic else profile_policy)(settings)


def test_deployment_budget_matches_typed_defaults_and_env_template() -> None:
    root = Path(__file__).resolve().parents[4]
    capacity = json.loads((root / "deploy/capacity.json").read_text())
    template = (root / ".env.example").read_text()
    defaults = Settings(environment="test")
    for values in capacity.values():
        for key, value in values.items():
            assert getattr(defaults, key) == value
            assert f"BIKE_DOC_API_{key.upper()}={value}\n" in template


def test_restoration_budget_keeps_the_connected_publication_gate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    capacity = Path(__file__).resolve().parents[1] / "capacity"
    monkeypatch.syspath_prepend(str(capacity))
    from budget import scenario_budgets

    normal = scenario_budgets("steady")
    restoration = scenario_budgets("nats_outage")
    assert (
        restoration["publication_ack_p95_seconds"]
        == Settings().job_publish_timeout_seconds
    )
    assert normal["publication_ack_p95_seconds"] == 1
    assert scenario_budgets("steady") == normal


@pytest.mark.parametrize(
    "surface",
    [
        "application.log",
        "driver.log",
        "topology.log",
        "broker.json",
        "traces.jsonl",
        "metrics.jsonl",
        "signals-api-synthetic.jsonl",
        "progress-profile-synthetic.jsonl",
    ],
)
def test_certification_rejects_content_on_every_operational_surface(
    surface: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    capacity = Path(__file__).resolve().parents[1] / "capacity"
    monkeypatch.syspath_prepend(str(capacity))
    spec = importlib.util.spec_from_file_location(
        "capacity_certify", capacity / "certify.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    (tmp_path / "result.json").write_text(json.dumps({"private_identities": []}))
    for name in (
        "application.log",
        "driver.log",
        "topology.log",
        "broker.json",
        "traces.jsonl",
        "metrics.jsonl",
        surface,
    ):
        (tmp_path / name).write_text("{}")
    sentinel = "synthetic-private-content"
    (tmp_path / surface).write_text(sentinel)
    with pytest.raises(AssertionError, match=f"privacy failure on {surface}"):
        module.evaluate(tmp_path, [sentinel])
