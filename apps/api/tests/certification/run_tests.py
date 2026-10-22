"""Run existing owners in migration-safe order on disposable infrastructure."""

import json
import os
import subprocess
import sys
from pathlib import Path

from evidence import load_manifest


def main() -> None:
    output = Path(sys.argv[1]).resolve()
    manifest = load_manifest()
    for name, suite in manifest["suites"].items():
        if name == "capacity":
            continue
        destination = output / f"{name}.json"
        env = dict(os.environ)
        env["BIKEDOC_CERTIFICATION_REPORT"] = str(destination)
        env["PYTHONPATH"] = str(Path(__file__).resolve().parent)
        command = [
            sys.executable,
            "-m",
            "pytest",
            "-p",
            "pytest_evidence",
            "-m",
            suite["marker"],
            *suite["targets"],
            "--tb=no",
            "--no-header",
            "--disable-warnings",
        ]
        (output / "execution.json").write_text(
            json.dumps(
                {
                    "suite": name,
                    "status": "running",
                }
            )
            + "\n"
        )
        print(f"Executing {name}", flush=True)
        result = subprocess.run(command, env=env, check=False, timeout=1800)
        if result.returncode:
            (output / "execution.json").write_text(
                json.dumps(
                    {
                        "suite": name,
                        "status": "failed",
                        "exitstatus": result.returncode,
                    }
                )
                + "\n"
            )
            sys.exit(result.returncode)
        report = json.loads(destination.read_text())
        # No successful stage can hide skips/xfails, even in unreferenced cases.
        assert report["tests"] and set(report["collected"]) == set(report["tests"])
        assert all(
            phases == {"setup": "passed", "call": "passed", "teardown": "passed"}
            for phases in report["tests"].values()
        ), f"unexecuted/skipped/failed evidence in {name}"


if __name__ == "__main__":
    main()
