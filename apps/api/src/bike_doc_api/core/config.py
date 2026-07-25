"""Application settings."""

import logging
import os
from collections.abc import Mapping
from functools import lru_cache
from math import isfinite
from pathlib import Path
from typing import Literal

import google.auth
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)

ImageAnalysisMode = Literal["off", "pixels_only", "shadow", "enabled"]


class ProfileInferenceFieldPolicySettings(BaseModel):
    """Deployment data for one canonical field/evidence policy."""

    model_config = ConfigDict(extra="forbid")

    field_path: str = Field(min_length=1)
    evidence_class: str = Field(min_length=1)
    calibration_key: str = Field(min_length=1)
    policy_version: str = Field(min_length=1)
    auto_fill_threshold: float = Field(ge=0.0, le=1.0)
    auto_overwrite_threshold: float | None = Field(default=None, ge=0.0, le=1.0)
    precision_gate_passed: bool = False
    accepted_baseline_version: str | None = None
    regression_evidence_passed: bool = False
    promoted: bool = False

    @field_validator(
        "field_path", "evidence_class", "calibration_key", "policy_version"
    )
    @classmethod
    def validate_identifiers(cls, value: str) -> str:
        """Reject whitespace-only deployment identifiers."""

        normalized = value.strip()
        if not normalized:
            raise ValueError("profile inference policy identifiers must not be blank")
        return normalized

    @field_validator("accepted_baseline_version")
    @classmethod
    def validate_baseline_version(cls, value: str | None) -> str | None:
        """Normalize the optional accepted evaluation baseline identifier."""

        if value is None:
            return None
        normalized = value.strip()
        return normalized or None

    @model_validator(mode="after")
    def validate_threshold_order(self) -> "ProfileInferenceFieldPolicySettings":
        """Keep overwrite at least as selective as fill when configured."""

        if (
            self.auto_overwrite_threshold is not None
            and self.auto_overwrite_threshold < self.auto_fill_threshold
        ):
            raise ValueError(
                "auto_overwrite_threshold must be greater than or equal to "
                "auto_fill_threshold",
            )
        return self


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
    image_analysis_mode: ImageAnalysisMode = "off"
    diagnostic_llm_provider: Literal["google_ai", "vertex_ai"] = "google_ai"
    diagnostic_agent_model: str = Field(default="gemini-2.5-flash", min_length=1)
    diagnostic_agent_temperature: float = Field(default=0.2, ge=0.0, le=2.0)
    diagnostic_agent_max_output_tokens: int = Field(default=2048, gt=0)
    diagnostic_agent_timeout_seconds: float = Field(default=30.0, gt=0.0)
    profile_inference_llm_provider: Literal["google_ai", "vertex_ai"] = "google_ai"
    profile_inference_model: str = Field(default="gemini-2.5-flash", min_length=1)
    profile_inference_timeout_seconds: float = Field(default=30.0, gt=0.0)
    profile_inference_max_attempts: int = Field(default=3, ge=1, le=5)
    profile_inference_extractor_version: str = Field(
        default="drivetrain-specifications.v1",
        min_length=1,
    )
    profile_inference_policy_mode: Literal[
        "shadow", "bootstrap-v1", "evaluated", "production"
    ] = "shadow"
    profile_inference_policies: list[ProfileInferenceFieldPolicySettings] = Field(
        default_factory=list,
    )
    # Kept as a compatibility alias for deployments created by the tracer
    # rollout. New deployments should use profile_inference_policy_mode.
    profile_inference_resolver_policy: (
        Literal["production", "shadow", "bootstrap-v1", "evaluated"] | None
    ) = None
    price_lookup_provider: Literal["unavailable", "gemini_grounded"] = "unavailable"
    price_lookup_llm_provider: Literal["google_ai", "vertex_ai"] = "google_ai"
    price_lookup_model: str = Field(default="gemini-2.5-flash", min_length=1)
    price_lookup_temperature: float = Field(default=0.1, ge=0.0, le=2.0)
    price_lookup_max_output_tokens: int = Field(default=1536, gt=0)
    price_lookup_timeout_seconds: float = Field(default=20.0, gt=0.0)

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

    @field_validator("image_analysis_mode", mode="before")
    @classmethod
    def validate_image_analysis_mode(cls, value: object) -> object:
        """Normalize the static diagnostic-image rollout mode."""

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

    @field_validator("profile_inference_llm_provider", mode="before")
    @classmethod
    def validate_profile_inference_llm_provider(cls, value: object) -> object:
        """Normalize the isolated image-extraction provider selection."""

        if isinstance(value, str):
            return value.strip().lower()
        return value

    @field_validator("profile_inference_model", "profile_inference_extractor_version")
    @classmethod
    def validate_profile_inference_strings(cls, value: str) -> str:
        """Reject blank model or version identifiers used for run idempotency."""

        normalized = value.strip()
        if not normalized:
            raise ValueError("profile inference settings must not be blank")
        return normalized

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

    @field_validator("profile_inference_timeout_seconds")
    @classmethod
    def validate_finite_profile_inference_float(cls, value: float) -> float:
        """Reject non-finite profile-inference generation settings."""

        if not isfinite(value):
            raise ValueError("profile inference numeric settings must be finite")
        return value

    @field_validator("price_lookup_provider", mode="before")
    @classmethod
    def validate_price_lookup_provider(cls, value: object) -> object:
        """Normalize the configured price lookup provider."""
        if isinstance(value, str):
            return value.strip().lower()
        return value

    @field_validator("price_lookup_llm_provider", mode="before")
    @classmethod
    def validate_price_lookup_llm_provider(cls, value: object) -> object:
        """Normalize the configured price lookup LLM provider."""
        if isinstance(value, str):
            return value.strip().lower()
        return value

    @field_validator("price_lookup_model")
    @classmethod
    def validate_price_lookup_model(cls, value: str) -> str:
        """Normalize the price lookup model setting."""
        model = value.strip()
        if not model:
            raise ValueError("price_lookup_model must not be empty")
        return model

    @field_validator(
        "price_lookup_temperature",
        "price_lookup_timeout_seconds",
    )
    @classmethod
    def validate_finite_price_lookup_float(cls, value: float) -> float:
        """Reject non-finite price lookup generation settings."""
        if not isfinite(value):
            raise ValueError("price lookup numeric settings must be finite")
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
        if self.profile_inference_resolver_policy is not None:
            legacy_mode = {
                "production": "evaluated",
                "shadow": "shadow",
                "bootstrap-v1": "bootstrap-v1",
                "evaluated": "evaluated",
            }[self.profile_inference_resolver_policy]
            if (
                "profile_inference_policy_mode" in self.model_fields_set
                and (
                    "evaluated"
                    if self.profile_inference_policy_mode == "production"
                    else self.profile_inference_policy_mode
                )
                != legacy_mode
            ):
                raise ValueError(
                    "profile inference policy mode conflicts with its "
                    "compatibility alias"
                )
            self.profile_inference_policy_mode = legacy_mode  # type: ignore[assignment]
        if (
            environment == "production"
            and self.profile_inference_policy_mode == "bootstrap-v1"
        ):
            raise ValueError(
                "bootstrap-v1 profile inference policy is not permitted in production"
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


def validate_price_lookup_runtime_configuration(
    settings: Settings,
    *,
    environ: Mapping[str, str] | None = None,
) -> None:
    """Validate provider credentials required by live price lookup."""

    if settings.environment.lower() == "test":
        return
    if settings.price_lookup_provider != "gemini_grounded":
        return

    env = environ if environ is not None else os.environ
    if settings.price_lookup_llm_provider == "google_ai":
        if env.get("GEMINI_API_KEY") or env.get("GOOGLE_API_KEY"):
            return
        raise ValueError(
            "google_ai price lookup requires GEMINI_API_KEY or GOOGLE_API_KEY",
        )

    if env.get("GOOGLE_GENAI_USE_VERTEXAI", "").strip().lower() != "true":
        raise ValueError(
            "vertex_ai price lookup requires GOOGLE_GENAI_USE_VERTEXAI=true",
        )
    if not env.get("GOOGLE_CLOUD_PROJECT"):
        raise ValueError("vertex_ai price lookup requires GOOGLE_CLOUD_PROJECT")
    if not env.get("GOOGLE_CLOUD_LOCATION"):
        raise ValueError("vertex_ai price lookup requires GOOGLE_CLOUD_LOCATION")


def validate_profile_inference_runtime_configuration(
    settings: Settings,
    *,
    environ: Mapping[str, str] | None = None,
) -> None:
    """Validate credentials required by the configured image extractor."""

    if settings.environment.lower() == "test":
        return
    env = environ if environ is not None else os.environ
    if settings.profile_inference_llm_provider == "google_ai":
        if env.get("GEMINI_API_KEY") or env.get("GOOGLE_API_KEY"):
            return
        raise ValueError(
            "google_ai profile inference requires GEMINI_API_KEY or GOOGLE_API_KEY",
        )
    if env.get("GOOGLE_GENAI_USE_VERTEXAI", "").strip().lower() != "true":
        raise ValueError(
            "vertex_ai profile inference requires GOOGLE_GENAI_USE_VERTEXAI=true",
        )
    if not env.get("GOOGLE_CLOUD_PROJECT"):
        raise ValueError("vertex_ai profile inference requires GOOGLE_CLOUD_PROJECT")
    if not env.get("GOOGLE_CLOUD_LOCATION"):
        raise ValueError("vertex_ai profile inference requires GOOGLE_CLOUD_LOCATION")


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

    # Construct the client eagerly so startup still validates ADC and project
    # resolution, but do not fetch bucket metadata. Runtime object operations
    # may succeed with narrower IAM than bucket-level metadata access.
    from google.cloud import storage  # type: ignore[import-untyped]

    try:
        storage.Client(project=effective_project, credentials=credentials)
    except Exception as exc:
        logger.exception(
            "failed to initialize GCS artifact storage client",
            extra={
                "bucket_name": settings.artifact_gcs_bucket,
                "project_id": effective_project,
            },
        )
        raise ValueError(
            "gcs artifact storage could not initialize the storage client; verify "
            "the runtime credentials and project configuration"
        ) from exc
