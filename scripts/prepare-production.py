#!/usr/bin/env python3
"""Create private single-host deployment secrets; never print their values."""

import argparse
import json
import os
import secrets
from pathlib import Path


def prepare(directory: Path, trusted_single_host: bool) -> None:
    if not trusted_single_host:
        raise ValueError(
            "This topology requires explicit trusted single-host acknowledgement; "
            "multi-host requires a reviewed TLS topology"
        )
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    if any(directory.iterdir()):
        raise ValueError(
            "Use an empty secret directory; rotation must preserve database credentials"
        )
    os.chmod(directory, 0o700)

    def write(name: str, value: str) -> None:
        path = directory / name
        # Create with restrictive permissions from the first byte, independent
        # of the caller's umask; exclusive creation also refuses symlinks.
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w") as secret:
            secret.write(value)

    db_password = secrets.token_hex(32)
    write("postgres_password", db_password)
    db = f"postgresql+asyncpg://bikedoc:{db_password}@db:5432/bikedoc"
    prefix = "BIKE_DOC_API_"
    common = {
        "DATABASE_URL": db,
        "ENVIRONMENT": "production",
        "HEALTH_PORT": "8081",
        "TELEMETRY_EXPORTER": "otlp",
        "TELEMETRY_OTLP_ENDPOINT": "http://collector:4318",
        "ARTIFACT_LOCAL_STORAGE_ROOT": "/var/lib/bikedoc/artifacts",
    }
    # Reviewed jointly with Settings/.env.example and deploy/CAPACITY.md.
    capacity = json.loads(
        (Path(__file__).resolve().parents[1] / "deploy/capacity.json").read_text()
    )
    stream = "BIKEDOC_WORK_V1"
    wakeup = "bikedoc.events.wakeup.v1"
    durables = ("bikedoc_diagnostic_v1", "bikedoc_profile_v1")
    users = []
    for role in ("api", "diagnostic", "profile", "monitor"):
        password = secrets.token_hex(32)
        inbox = f"_INBOX.{role}.>"
        sub = [inbox]
        if role == "api":
            pub = [
                "bikedoc.work.v1.diagnostic",
                "bikedoc.work.v1.profile",
                wakeup,
                f"$JS.API.STREAM.INFO.{stream}",
                f"$JS.API.STREAM.CREATE.{stream}",
                *(f"$JS.API.CONSUMER.INFO.{stream}.{name}" for name in durables),
                *(
                    f"$JS.API.CONSUMER.DURABLE.CREATE.{stream}.{name}"
                    for name in durables
                ),
            ]
            sub += [wakeup, f"$JS.EVENT.ADVISORY.CONSUMER.*.{stream}.*"]
        elif role == "monitor":
            pub = [
                f"$JS.API.STREAM.INFO.{stream}",
                *(f"$JS.API.CONSUMER.INFO.{stream}.{name}" for name in durables),
            ]
            sub += [f"$JS.EVENT.ADVISORY.CONSUMER.*.{stream}.*"]
        else:
            durable = f"bikedoc_{role}_v1"
            pub = [
                f"$JS.API.STREAM.INFO.{stream}",
                f"$JS.API.CONSUMER.INFO.{stream}.{durable}",
                f"$JS.API.CONSUMER.MSG.NEXT.{stream}.{durable}",
                f"$JS.ACK.{stream}.{durable}.>",
            ]
            sub += [f"$JS.EVENT.ADVISORY.CONSUMER.*.{stream}.{durable}"]
            if role == "diagnostic":
                pub += [wakeup]
        users.append(
            {
                "user": role,
                "password": password,
                "permissions": {"publish": {"allow": pub}, "subscribe": {"allow": sub}},
            }
        )
        values = {prefix + k: v for k, v in common.items()}
        if role in capacity:
            values.update(
                {prefix + k.upper(): str(v) for k, v in capacity[role].items()}
            )
        values[prefix + "NATS_INBOX_PREFIX"] = f"_INBOX.{role}"
        values[prefix + "NATS_URL"] = f"nats://{role}:{password}@nats:4222"
        values[prefix + "TELEMETRY_SERVICE_NAME"] = f"bike-doc-{role}"
        if role == "api":
            values.update(
                {
                    prefix + "AUTH_MODE": "firebase",
                    prefix + "FIREBASE_PROJECT_ID": "REPLACE_ME",
                    prefix + "DIAGNOSTIC_EXECUTION": "durable_queue",
                    prefix + "PROFILE_INFERENCE_EXECUTION": "durable_queue",
                }
            )
        if role in {"diagnostic", "profile"}:
            values[prefix + "PROCESS_ROLE"] = role
            # Supply role-specific provider credentials before starting workers.
            values["GOOGLE_API_KEY"] = "REPLACE_ME"
        if role == "monitor":
            values = {
                key: value
                for key, value in values.items()
                if key in {prefix + "NATS_URL", prefix + "NATS_INBOX_PREFIX"}
            }
        write(f"{role}.json", json.dumps(values, indent=2) + "\n")
    write("migrate.json", json.dumps({prefix + "DATABASE_URL": db}) + "\n")
    config = {
        "server_name": "bikedoc-single-host",
        "port": 4222,
        "http": "0.0.0.0:8222",
        "jetstream": {
            "store_dir": "/data",
            "max_memory_store": 268435456,
            "max_file_store": 10737418240,
        },
        "authorization": {"users": users},
    }
    write("nats.conf", json.dumps(config, indent=2) + "\n")
    # Replace with a reachable OTLP/gRPC backend and its TLS/auth configuration.
    write(
        "otlp-export.yaml",
        "otlp:\n  endpoint: REPLACE_ME:4317\n  tls:\n    insecure: false\n",
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument("--trusted-single-host", action="store_true")
    args = parser.parse_args()
    prepare(args.directory, args.trusted_single_host)
