"""Load one role's deployment secret before importing application settings."""

import json
import os
import sys
from pathlib import Path
from urllib.parse import urlsplit

COMMANDS = {
    "api": [
        "uvicorn",
        "bike_doc_api.main:app",
        "--host",
        "0.0.0.0",
        "--port",
        "8000",
        "--no-access-log",
        "--timeout-graceful-shutdown",
        "35",
    ],
    "diagnostic": ["python", "-m", "bike_doc_api.workers.diagnostic_role"],
    "profile": ["python", "-m", "bike_doc_api.workers.profile_role"],
    "migrate": ["alembic", "upgrade", "head"],
    "reconstruct": ["python", "-m", "bike_doc_api.maintenance.reconstruction"],
    "inspect": ["python", "-m", "bike_doc_api.operations.inspection"],
}


def validate_transport(values: dict[str, str]) -> None:
    endpoint = values.get("BIKE_DOC_API_NATS_URL")
    if endpoint is not None:
        parsed = urlsplit(endpoint)
        if parsed.scheme != "tls" and not (
            parsed.scheme == "nats" and parsed.hostname == "nats"
        ):
            raise ValueError("TLS required outside the private single-host broker")


def instance_environment(values: dict[str, str], hostname: str) -> dict[str, str]:
    values = dict(values)
    key = "BIKE_DOC_API_TELEMETRY_SERVICE_NAME"
    if key in values:
        values[key] = f"{values[key]}.{hostname}"
    return values


def main() -> None:
    try:
        role = sys.argv[1]
        values = json.loads(Path("/run/secrets/role.json").read_text())
        if not isinstance(values, dict) or not all(
            isinstance(k, str) and isinstance(v, str) for k, v in values.items()
        ):
            raise ValueError
        validate_transport(values)
        os.environ.update(instance_environment(values, os.uname().nodename))
        # Pydantic validation errors can contain the entire input environment.
        # Reject malformed secrets here, before Uvicorn can print an import-time
        # traceback containing database/broker credentials.
        from bike_doc_api.core.config import Settings

        Settings()
        command = COMMANDS[role] + sys.argv[2:]
    except Exception:
        raise SystemExit("Invalid deployment role secret or command") from None
    os.execvp(command[0], command)


if __name__ == "__main__":
    main()
