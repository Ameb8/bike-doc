"""Configuration setup tests."""

import pytest
from pydantic import ValidationError

from bike_doc_api.core.config import Settings


def test_settings_read_bike_doc_api_prefixed_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("BIKE_DOC_API_APP_NAME", "Configured API")
    monkeypatch.setenv("BIKE_DOC_API_ENVIRONMENT", "test")
    monkeypatch.setenv("BIKE_DOC_API_DEBUG", "true")
    monkeypatch.setenv(
        "BIKE_DOC_API_CORS_ORIGINS",
        '["http://localhost:3000","http://localhost:8080"]',
    )
    monkeypatch.setenv(
        "BIKE_DOC_API_DATABASE_URL",
        "postgresql+asyncpg://test:test@localhost:5432/test",
    )
    monkeypatch.setenv("BIKE_DOC_API_LOG_LEVEL", "warning")
    monkeypatch.setenv("BIKE_DOC_API_LOG_FORMAT", "json")
    monkeypatch.setenv("BIKE_DOC_API_UNIMPLEMENTED_SETTING", "ignored")

    settings = Settings()

    assert settings.app_name == "Configured API"
    assert settings.environment == "test"
    assert settings.debug is True
    assert settings.cors_origins == [
        "http://localhost:3000",
        "http://localhost:8080",
    ]
    assert settings.database_url == "postgresql+asyncpg://test:test@localhost:5432/test"
    assert settings.log_level == "WARNING"
    assert settings.log_format == "json"


def test_empty_optional_log_settings_are_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("BIKE_DOC_API_LOG_LEVEL", "")
    monkeypatch.setenv("BIKE_DOC_API_LOG_FORMAT", "")

    settings = Settings()

    assert settings.log_level is None
    assert settings.log_format is None


def test_invalid_log_level_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BIKE_DOC_API_LOG_LEVEL", "verbose")

    with pytest.raises(ValidationError):
        Settings()
