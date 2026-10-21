"""Opt-in capacity certification against disposable production Compose projects."""

import argparse
import hashlib
import json
import os
import platform
import secrets
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from budget import (
    CONTENT,
    PATH_SENTINEL,
    PROVIDER,
    SCENARIOS,
    percentile,
    scenario_budgets,
    validate_pools,
)

REPO = Path(__file__).resolve().parents[4]
CODE = Path(__file__).resolve().parent
SECRET_SENTINEL = "CAPACITY_PROHIBITED_SECRET_156"
AUTH_SENTINEL = "CAPACITY_PROHIBITED_AUTH_156"


def certification_report(
    environment: dict[str, Any], results: list[dict[str, Any]]
) -> dict[str, Any]:
    """Finite aggregate evidence suitable for review; raw captures stay private."""
    fields = (
        "scenario",
        "accepted_turns",
        "jobs",
        "acceptance_p95_seconds",
        "outage_acceptance_p95_seconds",
        "restored_acceptance_p95_seconds",
        "diagnostic_queue_p95_seconds",
        "publication_ack_p95_seconds",
        "publication_ack_recovered_p95_seconds",
        "publication_ack_recovered_samples",
        "execution",
        "queue",
        "sse_p95_seconds",
        "sse_samples",
        "sse_resumed_p95_seconds",
        "sse_resumed_samples",
        "sse_poll_recoveries",
        "recovery_seconds",
        "recovery_events",
        "transport_drain_seconds",
        "database_connections_max",
        "pool_role_peaks",
        "active_role_peaks",
        "consumer_peaks",
        "transport_final",
        "stream_messages_max",
        "lock_waiters_max",
        "publication_pending_max",
        "outage_intents",
        "durable_effects_unique",
        "interrupted_jobs",
        "fixed_deadlines_observed",
        "timing",
        "progress_acknowledgements",
        "resolution_counts",
        "broker_samples",
        "process_counts",
        "shutdown_exit_codes",
        "budgets",
        "checks",
        "passed",
    )
    return {
        "schema_version": 1,
        "environment": environment,
        "scenarios": [
            {key: result[key] for key in fields if key in result} for result in results
        ],
    }


def invoke(command: list[str], env: dict[str, str], log: Path) -> None:
    with log.open("ab") as output:
        subprocess.run(
            command,
            cwd=REPO,
            env=env,
            stdout=output,
            stderr=subprocess.STDOUT,
            check=True,
            timeout=600,
        )


def exit_codes(compose: list[str], service: str, env: dict[str, str]) -> list[int]:
    identities = subprocess.check_output(
        [*compose, "ps", "--all", "--quiet", service],
        cwd=REPO,
        env=env,
        text=True,
        timeout=30,
    ).split()
    assert identities, f"missing shutdown evidence: {service}"
    codes = []
    for identity in identities:
        running, code = subprocess.check_output(
            [
                "docker",
                "inspect",
                "--format",
                "{{.State.Running}} {{.State.ExitCode}}",
                identity,
            ],
            env=env,
            text=True,
            timeout=30,
        ).split()
        assert running == "false", "process still running after shutdown"
        codes.append(int(code))
    return codes


