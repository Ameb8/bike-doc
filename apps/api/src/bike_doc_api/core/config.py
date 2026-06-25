"""Application settings."""

import logging
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


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
    auth_mode: Literal["production", "dev"] = "production"
    dev_auth_token: str = "dev-token"
    dev_auth_subject: str = "dev-user"
    dev_auth_email: str = "dev@example.com"
    dev_auth_display_name: str = "Dev User"
    log_level: str | None = None
    log_format: Literal["console", "json"] | None = None
    artifact_storage_provider: Literal["local", "gcs"] = "local"
    artifact_local_storage_root: Path = Path("apps/api/.local/artifacts")
    artifact_max_upload_bytes: int = Field(default=10 * 1024 * 1024, gt=0)
    diagnostic_agent_model: str = Field(default="gemini-2.5-flash", min_length=1)

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

    @field_validator("diagnostic_agent_model")
    @classmethod
    def validate_diagnostic_agent_model(cls, value: str) -> str:
        """Normalize the diagnostic agent model setting."""
        model = value.strip()
        if not model:
            raise ValueError("diagnostic_agent_model must not be empty")
        return model

    @model_validator(mode="after")
    def validate_auth_environment(self) -> "Settings":
        """Prevent local fixed-token auth from being enabled in production."""
        if self.environment.lower() == "production" and self.auth_mode == "dev":
            raise ValueError("dev auth mode must not be enabled in production")
        return self


@lru_cache
def get_settings() -> Settings:
    """Return cached process settings."""
    return Settings()
