"""Certification must fail closed on missing, skipped or placeholder evidence."""

import importlib.util
import json
import signal
import subprocess
from pathlib import Path

import pytest

CODE = Path(__file__).resolve().parents[1] / "certification/evidence.py"
spec = importlib.util.spec_from_file_location("queue_certification_evidence", CODE)
assert spec and spec.loader
evidence = importlib.util.module_from_spec(spec)
spec.loader.exec_module(evidence)


def test_manifest_matches_every_canonical_obligation_and_executable_owner() -> None:
    manifest = evidence.load_manifest()
    assert len(manifest["items"]) == 38
    assert len(manifest["capacity_scenarios"]) == 10
    assert manifest["compatibility"]


@pytest.mark.parametrize(
    "fault", ["missing", "duplicate", "spec_drift", "owner", "empty", "capacity"]
)
def test_manifest_rejects_incomplete_or_unexecuted_coverage(
    tmp_path: Path, fault: str
) -> None:
    manifest = evidence.load_manifest()
    if fault == "missing":
        manifest["items"].pop()
    elif fault == "duplicate":
        manifest["items"][-1] = manifest["items"][0]
    elif fault == "spec_drift":
        manifest["items"][0]["obligation"] = "obsolete spec"
    elif fault == "owner":
        manifest["evidence"]["publication_ack_window"]["node"] += "_placeholder"
    elif fault == "capacity":
        manifest["capacity_scenarios"].pop()
    else:
        manifest["items"][0]["evidence"] = []
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(manifest))
    with pytest.raises(AssertionError):
        evidence.load_manifest(path)


@pytest.mark.parametrize("outcome", ["skipped", "failed", "xfail", "missing"])
@pytest.mark.parametrize("phase", ["setup", "call", "teardown"])
def test_required_parameterizations_cannot_skip_or_fail(
    outcome: str, phase: str
) -> None:
    node = "tests/example.py::test_required"
    passed = {"setup": "passed", "call": "passed", "teardown": "passed"}
    report = {"tests": {node + "[one]": dict(passed), node + "[two]": dict(passed)}}
    assert evidence.test_passed(node, report)
    if outcome == "missing":
        report["tests"][node + "[two]"].pop(phase)
    else:
        report["tests"][node + "[two]"][phase] = outcome
    assert not evidence.test_passed(node, report)
    assert not evidence.test_passed("tests/example.py::test_unexecuted", report)


def test_no_capacity_or_failed_budget_cannot_produce_a_certificate() -> None:
    manifest = evidence.load_manifest()
    rows = evidence.matrix_results(manifest, {}, {})
    assert len(rows) == 38 and not any(row["passed"] for row in rows)
    failed = {
        "scenarios": [
            {"scenario": name, "passed": True, "checks": {"privacy": False}}
            for name in manifest["capacity_scenarios"]
        ]
    }
    rows = evidence.matrix_results(manifest, {}, failed)
    assert not next(row for row in rows if row["number"] == 38)["passed"]


def test_failure_artifacts_never_serialize_parameters_or_exception_content(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = CODE.with_name("pytest_evidence.py")
    plugin_spec = importlib.util.spec_from_file_location("certification_observer", path)
    assert plugin_spec and plugin_spec.loader
    plugin = importlib.util.module_from_spec(plugin_spec)
    plugin_spec.loader.exec_module(plugin)
    secret = "PROHIBITED_USER_PROVIDER_CONTENT"
    node = f"tests/example.py::test_required[{secret}]"
    report = pytest.TestReport(
        nodeid=node,
        location=("tests/example.py", 12, "test_required"),
        keywords={},
        outcome="failed",
        longrepr=secret,
        when="call",
        sections=[("Captured log", secret)],
        user_properties=[("input", secret)],
    )
    plugin.pytest_runtest_logreport(report)
    output = tmp_path / "report.json"
    monkeypatch.setenv("BIKEDOC_CERTIFICATION_REPORT", str(output))
    plugin.pytest_sessionfinish(None, 1)
    encoded = output.read_text()
    assert secret not in encoded
    saved = json.loads(encoded)
    assert saved["failures"][0]["line"] == 13
    assert saved["failures"][0]["node"].startswith("tests/example.py::test_required[")
    assert len(saved["tests"]) == 1


@pytest.mark.parametrize("forced", [False, True])
def test_stage_timeout_cleans_up_and_returns_failed_evidence(
    monkeypatch: pytest.MonkeyPatch, forced: bool
) -> None:
    monkeypatch.syspath_prepend(str(CODE.parent))
    runner_spec = importlib.util.spec_from_file_location(
        "queue_certification_runner", CODE.with_name("certify.py")
    )
    assert runner_spec and runner_spec.loader
    runner = importlib.util.module_from_spec(runner_spec)
    runner_spec.loader.exec_module(runner)
    waits = []
    signals = []

    class Process:
        pid = 123

        def wait(self, *, timeout: int) -> int:
            waits.append(timeout)
            if len(waits) == 1 or (forced and len(waits) == 2):
                raise subprocess.TimeoutExpired("private command", timeout)
            return -signal.SIGINT

    monkeypatch.setattr(runner.subprocess, "Popen", lambda *a, **kw: Process())
    monkeypatch.setattr(runner.os, "killpg", lambda pid, sig: signals.append(sig))
    assert runner.invoke(["private command"], 7) is False
    assert waits == ([7, 45, 10] if forced else [7, 45])
    assert signals == ([signal.SIGINT, signal.SIGKILL] if forced else [signal.SIGINT])
