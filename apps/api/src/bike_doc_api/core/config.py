"""Application settings."""

import logging
from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator
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
    log_level: str | None = None
    log_format: Literal["console", "json"] | None = None

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


@lru_cache
def get_settings() -> Settings:
    """Return cached process settings."""
    return Settings()
