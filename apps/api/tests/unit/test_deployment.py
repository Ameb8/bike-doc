"""Security and operational contracts that do not require infrastructure."""

import importlib.util
import json
from pathlib import Path

import pytest
import yaml

from bike_doc_api.core.config import Settings
from bike_doc_api.operations.alerts import evaluate, validate

ROOT = Path(__file__).resolve().parents[4]


def test_secret_generation_and_role_permissions(tmp_path: Path) -> None:
    spec = importlib.util.spec_from_file_location(
        "prepare", ROOT / "scripts/prepare-production.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    with pytest.raises(ValueError, match="single-host"):
        module.prepare(tmp_path, False)
    module.prepare(tmp_path, True)
    with pytest.raises(ValueError, match="empty"):
        module.prepare(tmp_path, True)
    for role in ("api", "profile", "diagnostic", "migrate"):
        values = json.loads((tmp_path / f"{role}.json").read_text())
        settings = Settings(
            **{
                k.removeprefix("BIKE_DOC_API_").lower(): v
                for k, v in values.items()
                if k.startswith("BIKE_DOC_API_")
            }
        )
        assert settings.database_url.startswith("postgresql+")
        if role == "api":
            assert "GOOGLE_API_KEY" not in values
            assert settings.diagnostic_execution == "durable_queue"
        if role == "profile":
            assert not any(
                "DIAGNOSTIC" in k or "ADK" in k or "AUTH" in k for k in values
            )
        assert (tmp_path / f"{role}.json").stat().st_mode & 0o777 == 0o600
    users = json.loads((tmp_path / "nats.conf").read_text())["authorization"]["users"]
    for user in users:
        if user["user"] == "api":
            creation = [
                subject
                for subject in user["permissions"]["publish"]["allow"]
                if "CONSUMER." in subject and "CREATE" in subject
            ]
            assert set(creation) == {
                "$JS.API.CONSUMER.DURABLE.CREATE.BIKEDOC_WORK_V1.bikedoc_diagnostic_v1",
                "$JS.API.CONSUMER.DURABLE.CREATE.BIKEDOC_WORK_V1.bikedoc_profile_v1",
            }
        if user["user"] in {"profile", "diagnostic"}:
            other = "diagnostic" if user["user"] == "profile" else "profile"
            assert other not in json.dumps(user["permissions"])
            assert not any(
                "CONSUMER.CREATE" in p for p in user["permissions"]["publish"]["allow"]
            )


def test_production_topology() -> None:
    compose = yaml.safe_load((ROOT / "compose.production.yaml").read_text())
    services = compose["services"]
    assert services["nats"]["image"] == "nats:2.12.1-alpine"
    assert services["nats"]["container_name"]
    assert services["nats"]["volumes"] == ["nats_data:/data"]
    for role in (
        "nats",
        "db",
        "collector",
        "api",
        "diagnostic-worker",
        "profile-worker",
    ):
        assert "ports" not in services[role]
    for role in ("api", "diagnostic-worker", "profile-worker"):
        process = services[role]
        assert process["restart"] == "unless-stopped"
        assert process["stop_grace_period"] == "60s"
        assert "environment" not in process and "env_file" not in process
        assert "8081/ready" in str(process["healthcheck"])
    assert compose["networks"]["private"]["internal"]
    assert "maintenance" not in services
    for role, variable in (
        ("api", "BIKEDOC_API_REPLICAS"),
        ("diagnostic-worker", "BIKEDOC_DIAGNOSTIC_REPLICAS"),
        ("profile-worker", "BIKEDOC_PROFILE_REPLICAS"),
    ):
        assert services[role]["deploy"]["replicas"] == "${" + variable + ":-1}"


def test_every_alert_crosses_and_recovers() -> None:
    contract = json.loads((ROOT / "deploy/alerts.json").read_text())
    validate(contract)
    for rule in contract["rules"]:
        name, threshold, op = rule["name"], rule["threshold"], rule["operator"]
        if op == "missing":
            bad, good = {}, {name: {"value": 0, "age_seconds": 0}}
        else:
            bad = {
                name: {"value": threshold + 1 if op == "gt" else 0, "age_seconds": 0}
            }
            good = {name: {"value": threshold, "age_seconds": 0}}
        assert name in evaluate(contract, bad)
        assert name not in evaluate(contract, good)
        if rule.get("missing_fires"):
            assert name in evaluate(contract, {name: {"value": 1, "age_seconds": 121}})
    contract["rules"][0]["attributes"] = {"user_id": ["private"]}
    with pytest.raises(ValueError, match="dimensions"):
        validate(contract)


def test_alert_contract_covers_required_degradation_classes() -> None:
    contract = json.loads((ROOT / "deploy/alerts.json").read_text())
    rules = {rule["name"]: rule for rule in contract["rules"]}
    # Keep the operational acceptance criteria independent of the threshold
    # crossing loop: deleting a required rule must not silently pass validation.
    required = {
        "publication_lag": "publication_oldest",
        "publication_failure": "publication",
        "queue_latency": "queue_latency",
        "diagnostic_workers_unavailable": "worker_available",
        "profile_workers_unavailable": "worker_available",
        "jetstream_storage": "nats.storage_utilization",
        "jetstream_memory": "nats.memory_utilization",
        "jetstream_resource_alarm": "nats.resource_alarm",
        "abnormal_redelivery": "transport_messages",
        "interrupted_work": "outcome",
        "maximum_delivery": "advisory",
        "database_saturation": "pool",
    }
    assert {name: rules[name]["metric"] for name in required} == required
    for name, workload in (
        ("diagnostic_workers_unavailable", "diagnostic"),
        ("profile_workers_unavailable", "profile_inference"),
    ):
        rule = rules[name]
        assert rule["attributes"] == {"workload": [workload]}
        assert rule["reduce"] == "max"
        assert rule["operator"] == "lt" and rule["threshold"] == 1
        assert rule["missing_fires"] is True
        # Abrupt process death leaves no final zero heartbeat. Staleness and
        # absence must both fire, while another fresh replica keeps the pool up.
        assert name in evaluate(contract, {})
        assert name in evaluate(
            contract,
            {name: {"value": 1, "age_seconds": contract["window_seconds"] + 1}},
        )
        assert name not in evaluate(contract, {name: {"value": 1, "age_seconds": 0}})
    assert set(rules["publication_failure"]["attributes"]["publication"]) == {
        "unavailable",
        "timeout",
        "rejected",
    }
    assert rules["maximum_delivery"]["attributes"] == {"advisory": ["max_deliver"]}
    assert rules["interrupted_work"]["attributes"] == {"outcome": ["interrupted"]}
    assert rules["database_saturation"]["reduce"] == "pool_utilization"


def test_deployment_requires_tls_outside_single_host() -> None:
    from bike_doc_api.operations.deployment import validate_transport

    validate_transport({"BIKE_DOC_API_NATS_URL": "nats://nats:4222"})
    validate_transport({"BIKE_DOC_API_NATS_URL": "tls://private-broker:4222"})
    with pytest.raises(ValueError, match="TLS required"):
        validate_transport({"BIKE_DOC_API_NATS_URL": "nats://external:4222"})


def test_development_workers_also_have_narrow_environment() -> None:
    compose = yaml.safe_load((ROOT / "compose.yaml").read_text())
    for role in ("profile-worker", "diagnostic-worker"):
        environment = compose["services"][role]["environment"]
        assert "BIKE_DOC_API_AUTH_MODE" not in environment
        assert "BIKE_DOC_API_FIREBASE_PROJECT_ID" not in environment
        assert "BIKE_DOC_API_JOB_MAINTENANCE_ENABLED" not in environment
    profile = compose["services"]["profile-worker"]["environment"]
    diagnostic = compose["services"]["diagnostic-worker"]["environment"]
    assert not any(
        key.startswith(("BIKE_DOC_API_DIAGNOSTIC_", "BIKE_DOC_API_PRICE_LOOKUP_"))
        for key in profile
    )
    assert not any("PROFILE_INFERENCE" in key for key in diagnostic)
    assert set(compose["services"]["migrate"]["environment"]) == {
        "BIKE_DOC_API_DATABASE_URL"
    }


def test_deployment_scopes_existing_service_name_without_new_resource_fields() -> None:
    from bike_doc_api.operations.deployment import instance_environment

    values = {"BIKE_DOC_API_TELEMETRY_SERVICE_NAME": "bike-doc-profile"}
    first = instance_environment(values, "container-a")
    second = instance_environment(values, "container-b")
    assert first != second
    assert (
        first["BIKE_DOC_API_TELEMETRY_SERVICE_NAME"] == "bike-doc-profile.container-a"
    )
    assert values["BIKE_DOC_API_TELEMETRY_SERVICE_NAME"] == "bike-doc-profile"
    assert set(first) == set(values)


def test_invalid_role_settings_fail_without_logging_secret_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import traceback

    from bike_doc_api.operations import deployment

    sentinel = "private-deployment-password"
    values = {
        "BIKE_DOC_API_ENVIRONMENT": "production",
        "BIKE_DOC_API_PROCESS_ROLE": "api",
        "BIKE_DOC_API_AUTH_MODE": "dev_token",
        "BIKE_DOC_API_DATABASE_URL": f"postgresql+asyncpg://user:{sentinel}@db/app",
    }
    for key, value in values.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr("sys.argv", ["deployment", "api"])
    monkeypatch.setattr(Path, "read_text", lambda self: json.dumps(values))
    with pytest.raises(SystemExit, match="Invalid deployment role secret") as error:
        deployment.main()
    rendered = "".join(traceback.format_exception(error.value))
    assert sentinel not in rendered
    assert "ValidationError" not in rendered
