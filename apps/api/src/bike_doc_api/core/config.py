"""Application settings."""

import logging
import os
from collections.abc import Mapping
from functools import lru_cache
from math import isfinite
from pathlib import Path
from typing import Literal

import google.auth
from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)


class Settings(BaseSettings):
    """Typed settings loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_prefix="BIKE_DOC_API_",
        extra="ignore",
    )

    app_name: str = Field(default="Bike Doc API", min_length=1)
    environment: str = Field(default="local", min_length=1)
    debug: bool = False
    cors_origins: list[str] = Field(default_factory=list)
    database_url: str = Field(
        default="postgresql+asyncpg://bikedoc:bikedoc@localhost:5432/bikedoc",
        min_length=1,
    )
    auth_mode: Literal["firebase", "dev", "local_unsigned_jwt"] = "dev"
    dev_auth_token: str = "dev-token"
    dev_auth_subject: str = "dev-user"
    dev_auth_email: str = "dev@example.com"
    dev_auth_display_name: str = "Dev User"
    firebase_project_id: str | None = None
    log_level: str | None = None
    log_format: Literal["console", "json"] | None = None
    artifact_storage_provider: Literal["local", "gcs"] = "local"
    artifact_local_storage_root: Path = Path("apps/api/.local/artifacts")
    artifact_gcs_bucket: str | None = None
    artifact_max_upload_bytes: int = Field(default=10 * 1024 * 1024, gt=0)
    diagnostic_llm_provider: Literal["google_ai", "vertex_ai"] = "google_ai"
    diagnostic_agent_model: str = Field(default="gemini-2.5-flash", min_length=1)
    diagnostic_agent_temperature: float = Field(default=0.2, ge=0.0, le=2.0)
    diagnostic_agent_max_output_tokens: int = Field(default=2048, gt=0)
    diagnostic_agent_timeout_seconds: float = Field(default=30.0, gt=0.0)

    @field_validator("environment")
    @classmethod
    def validate_environment(cls, value: str) -> str:
        """Normalize the runtime environment name."""
        environment = value.strip()
        if not environment:
            raise ValueError("environment must not be empty")
        return environment

    @field_validator("cors_origins")
    @classmethod
    def validate_cors_origins(cls, value: list[str]) -> list[str]:
        """Reject blank CORS origin entries."""
        origins = [origin.strip() for origin in value]
        if any(not origin for origin in origins):
            raise ValueError("cors_origins must not contain blank values")
        return origins

    @field_validator("database_url")
    @classmethod
    def validate_database_url(cls, value: str) -> str:
        """Normalize the SQLAlchemy database URL."""
        database_url = value.strip()
        if not database_url:
            raise ValueError("database_url must not be empty")
        return database_url

    @field_validator("auth_mode", mode="before")
    @classmethod
    def validate_auth_mode(cls, value: object) -> object:
        """Normalize the configured auth mode."""
        if isinstance(value, str):
            return value.strip().lower()
        return value

    @field_validator(
        "dev_auth_token",
        "dev_auth_subject",
        "dev_auth_email",
        "dev_auth_display_name",
    )
    @classmethod
    def validate_dev_auth_values(cls, value: str) -> str:
        """Reject blank fixed-dev-token identity values."""
        normalized = value.strip()
        if not normalized:
            raise ValueError("dev auth values must not be empty")
        return normalized

    @field_validator("firebase_project_id")
    @classmethod
    def validate_firebase_project_id(cls, value: str | None) -> str | None:
        """Normalize optional Firebase project ID settings."""
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None

    @field_validator("log_level", mode="before")
    @classmethod
    def validate_log_level(cls, value: object) -> str | None:
        """Normalize optional stdlib logging level names."""
        if value is None:
            return None
        if isinstance(value, str):
            log_level = value.strip().upper()
            if not log_level:
                return None
            if log_level in logging.getLevelNamesMapping():
                return log_level
        raise ValueError("log_level must be a valid stdlib logging level name")

    @field_validator("log_format", mode="before")
    @classmethod
    def validate_log_format(cls, value: object) -> object:
        """Treat empty log format values as unset."""
        if isinstance(value, str):
            log_format = value.strip().lower()
            return log_format or None
        return value

    @field_validator("artifact_storage_provider", mode="before")
    @classmethod
    def validate_artifact_storage_provider(cls, value: object) -> object:
        """Normalize the configured artifact storage provider."""
        if isinstance(value, str):
            return value.strip().lower()
        return value

    @field_validator("artifact_gcs_bucket")
    @classmethod
    def validate_artifact_gcs_bucket(cls, value: str | None) -> str | None:
        """Normalize the optional artifact GCS bucket setting."""
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None

    @field_validator("diagnostic_agent_model")
    @classmethod
    def validate_diagnostic_agent_model(cls, value: str) -> str:
        """Normalize the diagnostic agent model setting."""
        model = value.strip()
        if not model:
            raise ValueError("diagnostic_agent_model must not be empty")
        return model

    @field_validator("diagnostic_llm_provider", mode="before")
    @classmethod
    def validate_diagnostic_llm_provider(cls, value: object) -> object:
        """Normalize the diagnostic LLM provider setting."""
        if isinstance(value, str):
            return value.strip().lower()
        return value

    @field_validator(
        "diagnostic_agent_temperature",
        "diagnostic_agent_timeout_seconds",
    )
    @classmethod
    def validate_finite_diagnostic_float(cls, value: float) -> float:
        """Reject non-finite diagnostic generation settings."""
        if not isfinite(value):
            raise ValueError("diagnostic numeric settings must be finite")
        return value

    @model_validator(mode="after")
    def validate_auth_environment(self) -> "Settings":
        """Prevent local fixed-token auth from being enabled in production."""
        environment = self.environment.lower()
        if environment == "production" and self.auth_mode != "firebase":
            raise ValueError("only firebase auth mode is permitted in production")
        if self.auth_mode == "firebase" and self.firebase_project_id is None:
            raise ValueError("firebase_project_id is required in firebase auth mode")
        if self.artifact_storage_provider == "gcs" and self.artifact_gcs_bucket is None:
            raise ValueError(
                "artifact_gcs_bucket is required when artifact_storage_provider=gcs"
            )
        return self


@lru_cache
def get_settings() -> Settings:
    """Return cached process settings."""
    return Settings()


def validate_diagnostic_runtime_configuration(
    settings: Settings,
    *,
    environ: Mapping[str, str] | None = None,
) -> None:
    """Validate provider credentials required by the configured ADK runtime."""

    if settings.environment.lower() == "test":
        return

    env = environ if environ is not None else os.environ
    if settings.diagnostic_llm_provider == "google_ai":
        if env.get("GEMINI_API_KEY") or env.get("GOOGLE_API_KEY"):
            return
        raise ValueError(
            "google_ai diagnostic runtime requires GEMINI_API_KEY or GOOGLE_API_KEY",
        )

    if env.get("GOOGLE_GENAI_USE_VERTEXAI", "").strip().lower() != "true":
        raise ValueError(
            "vertex_ai diagnostic runtime requires GOOGLE_GENAI_USE_VERTEXAI=true",
        )
    if not env.get("GOOGLE_CLOUD_PROJECT"):
        raise ValueError("vertex_ai diagnostic runtime requires GOOGLE_CLOUD_PROJECT")
    if not env.get("GOOGLE_CLOUD_LOCATION"):
        raise ValueError("vertex_ai diagnostic runtime requires GOOGLE_CLOUD_LOCATION")


def validate_artifact_storage_runtime_configuration(
    settings: Settings,
    *,
    environ: Mapping[str, str] | None = None,
) -> None:
    """Validate runtime requirements for the configured artifact storage."""

    if settings.environment.lower() == "test":
        return
    if settings.artifact_storage_provider != "gcs":
        return
    if settings.artifact_gcs_bucket is None:
        raise ValueError(
            "gcs artifact storage requires BIKE_DOC_API_ARTIFACT_GCS_BUCKET",
        )

    env = environ if environ is not None else os.environ
    try:
        credentials, project_id = google.auth.default()
    except Exception as exc:
        raise ValueError(
            "gcs artifact storage requires Google Application Default Credentials; "
            "in production attach a service account to the runtime, and for local "
            "development set GOOGLE_APPLICATION_CREDENTIALS or run "
            "'gcloud auth application-default login'"
        ) from exc

    effective_project = project_id or env.get("GOOGLE_CLOUD_PROJECT")
    if not effective_project:
        raise ValueError(
            "gcs artifact storage requires GOOGLE_CLOUD_PROJECT or default "
            "project resolution from the active Google credentials"
        )

    from google.cloud import storage  # type: ignore[import-untyped]

    try:
        client = storage.Client(project=effective_project, credentials=credentials)
        client.get_bucket(settings.artifact_gcs_bucket)
    except Exception as exc:
        logger.exception(
            "failed to access configured GCS artifact bucket",
            extra={
                "bucket_name": settings.artifact_gcs_bucket,
                "project_id": effective_project,
            },
        )
        raise ValueError(
            "gcs artifact storage could not access the configured bucket; verify "
            "the bucket exists and the runtime service account has bucket access"
        ) from exc