def evaluate(output: Path, forbidden: list[str]) -> dict[str, Any]:
    result = json.loads((output / "result.json").read_text())
    forbidden = [*forbidden, *result.pop("private_identities")]
    # All captured operational bytes are scanned, including actual OTLP output,
    # raw broker envelopes/headers, stdout/stderr and exact observation samples.
    # Product API/SSE payloads and provider inputs are deliberately not archived.
    surfaces = [
        "application.log",
        "driver.log",
        "topology.log",
        "broker.json",
        "traces.jsonl",
        "metrics.jsonl",
    ]
    surfaces += [p.name for p in output.glob("signals-*.jsonl")]
    surfaces += [p.name for p in output.glob("progress-*.jsonl")]
    for name in surfaces:
        path = output / name
        assert path.exists() and path.stat().st_size, f"missing privacy surface: {name}"
        raw = path.read_text()
        assert not any(value in raw for value in forbidden), (
            f"privacy failure on {name}"
        )
    observations = []
    process_counts = {}
    for role in ("api", "diagnostic", "profile"):
        paths = list(output.glob(f"signals-{role}-*.jsonl"))
        process_counts[role] = len(paths)
        assert len(paths) >= 2, f"missing replicated {role} evidence"
        for path in paths:
            observations.extend(
                json.loads(line) for line in path.read_text().splitlines()
            )
    observations = [o for o in observations if o["time"] >= result["traffic_start"]]
    diagnostic = [
        o["value"]
        for o in observations
        if o["metric"] == "queue_latency"
        and o["attributes"]["workload"] == "diagnostic"
    ]
    publication = [o["value"] for o in observations if o["metric"] == "publication_ack"]
    execution = {}
    queue = {}
    for workload in ("diagnostic", "profile_inference"):
        queue_values = [
            o["value"]
            for o in observations
            if o["metric"] == "queue_latency"
            and o["attributes"]["workload"] == workload
        ]
        queue[workload] = {
            "p95_seconds": percentile(queue_values),
            "samples": len(queue_values),
        }
        values = [
            o["value"]
            for o in observations
            if o["metric"] == "execution_duration"
            and o["attributes"]["workload"] == workload
        ]
        execution[workload] = {
            "p95_seconds": percentile(values),
            "samples": len(values),
        }
    scenario = result["scenario"]
    budgets = scenario_budgets(scenario)
    sse_hints = [o["value"] for o in observations if o["metric"] == "sse_latency"]
    if scenario != "wakeup_loss":
        assert sse_hints, "missing SSE wake-up latency observations"
    result.update(
        diagnostic_queue_p95_seconds=percentile(diagnostic),
        publication_ack_p95_seconds=percentile(publication),
        execution=execution,
        queue=queue,
        sse_observation_p95_seconds=percentile(sse_hints) if sse_hints else None,
        sse_hint_samples=len(sse_hints),
        process_counts=process_counts,
        sse_poll_recoveries=sum(
            o["value"] for o in observations if o["metric"] == "sse_recovery"
        ),
        recovery_events=sum(
            o["value"] for o in observations if o["metric"] == "recovery"
        ),
        resolution_counts={
            name: sum(
                o["value"]
                for o in observations
                if o["metric"] == "resolution" and o["attributes"]["resolution"] == name
            )
            for name in ("running", "terminal", "executable")
        },
        privacy_surfaces=surfaces,
        budgets=budgets,
    )
    if scenario == "wakeup_loss":
        assert result["sse_poll_recoveries"] > 0, "polling fallback not exercised"
    if scenario == "duplicates":
        assert (
            result["resolution_counts"]["terminal"]
            + result["resolution_counts"]["running"]
            > 0
        )
    if scenario == "worker_loss":
        assert result["recovery_events"] > 0, "deadline recovery not exercised"
    if scenario == "timing":
        progress_counts = {
            role: sum(
                len(path.read_text().splitlines())
                for path in output.glob(f"progress-{role}-*.jsonl")
            )
            for role in ("diagnostic", "profile")
        }
        assert all(progress_counts.values()), "missing real progress acknowledgements"
        assert result["fixed_deadlines_observed"] > 0
        assert all(t["max_attempts"] == 1 for t in result["timing"].values())
        result["progress_acknowledgements"] = progress_counts
    if scenario == "nats_outage":
        assert result["publication_pending_max"] >= result["outage_intents"], (
            "outage intent not accumulated"
        )
    if scenario == "profile_backlog":
        assert (
            max(
                s.get("consumers", {}).get("profile_inference", {}).get("pending", 0)
                for s in result["database_samples"]
            )
            >= 16
        ), "profile not saturated"
    assert result["broker_samples"] > 0
    for sample in result["database_samples"]:
        # After loss, old processes can retain fenced running claims until
        # deadline recovery while the replacement processes execute other jobs.
        factor = 2 if scenario == "worker_loss" else 1
        assert sample["running"].get("diagnostic", 0) <= 4 * factor
        assert sample["running"].get("profile_inference", 0) <= 2 * factor
        for workload, consumer in sample.get("consumers", {}).items():
            limit = 8
            assert consumer["max_ack_pending"] == limit
            assert consumer["ack_pending"] <= limit
            expected = "bikedoc.work.v1." + (
                "diagnostic" if workload == "diagnostic" else "profile"
            )
            assert consumer["filter"] == expected and consumer["ack_wait"] == 30
    # Independent pool evidence enforces per-process role ceilings and no overflow.
    pool_peaks = {}
    active_peaks = {}
    for path in output.glob("signals-*.jsonl"):
        records = [json.loads(line) for line in path.read_text().splitlines()]
        validate_pools(path.name.removeprefix("signals-").split("-")[0], records)
        if path.name.startswith(("signals-diagnostic-", "signals-profile-")):
            limit = 2 if path.name.startswith("signals-diagnostic-") else 1
            active = 0.0
            peak = 0.0
            for observation in records:
                if observation["metric"] == "active":
                    active += observation["value"]
                    assert 0 <= active <= limit, "local execution capacity exceeded"
                    peak = max(peak, active)
            active_peaks[path.stem.removeprefix("signals-")] = peak
        pools = [o for o in records if o["metric"] == "pool"]
        assert pools, f"no pool pressure observations: {path.name}"
        pool_peaks[path.stem.removeprefix("signals-")] = {
            role: max(
                o["value"]
                for o in pools
                if o["attributes"] == {"role": role, "pool": "checked_out"}
            )
            for role in {o["attributes"]["role"] for o in pools}
        }
    pool_role_peaks: dict[str, dict[str, float]] = {}
    active_role_peaks: dict[str, float] = {}
    for process, peaks in pool_peaks.items():
        roles = pool_role_peaks.setdefault(process.split("-")[0], {})
        for role, value in peaks.items():
            roles[role] = max(roles.get(role, 0), value)
    for process, peak in active_peaks.items():
        role = process.split("-")[0]
        active_role_peaks[role] = max(active_role_peaks.get(role, 0), peak)
    consumer_peaks = {}
    for workload in ("diagnostic", "profile_inference"):
        samples = [
            s["consumers"][workload]
            for s in result["database_samples"]
            if workload in s.get("consumers", {})
        ]
        assert samples, f"missing independent consumer samples: {workload}"
        consumer_peaks[workload] = {
            field: max(sample[field] for sample in samples)
            for field in ("pending", "ack_pending", "redelivered")
        }
    checks = {
        name: result[name] <= limit
        for name, limit in budgets.items()
        if name != "drain_seconds"
    }
    if scenario == "nats_outage":
        # Connection establishment is included in the publication observation.
        # Keep the normal gate after all pre-restoration calls must have settled.
        restored = (output / "done-restore").stat().st_mtime
        stable_acks = [
            o["value"]
            for o in observations
            if o["metric"] == "publication_ack" and o["time"] >= restored + 10
        ]
        result["publication_ack_recovered_p95_seconds"] = percentile(stable_acks)
        result["publication_ack_recovered_samples"] = len(stable_acks)
        checks["publication_ack_recovered"] = (
            result["publication_ack_recovered_p95_seconds"]
            <= scenario_budgets("steady")["publication_ack_p95_seconds"]
        )
    checks["drain_seconds"] = result["recovery_seconds"] <= (
        budgets["recovery_seconds"]
        if scenario == "worker_loss"
        else budgets["drain_seconds"]
    )
    checks["execution_diagnostic"] = execution["diagnostic"]["p95_seconds"] < 120
    checks["execution_profile"] = execution["profile_inference"]["p95_seconds"] < 60
    result.update(
        checks=checks,
        passed=all(checks.values()),
        pool_peaks=pool_peaks,
        active_peaks=active_peaks,
        pool_role_peaks=pool_role_peaks,
        active_role_peaks=active_role_peaks,
        consumer_peaks=consumer_peaks,
        stream_messages_max=max(s.get("stored", 0) for s in result["database_samples"]),
    )
    pressure = result.pop("database_samples")
    (output / "pressure.json").write_text(json.dumps(pressure, indent=2) + "\n")
    (output / "result.json").write_text(json.dumps(result, indent=2) + "\n")
    assert result["passed"], "capacity budget failed: " + ", ".join(
        k for k, v in checks.items() if not v
    )
    return result


