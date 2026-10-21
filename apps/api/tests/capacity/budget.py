"""Finite workload definitions, synthetic privacy markers, and test-topology gates."""

import math
from pathlib import Path

ROOT = Path("/evidence")
CONTENT = "CAPACITY_PROHIBITED_CONTENT_156"
PROVIDER = "CAPACITY_PROHIBITED_PROVIDER_156"
PATH_SENTINEL = "CAPACITY_PROHIBITED_PATH_156"
ARRIVAL_INTERVAL_SECONDS = 4.0

SCENARIOS = (
    "nats_outage",
    "worker_loss",
    "broker_restart",
    "duplicates",
    "wakeup_loss",
    "timing",
    "profile_failure",
    "burst",
    "steady",
    "profile_backlog",
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


def scenario_budgets(scenario: str) -> dict[str, float]:
    assert scenario in SCENARIOS
    budgets = dict(BUDGETS)
    if scenario == "worker_loss":
        budgets.update(diagnostic_queue_p95_seconds=180, sse_p95_seconds=40)
    elif scenario == "nats_outage":
        budgets["diagnostic_queue_p95_seconds"] = 150
        budgets["drain_seconds"] = 150
        budgets["publication_ack_p95_seconds"] = 10
    elif scenario == "broker_restart":
        budgets["diagnostic_queue_p95_seconds"] = 60
    elif scenario == "burst":
        budgets["diagnostic_queue_p95_seconds"] = 24
        budgets["acceptance_p95_seconds"] = 4
    elif scenario == "timing":
        budgets["diagnostic_queue_p95_seconds"] = 20
    elif scenario == "duplicates":
        # Running duplicates deliberately NAK until the fixed execution deadline.
        # Product completion does not shorten an already scheduled broker delay.
        budgets["transport_drain_seconds"] = 165
    return budgets


def percentile(values: list[float], p: float = 0.95) -> float:
    assert values, "missing latency samples"
    return sorted(values)[max(0, math.ceil(len(values) * p) - 1)]
