"""Finite content-safe pytest evidence, enabled only by the certification runner."""

import hashlib
import json
import os
from pathlib import Path
from typing import Any

import pytest

_TESTS: dict[str, dict[str, str]] = {}
_COLLECTED: list[str] = []
_FAILURES: list[dict[str, object]] = []


def safe_node(node: str) -> str:
    """Parameter IDs can embed synthetic content; retain only an opaque digest."""
    base, separator, parameter = node.partition("[")
    if not separator:
        return base
    return base + "[" + hashlib.sha256(parameter.encode()).hexdigest()[:16] + "]"


def pytest_collection_finish(session: pytest.Session) -> None:
    _COLLECTED.extend(safe_node(item.nodeid) for item in session.items)


def pytest_runtest_logreport(report: pytest.TestReport) -> None:
    # Never serialize exception messages, locals, captures, inputs or URLs.
    outcome = "xfail" if hasattr(report, "wasxfail") else report.outcome
    _TESTS.setdefault(safe_node(report.nodeid), {})[report.when] = outcome
    if report.failed:
        crash = getattr(report.longrepr, "reprcrash", None)
        _FAILURES.append(
            {
                "node": safe_node(report.nodeid),
                "phase": report.when,
                "file": report.location[0],
                "line": crash.lineno if crash else report.location[1] + 1,
            }
        )


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    payload: dict[str, Any] = {
        "exitstatus": int(exitstatus),
        "collected": _COLLECTED,
        "tests": _TESTS,
        "failures": _FAILURES,
    }
    Path(os.environ["BIKEDOC_CERTIFICATION_REPORT"]).write_text(
        json.dumps(payload, indent=2) + "\n"
    )
