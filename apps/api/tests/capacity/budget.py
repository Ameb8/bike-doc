"""Finite workload definitions, synthetic privacy markers, and test-topology gates."""

import math
from pathlib import Path
from typing import Any

ROOT = Path("/evidence")
CONTENT = "CAPACITY_PROHIBITED_CONTENT_156"
PROVIDER = "CAPACITY_PROHIBITED_PROVIDER_156"
PATH_SENTINEL = "CAPACITY_PROHIBITED_PATH_156"
ARRIVAL_INTERVAL_SECONDS = 4.0

SCENARIOS = (
    "nats_outage",
    "broker_restart",
    "duplicates",
    "wakeup_loss",
    "timing",
    "profile_failure",
    "burst",
    "steady",
    "profile_backlog",
    "worker_loss",
)
# Test-topology gates, not production SLOs. Every percentile requires samples.
BUDGETS = {
    "acceptance_p95_seconds": 2.0,
    "diagnostic_queue_p95_seconds": 8.0,
    "publication_ack_p95_seconds": 1.0,
    "sse_p95_seconds": 3.0,
    "database_connections_max": 52,
    "recovery_seconds": 180.0,
    "drain_seconds": 90.0,
    "transport_drain_seconds": 45.0,
}

# Physical pools only. SSE observes the API pool through an alias.
POOL_CEILINGS = {
    "api": {"api": 13, "sse": 13, "maintenance": 2, "adk": 1},
    "diagnostic": {"worker": 4, "adk": 2},
    "profile": {"worker": 3},
}


def validate_pools(process_role: str, observations: list[dict[str, Any]]) -> None:
    pools = [o for o in observations if o["metric"] == "pool"]
    assert pools, f"missing {process_role} pool evidence"
    ceilings = POOL_CEILINGS[process_role]
    for observation in pools:
        role = observation["attributes"]["role"]
        field = observation["attributes"]["pool"]
        assert role in ceilings, f"unexpected {process_role} pool: {role}"
        value = observation["value"]
        assert 0 <= value <= (0 if field == "overflow" else ceilings[role]), (
            f"{process_role}/{role} {field} exceeds reserved capacity"
        )


def scenario_budgets(scenario: str) -> dict[str, float]:
    assert scenario in SCENARIOS
    budgets = dict(BUDGETS)
    if scenario in {"burst", "worker_loss", "broker_restart", "timing"}:
        # These scenarios submit an unpaced two-client burst, unlike the 4s
        # arrival schedule used for steady traffic and profile isolation.
        budgets["acceptance_p95_seconds"] = 4
    if scenario == "worker_loss":
        # Pi cold restart of all six roles settled accepted work after 191s;
        # allow one 30s cleanup retry and 19s margin, preserving normal gates.
        budgets.update(
            diagnostic_queue_p95_seconds=240,
            recovery_seconds=240,
            sse_p95_seconds=240,
            sse_resumed_p95_seconds=BUDGETS["sse_p95_seconds"],
        )
    elif scenario == "nats_outage":
        budgets["outage_acceptance_p95_seconds"] = 2
        budgets["restored_acceptance_p95_seconds"] = 2
        budgets["diagnostic_queue_p95_seconds"] = 150
        budgets["drain_seconds"] = 150
        budgets["publication_ack_p95_seconds"] = 10
    elif scenario == "broker_restart":
        budgets["diagnostic_queue_p95_seconds"] = 60
    elif scenario == "burst":
        budgets["diagnostic_queue_p95_seconds"] = 24
    elif scenario == "timing":
        budgets["diagnostic_queue_p95_seconds"] = 30
    elif scenario == "duplicates":
        # Running duplicates deliberately NAK until the fixed execution deadline.
        # Product completion does not shorten an already scheduled broker delay.
        budgets["transport_drain_seconds"] = 165
    return budgets


def percentile(values: list[float], p: float = 0.95) -> float:
    assert values, "missing latency samples"
    return sorted(values)[max(0, math.ceil(len(values) * p) - 1)]
