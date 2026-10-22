"""Validate the numbered contract and resolve actual executed evidence."""

import ast
import json
import re
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[4]
API = REPO / "apps/api"
MANIFEST = Path(__file__).with_name("coverage.json")
SPEC = REPO / "docs/specs/apps/durable-job-queue.md"


def load_manifest(path: Path = MANIFEST) -> dict[str, Any]:
    manifest = json.loads(path.read_text())
    section = SPEC.read_text().split("## 17. Verification Requirements\n")[1]
    section = section.split("## 18.")[0]
    obligations = {
        int(number): " ".join(body.split())
        for number, body in re.findall(
            r"(?m)^(\d+)\. (.*?)(?=^\d+\. |\n\nCompatibility tests)",
            section,
            re.DOTALL,
        )
    }
    budget_tree = ast.parse((API / "tests/capacity/budget.py").read_text())
    scenarios = next(
        ast.literal_eval(statement.value)
        for statement in budget_tree.body
        if isinstance(statement, ast.Assign)
        and any(
            isinstance(target, ast.Name) and target.id == "SCENARIOS"
            for target in statement.targets
        )
    )
    assert len(manifest["capacity_scenarios"]) == len(scenarios)
    assert set(manifest["capacity_scenarios"]) == set(scenarios), (
        "capacity scenario coverage drift"
    )
    assert manifest["schema_version"] == 1
    assert manifest["compatibility"]
    assert set(manifest["compatibility"]) <= manifest["evidence"].keys()
    items = manifest["items"]
    assert len(items) == len(obligations) == 38, "incomplete verification matrix"
    assert {item["number"] for item in items} == set(obligations), (
        "duplicate or missing numbered obligation"
    )
    for item in items:
        assert item["obligation"] == obligations[item["number"]], (
            f"spec drift at item {item['number']}"
        )
        assert item["evidence"], f"no evidence for item {item['number']}"
        for reference in item["evidence"]:
            assert reference in manifest["evidence"], "unknown evidence reference"
    trees: dict[str, ast.Module] = {}
    for reference, evidence in manifest["evidence"].items():
        assert evidence["type"] in {
            "unit",
            "postgresql",
            "postgresql+jetstream",
            "cross-process",
            "capacity",
        }, f"invalid evidence type: {reference}"
        suite = manifest["suites"][evidence["suite"]]
        assert evidence["assertions"], f"undocumented evidence: {reference}"
        if evidence["type"] == "capacity":
            assert evidence["scenario"] in manifest["capacity_scenarios"]
            continue
        node = evidence["node"]
        module, function = node.split("::")
        assert module in suite["targets"], f"unexecuted owner: {reference}"
        if module not in trees:
            trees[module] = ast.parse((API / module).read_text())
        tree = trees[module]
        functions = [
            statement
            for statement in tree.body
            if isinstance(statement, ast.FunctionDef | ast.AsyncFunctionDef)
            and statement.name == function
        ]
        assert len(functions) == 1, f"missing executable test: {reference}"
        # Reject placeholders at registration; actual execution is mandatory too.
        assert any(
            (
                isinstance(n, ast.Assert)
                and not (isinstance(n.test, ast.Constant) and n.test.value is True)
            )
            or (
                isinstance(n, ast.Call)
                and isinstance(n.func, ast.Attribute)
                and n.func.attr == "raises"
            )
            for n in ast.walk(functions[0])
        ), f"test has no assertions: {reference}"
    return manifest


def test_passed(node: str, report: dict[str, Any]) -> bool:
    selected = [
        value
        for name, value in report["tests"].items()
        if name == node or name.startswith(node + "[")
    ]
    return bool(selected) and all(
        phases == {"setup": "passed", "call": "passed", "teardown": "passed"}
        for phases in selected
    )


def matrix_results(
    manifest: dict[str, Any],
    reports: dict[str, dict[str, Any]],
    capacity: dict[str, Any],
) -> list[dict[str, Any]]:
    scenarios = {entry["scenario"]: entry for entry in capacity.get("scenarios", [])}
    results = {}
    for name, evidence in manifest["evidence"].items():
        if evidence["type"] == "capacity":
            scenario = scenarios.get(evidence["scenario"], {})
            results[name] = (
                scenario.get("passed") is True
                and bool(scenario.get("checks"))
                and all(value is True for value in scenario["checks"].values())
            )
        else:
            report = reports.get(evidence["suite"], {})
            results[name] = report.get("exitstatus") == 0 and test_passed(
                evidence["node"], report
            )
    return [
        {
            "number": item["number"],
            "passed": all(results[name] for name in item["evidence"]),
            "evidence": {name: results[name] for name in item["evidence"]},
        }
        for item in manifest["items"]
    ]
