"""FastAPI app factory configuration tests."""

import pytest
from fastapi.middleware.cors import CORSMiddleware

import bike_doc_api.main as main
from bike_doc_api.core.config import Settings
from bike_doc_api.main import create_app


def test_create_app_uses_baseline_settings() -> None:
    settings = Settings(
        app_name="Configured API",
        environment="test",
        debug=True,
        cors_origins=["http://localhost:3000"],
        log_level="INFO",
        log_format="console",
    )

    app = create_app(settings)

    assert app.title == "Configured API"
    assert app.debug is True
    assert any(middleware.cls is CORSMiddleware for middleware in app.user_middleware)


def test_create_app_validates_artifact_storage_at_startup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings(
        app_name="Configured API",
        environment="production",
        auth_mode="firebase",
        firebase_project_id="bike-doc-prod",
        artifact_storage_provider="gcs",
        artifact_gcs_bucket="bike-doc-artifacts",
    )
    called: dict[str, object] = {}

    def _validate(received_settings: Settings) -> None:
        called["settings"] = received_settings

    monkeypatch.setattr(
        main, "validate_artifact_storage_runtime_configuration", _validate
    )

    create_app(settings)

    assert called["settings"] is settings
