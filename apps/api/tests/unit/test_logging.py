"""Process logging and request-correlation behavior."""

from __future__ import annotations

import json
import logging

import pytest
import structlog
from fastapi import FastAPI
from fastapi.testclient import TestClient

from bike_doc_api.api.middleware import install_request_logging
from bike_doc_api.core.config import Settings
from bike_doc_api.core.logging import configure_logging
from bike_doc_api.main import create_app


def test_json_renderer_preserves_structlog_and_stdlib_fields(
    capsys: pytest.CaptureFixture[str],
) -> None:
    configure_logging(environment="test", log_format="json")

    structlog.get_logger("bike_doc_api.test").info("application_event", bike_id="b_1")
    logging.getLogger("third_party").warning(
        "provider event", extra={"provider": "example"}
    )

    records = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert records[0]["event"] == "application_event"
    assert records[0]["bike_id"] == "b_1"
    assert records[1]["event"] == "provider event"
    assert records[1]["provider"] == "example"


def test_console_renderer_preserves_structured_fields(
    capsys: pytest.CaptureFixture[str],
) -> None:
    configure_logging(environment="local", log_format="console")

    structlog.get_logger("bike_doc_api.test").info("application_event", bike_id="b_1")

    assert "bike_id=b_1" in capsys.readouterr().out


def test_diagnostic_debug_is_limited_to_approved_namespaces() -> None:
    configure_logging(environment="local", diagnostic_log_level="DEBUG")

    assert logging.getLogger().level == logging.INFO
    assert logging.getLogger("bike_doc_api").level == logging.INFO
    assert logging.getLogger("bike_doc_api.adk").level == logging.DEBUG
    assert (
        logging.getLogger("bike_doc_api.services.diagnostic_visual_context").level
        == logging.DEBUG
    )
    for name in (
        "google_adk.google.adk.models.google_llm",
        "google_genai",
        "httpx",
        "httpcore",
        "urllib3",
        "PIL",
        "sqlalchemy.engine",
    ):
        assert logging.getLogger(name).level == logging.WARNING


def test_diagnostic_debug_does_not_emit_dependency_or_general_app_debug(
    capsys: pytest.CaptureFixture[str],
) -> None:
    configure_logging(
        environment="local", diagnostic_log_level="DEBUG", log_format="json"
    )

    structlog.get_logger("bike_doc_api.adk.runner").debug("approved_debug")
    structlog.get_logger("bike_doc_api.other").debug("suppressed_app_debug")
    logging.getLogger("httpx").debug("suppressed_dependency_debug")

    records = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert [record["event"] for record in records] == ["approved_debug"]


def test_reconfiguration_replaces_root_handler() -> None:
    configure_logging(environment="test")
    first_handler = logging.getLogger().handlers[0]

    configure_logging(environment="test")

    assert len(logging.getLogger().handlers) == 1
    assert logging.getLogger().handlers[0] is not first_handler
    assert logging.getLogger("uvicorn.access").disabled is True


def test_request_middleware_reuses_id_and_clears_context(
    capsys: pytest.CaptureFixture[str],
) -> None:
    configure_logging(environment="test", log_format="json")
    app = FastAPI()
    install_request_logging(app)

    @app.get("/ok")
    async def ok() -> dict[str, bool]:
        structlog.get_logger(__name__).info("inside_request")
        return {"ok": True}

    with TestClient(app) as client:
        response = client.get("/ok", headers={"X-Request-ID": "request-123"})
    assert response.headers["X-Request-ID"] == "request-123"

    records = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert records[0]["request_id"] == "request-123"
    completed = records[-1]
    assert completed["event"] == "http_request_completed"
    assert completed["route"] == "/ok"
    assert completed["status_code"] == 200

    structlog.get_logger(__name__).info("after_request")
    assert "request_id" not in json.loads(capsys.readouterr().out)


def test_request_middleware_generates_id_and_reraises_failures(
    capsys: pytest.CaptureFixture[str],
) -> None:
    configure_logging(environment="test", log_format="json")
    app = FastAPI()
    install_request_logging(app)

    @app.get("/boom")
    async def boom() -> None:
        raise RuntimeError("boom")

    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get("/boom")
    assert response.status_code == 500

    records = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    failed = next(
        record for record in records if record["event"] == "http_request_failed"
    )
    assert failed["status_code"] == 500
    assert failed["exception"]


def test_settings_pass_diagnostic_log_level_to_app() -> None:
    create_app(Settings(environment="test", diagnostic_log_level="DEBUG"))

    assert logging.getLogger("bike_doc_api.adk").level == logging.DEBUG
