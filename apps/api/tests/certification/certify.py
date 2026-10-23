"""Bounded whole-system certification of durable queue spec section 17."""

import argparse
import hashlib
import json
import os
import signal
import subprocess
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from evidence import API, MANIFEST, REPO, load_manifest, matrix_results, test_passed


def invoke(command: list[str], timeout: int) -> bool:
    """Discard raw output; retain only allowlisted test results and phase status."""
    process = subprocess.Popen(
        command,
        cwd=REPO,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    try:
        return process.wait(timeout=timeout) == 0
    except BaseException as error:
        # INT unwinds Python context managers and runs shell cleanup traps.
        os.killpg(process.pid, signal.SIGINT)
        try:
            process.wait(timeout=45)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=10)
        if isinstance(error, subprocess.TimeoutExpired):
            return False
        raise


def write(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, indent=2) + "\n")


def source_fingerprint() -> str:
    """Bind evidence to the actual worktree, including uncommitted test changes."""
    paths = sorted(
        [
            *API.rglob("*.py"),
            *API.joinpath("tests/capacity").glob("*.yaml"),
            *REPO.joinpath("deploy").rglob("*.*"),
            MANIFEST,
            API / "Dockerfile",
            API / "pyproject.toml",
            REPO / "uv.lock",
            REPO / "Taskfile.yml",
            REPO / "scripts/verify-backend-compatibility.sh",
            REPO / "scripts/prepare-production.py",
            REPO / "compose.production.yaml",
            REPO / "docs/specs/apps/durable-job-queue.md",
        ]
    )
    digest = hashlib.sha256()
    for path in paths:
        if ".venv" not in path.parts:
            digest.update(str(path.relative_to(REPO)).encode())
            digest.update(path.read_bytes())
    return digest.hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--validate-only", action="store_true")
    parser.add_argument(
        "--functional-only",
        action="store_true",
        help="iteration only: omit capacity; cannot issue a passing certificate",
    )
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    manifest = load_manifest()
    if args.validate_only:
        print("Validated all 38 numbered obligations and executable owners")
        return
    output = args.output or REPO / ".certification" / datetime.now(UTC).strftime(
        "run-%Y%m%d-%H%M%S"
    )
    output = output.resolve()
    output.mkdir(parents=True, exist_ok=False, mode=0o700)
    record: dict[str, object] = {
        "schema_version": 1,
        "manifest_sha256": hashlib.sha256(MANIFEST.read_bytes()).hexdigest(),
        "passed": False,
        "mode": "functional" if args.functional_only else "full",
        "stages": {},
        "runs": [],
    }
    stages = record["stages"]
    assert isinstance(stages, dict)
    runs = record["runs"]
    assert isinstance(runs, list)
    try:
        # Ordered commands, not Task deps (which may run concurrently).
        for target in ("format", "check"):
            print(f"Running task {target}", flush=True)
            stages[target] = invoke(["task", target], 900)
            assert stages[target], f"task {target} failed"
            if target == "format":
                record["source_sha256"] = source_fingerprint()
            else:
                assert source_fingerprint() == record["source_sha256"], (
                    "source changed during task check; rerun"
                )
        for repeat in range(1, 3):
            run = output / str(repeat)
            run.mkdir(mode=0o700)
            print(f"Clean infrastructure run {repeat}/2: functional matrix", flush=True)
            functional = invoke(
                [
                    "bash",
                    "scripts/verify-backend-compatibility.sh",
                    "certification",
                    str(run),
                ],
                2400,
            )
            stages[f"functional_{repeat}"] = functional
            reports = {
                path.stem: json.loads(path.read_text()) for path in run.glob("*.json")
            }
            assert source_fingerprint() == record["source_sha256"], (
                "source changed during functional certification; rerun"
            )
            capacity = {}
            capacity_ok = False
            if functional and not args.functional_only:
                print(
                    f"Clean infrastructure run {repeat}/2: tuned capacity/privacy",
                    flush=True,
                )
                # Existing raw captures are private and finite; none are retained
                # by this certification. Only its allowlisted report survives.
                with tempfile.TemporaryDirectory(
                    prefix="capacity-", dir=run
                ) as temporary:
                    raw = Path(temporary) / "run"
                    capacity_ok = invoke(
                        [
                            "uv",
                            "run",
                            "--project",
                            str(API),
                            "python",
                            str(API / "tests/capacity/certify.py"),
                            "--output",
                            str(raw),
                        ],
                        7200,
                    )
                    aggregate = raw / "certification.json"
                    if aggregate.exists():
                        capacity = json.loads(aggregate.read_text())
                        write(run / "capacity.json", capacity)
            stages[f"capacity_{repeat}"] = capacity_ok
            matrix = matrix_results(manifest, reports, capacity)
            compatible = all(
                test_passed(
                    manifest["evidence"][key]["node"],
                    reports.get(manifest["evidence"][key]["suite"], {"tests": {}}),
                )
                for key in manifest["compatibility"]
            )
            run_record = {"repeat": repeat, "items": matrix, "compatible": compatible}
            runs.append(run_record)
            for item in matrix:
                status = "PASS" if item["passed"] else "MISSING/FAILED"
                print(
                    f"§17.{item['number']:02}: {status}",
                    flush=True,
                )
            write(run / "matrix.json", run_record)
            assert functional and compatible, "functional/compatibility evidence failed"
            if not args.functional_only:
                assert capacity_ok and all(item["passed"] for item in matrix), (
                    "required matrix evidence failed"
                )
        assert source_fingerprint() == record["source_sha256"], (
            "source changed during certification; rerun from a stable worktree"
        )
        record["passed"] = not args.functional_only
    finally:
        write(output / "certification.json", record)
    if args.functional_only:
        print("Functional iteration passed twice; full certification remains required")
    else:
        print(f"All 38 obligations certified twice: {output / 'certification.json'}")


if __name__ == "__main__":
    main()
