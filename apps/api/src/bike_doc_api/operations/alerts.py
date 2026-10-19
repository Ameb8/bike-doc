"""Vendor-neutral validation/evaluation of reduced OTLP alert windows."""

import argparse
import json
from math import isfinite
from pathlib import Path
from typing import Any

from bike_doc_api.core.queue_contract import CONTRACT, DIMENSIONS, FIELDS

EXTERNAL = {
    "nats.storage_utilization",
    "nats.memory_utilization",
    "nats.resource_alarm",
}


def validate(contract: dict[str, Any]) -> None:
    if contract["schema_version"] != 1:
        raise ValueError("unsupported alert contract")
    if not 0 < contract["max_sample_age_seconds"] <= contract["window_seconds"]:
        raise ValueError("invalid alert freshness")
    names = set()
    for rule in contract["rules"]:
        name, metric = rule["name"], rule["metric"]
        if name in names or metric not in CONTRACT.keys() | EXTERNAL:
            raise ValueError("duplicate alert or unknown metric")
        names.add(name)
        for key, values in rule["attributes"].items():
            if (
                key not in FIELDS.get(metric, ())
                or not values
                or not set(values) <= DIMENSIONS[key]
            ):
                raise ValueError("invalid alert dimensions")
        reduce = rule["reduce"]
        expected = {"counter": {"increase"}, "histogram": {"p95"}, "gauge": {"max"}}
        allowed = (
            {"pool_utilization"}
            if metric == "pool"
            else expected.get(CONTRACT.get(metric, ("gauge",))[0], set())
        )
        if reduce not in allowed or rule["operator"] not in {"lt", "gt", "missing"}:
            raise ValueError("invalid alert reduction or operator")
        if not isfinite(rule["threshold"]) or rule["threshold"] < 0:
            raise ValueError("invalid alert threshold")


def evaluate(contract: dict[str, Any], samples: dict[str, Any]) -> list[str]:
    """Samples keyed by rule name: backend-reduced value and youngest valid age."""
    validate(contract)
    firing = []
    for rule in contract["rules"]:
        sample = samples.get(rule["name"])
        if sample is not None and (
            not isfinite(sample["age_seconds"]) or sample["age_seconds"] < 0
        ):
            raise ValueError("invalid sample freshness")
        missing = (
            sample is None or sample["age_seconds"] > contract["max_sample_age_seconds"]
        )
        if not missing:
            assert sample is not None
            value = sample["value"]
            if not isfinite(value) or value < 0 or sample["age_seconds"] < 0:
                raise ValueError("invalid reduced sample")
        if missing:
            fires = rule.get("missing_fires", False) or rule["operator"] == "missing"
        elif rule["operator"] == "missing":
            fires = False
        elif rule["operator"] == "gt":
            fires = value > rule["threshold"]
        else:
            fires = value < rule["threshold"]
        if fires:
            firing.append(rule["name"])
    return firing


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("contract", type=Path)
    parser.add_argument("--samples", type=Path)
    args = parser.parse_args()
    contract = json.loads(args.contract.read_text())
    validate(contract)
    if args.samples:
        result = evaluate(contract, json.loads(args.samples.read_text()))
        # Fixed contract names only, never telemetry payloads or credentials.
        print(json.dumps(result))  # noqa: T201


if __name__ == "__main__":
    main()