def certify(scenario: str, destination: Path, build: bool) -> dict[str, Any]:
    output = destination / scenario
    output.mkdir(parents=True, exist_ok=False)
    output.chmod(0o700)
    log = output / "topology.log"
    env = dict(os.environ)
    env.update(
        COMPOSE_PROJECT_NAME=f"bikedoc-capacity-{secrets.token_hex(4)}",
        BIKEDOC_RUNTIME_IMAGE="bikedoc-runtime:capacity",
        BIKEDOC_CAPACITY_CODE=str(CODE),
        BIKEDOC_CAPACITY_OUTPUT=str(output),
        BIKEDOC_SECRET_OWNER_UID=str(os.getuid()),
        BIKEDOC_API_REPLICAS="2",
        BIKEDOC_DIAGNOSTIC_REPLICAS="2",
        BIKEDOC_PROFILE_REPLICAS="2",
    )
    compose = [
        "docker",
        "compose",
        "-f",
        str(REPO / "compose.production.yaml"),
        "-f",
        str(CODE / "compose.yaml"),
    ]
    control = {"diagnostic_delay": 0.1, "profile_delay": 0.25}
    if scenario == "profile_backlog":
        control["profile_delay"] = 20
    if scenario == "profile_failure":
        control.update(profile_delay=1, profile_failure=True)
    if scenario in {"worker_loss", "broker_restart"}:
        control.update(diagnostic_delay=5, profile_delay=5)
    if scenario == "wakeup_loss":
        control["lose_hints"] = True
    if scenario == "timing":
        control.update(diagnostic_delay=12, profile_delay=12)
    (output / "control.json").write_text(json.dumps(control))
    shutdown = {}
    forbidden = [CONTENT, PROVIDER, PATH_SENTINEL, SECRET_SENTINEL, AUTH_SENTINEL]
    with tempfile.TemporaryDirectory(prefix="bikedoc-capacity-secrets-") as directory:
        env["BIKEDOC_SECRETS_DIR"] = directory
        invoke(
            [
                "python3",
                "scripts/prepare-production.py",
                directory,
                "--trusted-single-host",
            ],
            env,
            log,
        )
        root = Path(directory)
        for role in ("api", "diagnostic", "profile"):
            path = root / f"{role}.json"
            values = json.loads(path.read_text())
            values["BIKE_DOC_API_DEV_AUTH_TOKEN"] = SECRET_SENTINEL
            values["BIKE_DOC_API_DEV_AUTH_SUBJECT"] = AUTH_SENTINEL
            values["BIKE_DOC_API_DEV_AUTH_EMAIL"] = AUTH_SENTINEL + "@example.test"
            values["BIKE_DOC_API_DEV_AUTH_DISPLAY_NAME"] = AUTH_SENTINEL
            for key in ("BIKE_DOC_API_NATS_URL", "BIKE_DOC_API_DATABASE_URL"):
                forbidden.append(urlsplit(values[key]).password)
            path.write_text(json.dumps(values))
        # Passive observation of published work only, isolated from role ACLs.
        config_path = root / "nats.conf"
        config = json.loads(config_path.read_text())
        forbidden.extend(user["password"] for user in config["authorization"]["users"])
        password = secrets.token_hex(32)
        forbidden.append(password)
        config["authorization"]["users"].append(
            {
                "user": "observer",
                "password": password,
                "permissions": {
                    "subscribe": {
                        "allow": [
                            "bikedoc.work.v1.diagnostic",
                            "bikedoc.work.v1.profile",
                        ]
                    },
                    "publish": {"deny": [">"]},
                },
            }
        )
        config_path.write_text(json.dumps(config))
        (root / "observer.json").write_text(
            json.dumps(
                {"BIKE_DOC_API_NATS_URL": f"nats://observer:{password}@nats:4222"}
            )
        )
        (root / "observer.json").chmod(0o600)
        try:
            if build:
                invoke([*compose, "build", "migrate"], env, log)
            invoke(
                [*compose, "up", "-d", "--wait", "db", "nats", "collector"], env, log
            )
            invoke([*compose, "run", "--rm", "migrate"], env, log)
            invoke(
                [
                    *compose,
                    "run",
                    "--rm",
                    "--no-deps",
                    "operator",
                    "python",
                    "-m",
                    "bike_doc_api.operations.deployment",
                    "reconstruct",
                ],
                env,
                log,
            )
            # Reuse Task 4.3's real authorization checks before traffic. It
            # settles its own probes and cannot create product/job records.
            invoke(
                [
                    *compose,
                    "run",
                    "--rm",
                    "--no-deps",
                    "-v",
                    f"{directory}:/verification:ro",
                    "-v",
                    f"{REPO / 'deploy'}:/deployment:ro",
                    "operator",
                    "python",
                    "/deployment/verify_permissions.py",
                ],
                env,
                log,
            )
            invoke(
                [
                    *compose,
                    "up",
                    "-d",
                    "--no-deps",
                    "--wait",
                    "diagnostic-worker",
                    "profile-worker",
                ],
                env,
                log,
            )
            invoke([*compose, "up", "-d", "--no-deps", "--wait", "api"], env, log)
            invoke([*compose, "up", "-d", "--no-deps", "gateway"], env, log)
            with (output / "driver.log").open("wb") as stdout:
                driver = subprocess.Popen(
                    [
                        *compose,
                        "run",
                        "--rm",
                        "--no-deps",
                        "--user",
                        str(os.getuid()),
                        "-v",
                        f"{directory}:/verification:ro",
                        "operator",
                        "python",
                        "/capacity/role.py",
                        "driver",
                        scenario,
                    ],
                    cwd=REPO,
                    env=env,
                    stdout=stdout,
                    stderr=subprocess.STDOUT,
                )
                try:
                    deadline = time.monotonic() + 480
                    while driver.poll() is None:
                        assert time.monotonic() < deadline, (
                            "driver wall-clock budget exceeded"
                        )
                        for name in ("offline", "restore", "loss", "restart"):
                            if (output / f"action-{name}").exists() and not (
                                output / f"done-{name}"
                            ).exists():
                                if name == "offline":
                                    invoke([*compose, "stop", "nats"], env, log)
                                elif name == "restore":
                                    invoke(
                                        [*compose, "start", "--wait", "nats"], env, log
                                    )
                                elif name == "restart":
                                    invoke([*compose, "restart", "nats"], env, log)
                                else:
                                    # Force post-effect loss while calls are active;
                                    # profiles settle gracefully during backlog.
                                    invoke(
                                        [
                                            *compose,
                                            "kill",
                                            "-s",
                                            "SIGKILL",
                                            "diagnostic-worker",
                                            "api",
                                        ],
                                        env,
                                        log,
                                    )
                                    invoke(
                                        [
                                            *compose,
                                            "stop",
                                            "-t",
                                            "60",
                                            "profile-worker",
                                        ],
                                        env,
                                        log,
                                    )
                                    shutdown["profile_backlog_graceful"] = exit_codes(
                                        compose, "profile-worker", env
                                    )
                                    assert all(
                                        code == 0
                                        for code in shutdown["profile_backlog_graceful"]
                                    ), "profile shutdown exceeded graceful budget"
                                    invoke(
                                        [
                                            *compose,
                                            "up",
                                            "-d",
                                            "--wait",
                                            "--no-deps",
                                            "diagnostic-worker",
                                            "profile-worker",
                                            "api",
                                        ],
                                        env,
                                        log,
                                    )
                                (output / f"done-{name}").touch()
                        time.sleep(0.2)
                    assert driver.returncode == 0, (
                        f"driver failed ({scenario}); inspect private driver.log"
                    )
                finally:
                    if driver.poll() is None:
                        driver.terminate()
                        driver.wait(timeout=15)
            invoke(
                [
                    *compose,
                    "stop",
                    "-t",
                    "60",
                    "api",
                    "diagnostic-worker",
                    "profile-worker",
                ],
                env,
                log,
            )
            for service in ("api", "diagnostic-worker", "profile-worker"):
                shutdown[service] = exit_codes(compose, service, env)
                allowed = {0, 143} if service == "api" else {0}
                assert set(shutdown[service]) <= allowed, "shutdown did not settle"
            (output / "shutdown.json").write_text(json.dumps(shutdown, indent=2) + "\n")
            with (output / "application.log").open("wb") as logs:
                subprocess.run(
                    [
                        *compose,
                        "logs",
                        "--no-color",
                        "api",
                        "diagnostic-worker",
                        "profile-worker",
                        "collector",
                    ],
                    cwd=REPO,
                    env=env,
                    stdout=logs,
                    stderr=subprocess.STDOUT,
                    check=True,
                )
            result = evaluate(output, forbidden)
            result["shutdown_exit_codes"] = shutdown
            (output / "result.json").write_text(json.dumps(result, indent=2) + "\n")
            return result
        finally:
            # Capture even on failure, before removing the isolated topology.
            with (output / "application.log").open("wb") as logs:
                subprocess.run(
                    [
                        *compose,
                        "logs",
                        "--no-color",
                        "api",
                        "diagnostic-worker",
                        "profile-worker",
                        "collector",
                    ],
                    cwd=REPO,
                    env=env,
                    stdout=logs,
                    stderr=subprocess.STDOUT,
                    check=False,
                    timeout=30,
                )
            # Never expose secret-bearing environment/inspect output on failure.
            invoke([*compose, "down", "-v", "--remove-orphans"], env, log)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenario", choices=(*SCENARIOS, "all"), default="all")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--no-build", action="store_true")
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    output.chmod(0o700)
    environment = {
        "platform": platform.platform(),
        "hardware_model": (
            Path("/proc/device-tree/model").read_text().rstrip("\0")
            if Path("/proc/device-tree/model").exists()
            else platform.machine()
        ),
        "cpus": os.cpu_count(),
        "memory_kib": next(
            line.split(":")[1].strip()
            for line in Path("/proc/meminfo").read_text().splitlines()
            if line.startswith("MemTotal:")
        ),
        "docker": subprocess.check_output(
            ["docker", "version", "--format", "{{.Server.Version}}"], text=True
        ).strip(),
        "git_revision": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=REPO, text=True
        ).strip(),
        "dirty": bool(
            subprocess.check_output(["git", "status", "--porcelain"], cwd=REPO)
        ),
        "replicas": {"api": 2, "diagnostic": 2, "profile": 2},
        "capacity_settings": json.loads((REPO / "deploy/capacity.json").read_text()),
        "source_sha256": {
            str(path.relative_to(REPO)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(
                [
                    *CODE.glob("*.py"),
                    *CODE.glob("*.yaml"),
                    *(REPO / "apps/api/src").rglob("*.py"),
                    REPO / "compose.production.yaml",
                    REPO / "apps/api/Dockerfile",
                    REPO / "scripts/prepare-production.py",
                    REPO / "deploy/verify_permissions.py",
                    REPO / "uv.lock",
                ]
            )
        },
    }
    (output / "environment.json").write_text(json.dumps(environment, indent=2) + "\n")
    results = []
    selected = SCENARIOS if args.scenario == "all" else (args.scenario,)
    for index, scenario in enumerate(selected):
        print(f"Certifying {scenario} ({index + 1}/{len(selected)})", flush=True)
        results.append(certify(scenario, output, index == 0 and not args.no_build))
        if index == 0:
            environment["runtime_image_id"] = subprocess.check_output(
                [
                    "docker",
                    "image",
                    "inspect",
                    "bikedoc-runtime:capacity",
                    "--format",
                    "{{.Id}}",
                ],
                text=True,
            ).strip()
            (output / "environment.json").write_text(
                json.dumps(environment, indent=2) + "\n"
            )
        print(f"Passed {scenario}", flush=True)
    (output / "summary.json").write_text(json.dumps(results, indent=2) + "\n")
    (output / "certification.json").write_text(
        json.dumps(certification_report(environment, results), indent=2) + "\n"
    )


if __name__ == "__main__":
    main()
