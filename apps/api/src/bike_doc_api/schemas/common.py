"""Shared public API schemas and enum values."""

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict


class APIBaseModel(BaseModel):
    """Base model for public API schemas."""

    model_config = ConfigDict(extra="forbid")


class ErrorBody(APIBaseModel):
    """Public error body."""

    code: str
    message: str
    details: dict[str, Any] | None = None


class ErrorResponse(APIBaseModel):
    """Public error envelope."""

    error: ErrorBody


class UserSkillLevel(StrEnum):
    """User repair skill level."""

    UNKNOWN = "unknown"
    BEGINNER = "beginner"
    INTERMEDIATE = "intermediate"
    ADVANCED = "advanced"


class RepairSessionPhase(StrEnum):
    """Public repair workflow phase."""

    DIAGNOSTIC = "diagnostic"
    PLANNING = "planning"
    EXECUTION = "execution"
    COMPLETED = "completed"
    SHOP_REFERRED = "shop_referred"
    CANCELLED = "cancelled"


class RepairSessionStatus(StrEnum):
    """Public repair-session status."""

    CREATED = "created"
    RUNNING = "running"
    AWAITING_USER = "awaiting_user"
    AWAITING_DECISION = "awaiting_decision"
    BLOCKED_SAFETY = "blocked_safety"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class SafetyState(StrEnum):
    """Current session safety state."""

    OK = "ok"
    CAUTION = "caution"
    SHOP_RECOMMENDED = "shop_recommended"
    BLOCKED = "blocked"


class SafetySeverity(StrEnum):
    """Safety flag severity."""

    INFO = "info"
    CAUTION = "caution"
    WARNING = "warning"
    BLOCKING = "blocking"


class ArtifactPurpose(StrEnum):
    """Artifact purpose values."""

    DIAGNOSTIC_PHOTO = "diagnostic_photo"
    VERIFICATION_PHOTO = "verification_photo"
    BIKE_PROFILE_PHOTO = "bike_profile_photo"
    REPAIR_REFERENCE = "repair_reference"
    OTHER = "other"


class ArtifactMediaType(StrEnum):
    """Artifact media type values."""

    IMAGE = "image"
    VIDEO = "video"
    AUDIO = "audio"
    DOCUMENT = "document"
    OTHER = "other"


class ArtifactStatus(StrEnum):
    """Artifact processing status."""

    UPLOADED = "uploaded"
    PROCESSING = "processing"
    READY = "ready"
    REJECTED = "rejected"


class PhaseReportType(StrEnum):
    """Phase report type."""

    DIAGNOSTIC = "diagnostic"
    PLAN = "plan"
    EXECUTION = "execution"
    SHOP_REFERRAL = "shop_referral"


class Confidence(StrEnum):
    """Diagnostic confidence value."""

    UNKNOWN = "unknown"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
