"""Configuration setup tests."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from bike_doc_api.core.config import (
    Settings,
    validate_diagnostic_runtime_configuration,
)


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
    monkeypatch.setenv("BIKE_DOC_API_AUTH_MODE", "dev")
    monkeypatch.setenv("BIKE_DOC_API_DEV_AUTH_TOKEN", "configured-token")
    monkeypatch.setenv("BIKE_DOC_API_DEV_AUTH_SUBJECT", "auth|configured")
    monkeypatch.setenv("BIKE_DOC_API_DEV_AUTH_EMAIL", "configured@example.com")
    monkeypatch.setenv("BIKE_DOC_API_DEV_AUTH_DISPLAY_NAME", "Configured User")
    monkeypatch.setenv("BIKE_DOC_API_FIREBASE_PROJECT_ID", "bike-doc-dev")
    monkeypatch.setenv("BIKE_DOC_API_LOG_LEVEL", "warning")
    monkeypatch.setenv("BIKE_DOC_API_LOG_FORMAT", "json")
    monkeypatch.setenv("BIKE_DOC_API_DIAGNOSTIC_LLM_PROVIDER", "google_ai")
    monkeypatch.setenv("BIKE_DOC_API_DIAGNOSTIC_AGENT_MODEL", "gemini-test")
    monkeypatch.setenv("BIKE_DOC_API_DIAGNOSTIC_AGENT_TEMPERATURE", "0.7")
    monkeypatch.setenv("BIKE_DOC_API_DIAGNOSTIC_AGENT_MAX_OUTPUT_TOKENS", "1024")
    monkeypatch.setenv("BIKE_DOC_API_DIAGNOSTIC_AGENT_TIMEOUT_SECONDS", "12.5")
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
    assert settings.auth_mode == "dev"
    assert settings.dev_auth_token == "configured-token"
    assert settings.dev_auth_subject == "auth|configured"
    assert settings.dev_auth_email == "configured@example.com"
    assert settings.dev_auth_display_name == "Configured User"
    assert settings.firebase_project_id == "bike-doc-dev"
    assert settings.log_level == "WARNING"
    assert settings.log_format == "json"
    assert settings.diagnostic_llm_provider == "google_ai"
    assert settings.diagnostic_agent_model == "gemini-test"
    assert settings.diagnostic_agent_temperature == 0.7
    assert settings.diagnostic_agent_max_output_tokens == 1024
    assert settings.diagnostic_agent_timeout_seconds == 12.5


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


def test_blank_diagnostic_agent_model_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("BIKE_DOC_API_DIAGNOSTIC_AGENT_MODEL", " ")

    with pytest.raises(ValidationError):
        Settings()


def test_settings_accept_valid_diagnostic_runtime_settings() -> None:
    settings = Settings(
        environment="test",
        diagnostic_llm_provider="vertex_ai",
        diagnostic_agent_model="gemini-test",
        diagnostic_agent_temperature=1.5,
        diagnostic_agent_max_output_tokens=512,
        diagnostic_agent_timeout_seconds=45,
    )

    assert settings.diagnostic_llm_provider == "vertex_ai"
    assert settings.diagnostic_agent_model == "gemini-test"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("diagnostic_llm_provider", "local"),
        ("diagnostic_agent_temperature", -0.1),
        ("diagnostic_agent_temperature", 2.1),
        ("diagnostic_agent_max_output_tokens", 0),
        ("diagnostic_agent_timeout_seconds", 0),
    ],
)
def test_invalid_diagnostic_runtime_settings_are_rejected(
    field: str,
    value: object,
) -> None:
    with pytest.raises(ValidationError):
        Settings(environment="test", **{field: value})


def test_google_ai_runtime_validation_requires_api_key_outside_test() -> None:
    settings = Settings(
        environment="local",
        diagnostic_llm_provider="google_ai",
    )

    with pytest.raises(ValueError):
        validate_diagnostic_runtime_configuration(settings, environ={})


def test_google_ai_runtime_validation_accepts_gemini_api_key() -> None:
    settings = Settings(
        environment="local",
        diagnostic_llm_provider="google_ai",
    )

    validate_diagnostic_runtime_configuration(
        settings,
        environ={"GEMINI_API_KEY": "test-key"},
    )


def test_vertex_runtime_validation_requires_vertex_environment() -> None:
    settings = Settings(
        environment="local",
        diagnostic_llm_provider="vertex_ai",
    )

    with pytest.raises(ValueError):
        validate_diagnostic_runtime_configuration(
            settings,
            environ={
                "GOOGLE_GENAI_USE_VERTEXAI": "true",
                "GOOGLE_CLOUD_PROJECT": "bike-doc",
            },
        )


def test_vertex_runtime_validation_accepts_required_environment() -> None:
    settings = Settings(
        environment="local",
        diagnostic_llm_provider="vertex_ai",
    )

    validate_diagnostic_runtime_configuration(
        settings,
        environ={
            "GOOGLE_GENAI_USE_VERTEXAI": "true",
            "GOOGLE_CLOUD_PROJECT": "bike-doc",
            "GOOGLE_CLOUD_LOCATION": "us-central1",
        },
    )


def test_runtime_validation_is_bypassed_in_test_environment() -> None:
    validate_diagnostic_runtime_configuration(
        Settings(environment="test", diagnostic_llm_provider="vertex_ai"),
        environ={},
    )


def test_env_example_documents_diagnostic_runtime_settings() -> None:
    env_example = Path(__file__).resolve().parents[4] / ".env.example"
    content = env_example.read_text(encoding="utf-8")

    for variable in [
        "BIKE_DOC_API_AUTH_MODE",
        "BIKE_DOC_API_FIREBASE_PROJECT_ID",
        "BIKE_DOC_API_DIAGNOSTIC_LLM_PROVIDER",
        "BIKE_DOC_API_DIAGNOSTIC_AGENT_MODEL",
        "BIKE_DOC_API_DIAGNOSTIC_AGENT_TEMPERATURE",
        "BIKE_DOC_API_DIAGNOSTIC_AGENT_MAX_OUTPUT_TOKENS",
        "BIKE_DOC_API_DIAGNOSTIC_AGENT_TIMEOUT_SECONDS",
        "GEMINI_API_KEY",
        "GOOGLE_API_KEY",
        "GOOGLE_GENAI_USE_VERTEXAI",
        "GOOGLE_CLOUD_PROJECT",
        "GOOGLE_CLOUD_LOCATION",
    ]:
        assert variable in content


def test_dev_auth_mode_is_rejected_in_production() -> None:
    with pytest.raises(ValidationError):
        Settings(environment="production", auth_mode="dev")


def test_local_unsigned_jwt_mode_is_rejected_in_production() -> None:
    with pytest.raises(ValidationError):
        Settings(environment="production", auth_mode="local_unsigned_jwt")


def test_firebase_auth_mode_requires_project_id() -> None:
    with pytest.raises(ValidationError):
        Settings(environment="local", auth_mode="firebase")
