"""Configuration setup tests."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from bike_doc_api.core.config import (
    Settings,
    validate_artifact_storage_runtime_configuration,
    validate_diagnostic_runtime_configuration,
    validate_price_lookup_runtime_configuration,
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
    monkeypatch.setenv("BIKE_DOC_API_ARTIFACT_STORAGE_PROVIDER", "gcs")
    monkeypatch.setenv("BIKE_DOC_API_ARTIFACT_GCS_BUCKET", "bike-doc-artifacts")
    monkeypatch.setenv("BIKE_DOC_API_DIAGNOSTIC_LLM_PROVIDER", "google_ai")
    monkeypatch.setenv("BIKE_DOC_API_DIAGNOSTIC_AGENT_MODEL", "gemini-test")
    monkeypatch.setenv("BIKE_DOC_API_DIAGNOSTIC_AGENT_TEMPERATURE", "0.7")
    monkeypatch.setenv("BIKE_DOC_API_DIAGNOSTIC_AGENT_MAX_OUTPUT_TOKENS", "1024")
    monkeypatch.setenv("BIKE_DOC_API_DIAGNOSTIC_AGENT_TIMEOUT_SECONDS", "12.5")
    monkeypatch.setenv("BIKE_DOC_API_PRICE_LOOKUP_PROVIDER", "gemini_grounded")
    monkeypatch.setenv("BIKE_DOC_API_PRICE_LOOKUP_LLM_PROVIDER", "vertex_ai")
    monkeypatch.setenv("BIKE_DOC_API_PRICE_LOOKUP_MODEL", "gemini-price-test")
    monkeypatch.setenv("BIKE_DOC_API_PRICE_LOOKUP_TEMPERATURE", "0.3")
    monkeypatch.setenv("BIKE_DOC_API_PRICE_LOOKUP_MAX_OUTPUT_TOKENS", "777")
    monkeypatch.setenv("BIKE_DOC_API_PRICE_LOOKUP_TIMEOUT_SECONDS", "9.5")
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
    assert settings.artifact_storage_provider == "gcs"
    assert settings.artifact_gcs_bucket == "bike-doc-artifacts"
    assert settings.diagnostic_llm_provider == "google_ai"
    assert settings.diagnostic_agent_model == "gemini-test"
    assert settings.diagnostic_agent_temperature == 0.7
    assert settings.diagnostic_agent_max_output_tokens == 1024
    assert settings.diagnostic_agent_timeout_seconds == 12.5
    assert settings.price_lookup_provider == "gemini_grounded"
    assert settings.price_lookup_llm_provider == "vertex_ai"
    assert settings.price_lookup_model == "gemini-price-test"
    assert settings.price_lookup_temperature == 0.3
    assert settings.price_lookup_max_output_tokens == 777
    assert settings.price_lookup_timeout_seconds == 9.5


def test_empty_optional_log_settings_are_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("BIKE_DOC_API_LOG_LEVEL", "")
    monkeypatch.setenv("BIKE_DOC_API_LOG_FORMAT", "")

    settings = Settings()

    assert settings.log_level is None
    assert settings.log_format is None


@pytest.mark.parametrize("configured", ["off", "pixels_only", "shadow", "enabled"])
def test_image_analysis_mode_is_normalized_to_a_supported_mode(
    configured: str,
) -> None:
    settings = Settings(
        environment="test", image_analysis_mode=f" {configured.upper()} "
    )

    assert settings.image_analysis_mode == configured


def test_image_analysis_mode_rejects_unknown_modes() -> None:
    with pytest.raises(ValidationError):
        Settings(environment="test", image_analysis_mode="sampled")


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


def test_bootstrap_profile_policy_is_explicit_and_non_production_only() -> None:
    development = Settings(
        environment="test",
        profile_inference_resolver_policy="bootstrap-v1",
    )

    assert development.profile_inference_resolver_policy == "bootstrap-v1"
    with pytest.raises(ValidationError, match="not permitted in production"):
        Settings(
            environment="production",
            auth_mode="firebase",
            firebase_project_id="bike-doc",
            profile_inference_resolver_policy="bootstrap-v1",
        )


def test_profile_inference_deployment_mode_explicitly_supports_shadow() -> None:
    settings = Settings(
        environment="test",
        profile_inference_policy_mode="shadow",
    )

    assert settings.profile_inference_policy_mode == "shadow"


def test_profile_inference_default_extractor_version_covers_all_drivetrain_slices() -> (
    None
):
    assert Settings().profile_inference_extractor_version == (
        "drivetrain-specifications.v1"
    )


def test_profile_inference_policy_settings_require_separate_ordered_thresholds() -> (
    None
):
    with pytest.raises(ValidationError, match="greater than or equal"):
        Settings(
            environment="test",
            profile_inference_policies=[
                {
                    "field_path": "brakes.rear.mechanism",
                    "evidence_class": "direct_visual",
                    "calibration_key": "rear-brake.v1",
                    "policy_version": "policy.v1",
                    "auto_fill_threshold": 0.98,
                    "auto_overwrite_threshold": 0.97,
                },
            ],
        )


def test_profile_inference_policy_mode_rejects_conflicting_legacy_alias() -> None:
    with pytest.raises(ValidationError, match="conflicts"):
        Settings(
            environment="test",
            profile_inference_policy_mode="shadow",
            profile_inference_resolver_policy="bootstrap-v1",
        )


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


def test_gcs_storage_provider_requires_bucket() -> None:
    with pytest.raises(ValidationError):
        Settings(
            environment="test",
            artifact_storage_provider="gcs",
        )


def test_gcs_storage_bucket_is_trimmed() -> None:
    settings = Settings(
        environment="test",
        artifact_storage_provider="gcs",
        artifact_gcs_bucket="  bike-doc-artifacts  ",
    )

    assert settings.artifact_gcs_bucket == "bike-doc-artifacts"


def test_gcs_artifact_runtime_validation_requires_google_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings(
        environment="production",
        auth_mode="firebase",
        firebase_project_id="bike-doc-prod",
        artifact_storage_provider="gcs",
        artifact_gcs_bucket="bike-doc-artifacts",
    )

    def _raise_missing_credentials() -> tuple[object, str | None]:
        raise RuntimeError("missing credentials")

    monkeypatch.setattr(
        "bike_doc_api.core.config.google.auth.default",
        _raise_missing_credentials,
    )

    with pytest.raises(ValueError, match="Application Default Credentials"):
        validate_artifact_storage_runtime_configuration(settings, environ={})


def test_gcs_artifact_runtime_validation_requires_client_initialization(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings(
        environment="production",
        auth_mode="firebase",
        firebase_project_id="bike-doc-prod",
        artifact_storage_provider="gcs",
        artifact_gcs_bucket="bike-doc-artifacts",
    )

    monkeypatch.setattr(
        "bike_doc_api.core.config.google.auth.default",
        lambda: (object(), "bike-doc-prod"),
    )

    class _FakeStorageClient:
        def __init__(self, *, project: str, credentials: object) -> None:
            assert project == "bike-doc-prod"
            assert credentials is not None
            raise RuntimeError("client init failed")

    monkeypatch.setattr(
        "google.cloud.storage.Client",
        _FakeStorageClient,
    )

    with pytest.raises(ValueError, match="could not initialize the storage client"):
        validate_artifact_storage_runtime_configuration(settings, environ={})


def test_gcs_artifact_runtime_validation_logs_client_initialization_failure(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    settings = Settings(
        environment="production",
        auth_mode="firebase",
        firebase_project_id="bike-doc-prod",
        artifact_storage_provider="gcs",
        artifact_gcs_bucket="bike-doc-artifacts",
    )

    monkeypatch.setattr(
        "bike_doc_api.core.config.google.auth.default",
        lambda: (object(), "bike-doc-prod"),
    )

    class _FakeStorageClient:
        def __init__(self, *, project: str, credentials: object) -> None:
            assert project == "bike-doc-prod"
            assert credentials is not None
            raise RuntimeError("denied:client-init")

    monkeypatch.setattr(
        "google.cloud.storage.Client",
        _FakeStorageClient,
    )
    caplog.set_level("ERROR")

    with pytest.raises(ValueError, match="could not initialize the storage client"):
        validate_artifact_storage_runtime_configuration(settings, environ={})

    assert "failed to initialize GCS artifact storage client" in caplog.text
    assert "denied:client-init" in caplog.text


def test_gcs_artifact_runtime_validation_accepts_initializable_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings(
        environment="production",
        auth_mode="firebase",
        firebase_project_id="bike-doc-prod",
        artifact_storage_provider="gcs",
        artifact_gcs_bucket="bike-doc-artifacts",
    )
    seen: dict[str, object] = {}

    monkeypatch.setattr(
        "bike_doc_api.core.config.google.auth.default",
        lambda: (object(), "bike-doc-prod"),
    )

    class _FakeStorageClient:
        def __init__(self, *, project: str, credentials: object) -> None:
            seen["project"] = project
            seen["credentials"] = credentials
            seen["initialized"] = True

    monkeypatch.setattr(
        "google.cloud.storage.Client",
        _FakeStorageClient,
    )

    validate_artifact_storage_runtime_configuration(settings, environ={})

    assert seen["project"] == "bike-doc-prod"
    assert seen["initialized"] is True


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("diagnostic_llm_provider", "local"),
        ("diagnostic_agent_temperature", -0.1),
        ("diagnostic_agent_temperature", 2.1),
        ("diagnostic_agent_max_output_tokens", 0),
        ("diagnostic_agent_timeout_seconds", 0),
        ("price_lookup_provider", "local"),
        ("price_lookup_llm_provider", "local"),
        ("price_lookup_temperature", -0.1),
        ("price_lookup_temperature", 2.1),
        ("price_lookup_max_output_tokens", 0),
        ("price_lookup_timeout_seconds", 0),
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
    validate_price_lookup_runtime_configuration(
        Settings(
            environment="test",
            price_lookup_provider="gemini_grounded",
            price_lookup_llm_provider="vertex_ai",
        ),
        environ={},
    )


def test_price_lookup_runtime_validation_ignores_unavailable_provider() -> None:
    validate_price_lookup_runtime_configuration(
        Settings(environment="local", price_lookup_provider="unavailable"),
        environ={},
    )


def test_google_ai_price_lookup_validation_requires_api_key() -> None:
    settings = Settings(
        environment="local",
        price_lookup_provider="gemini_grounded",
        price_lookup_llm_provider="google_ai",
    )

    with pytest.raises(ValueError, match="price lookup"):
        validate_price_lookup_runtime_configuration(settings, environ={})


def test_google_ai_price_lookup_validation_accepts_api_key() -> None:
    settings = Settings(
        environment="local",
        price_lookup_provider="gemini_grounded",
        price_lookup_llm_provider="google_ai",
    )

    validate_price_lookup_runtime_configuration(
        settings,
        environ={"GEMINI_API_KEY": "test-key"},
    )


def test_vertex_price_lookup_validation_requires_vertex_environment() -> None:
    settings = Settings(
        environment="local",
        price_lookup_provider="gemini_grounded",
        price_lookup_llm_provider="vertex_ai",
    )

    with pytest.raises(ValueError, match="GOOGLE_CLOUD_LOCATION"):
        validate_price_lookup_runtime_configuration(
            settings,
            environ={
                "GOOGLE_GENAI_USE_VERTEXAI": "true",
                "GOOGLE_CLOUD_PROJECT": "bike-doc",
            },
        )


def test_env_example_documents_diagnostic_runtime_settings() -> None:
    env_example = Path(__file__).resolve().parents[4] / ".env.example"
    content = env_example.read_text(encoding="utf-8")

    for variable in [
        "BIKE_DOC_API_AUTH_MODE",
        "BIKE_DOC_API_FIREBASE_PROJECT_ID",
        "BIKE_DOC_API_ARTIFACT_STORAGE_PROVIDER",
        "BIKE_DOC_API_ARTIFACT_GCS_BUCKET",
        "GOOGLE_APPLICATION_CREDENTIALS",
        "BIKE_DOC_API_DIAGNOSTIC_LLM_PROVIDER",
        "BIKE_DOC_API_DIAGNOSTIC_AGENT_MODEL",
        "BIKE_DOC_API_DIAGNOSTIC_AGENT_TEMPERATURE",
        "BIKE_DOC_API_DIAGNOSTIC_AGENT_MAX_OUTPUT_TOKENS",
        "BIKE_DOC_API_DIAGNOSTIC_AGENT_TIMEOUT_SECONDS",
        "BIKE_DOC_API_PRICE_LOOKUP_PROVIDER",
        "BIKE_DOC_API_PRICE_LOOKUP_LLM_PROVIDER",
        "BIKE_DOC_API_PRICE_LOOKUP_MODEL",
        "BIKE_DOC_API_PRICE_LOOKUP_TEMPERATURE",
        "BIKE_DOC_API_PRICE_LOOKUP_MAX_OUTPUT_TOKENS",
        "BIKE_DOC_API_PRICE_LOOKUP_TIMEOUT_SECONDS",
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
