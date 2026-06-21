"""Application settings."""

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Typed settings loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="BIKE_DOC_API_",
        extra="ignore",
    )

    app_name: str = "Bike Doc API"
    environment: str = "local"
    debug: bool = False
    database_url: str = Field(
        default="postgresql+asyncpg://bikedoc:bikedoc@localhost:5432/bikedoc",
        description="SQLAlchemy async database URL.",
    )
    cors_origins: list[str] = Field(default_factory=list)


@lru_cache
def get_settings() -> Settings:
    """Return cached process settings."""
    return Settings()
