"""Event API schemas and mappers."""

from datetime import datetime
from enum import StrEnum
from typing import Any, Literal, Self, cast

from pydantic import Field, model_validator

from bike_doc_api.models.event import RepairSessionEvent as RepairSessionEventModel
from bike_doc_api.schemas.artifact import ArtifactRef
from bike_doc_api.schemas.common import (
    APIBaseModel,
    PhaseReportType,
    RepairSessionPhase,
    RepairSessionStatus,
    SafetyState,
)
from bike_doc_api.schemas.repair_session import (
    ExecutionProgress,
    InputRequest,
    RepairSession,
)
from bike_doc_api.schemas.report import SafetyFlag


class RepairSessionEventType(StrEnum):
    """Public repair session event type."""

    TURN_STARTED = "turn.started"
    ASSISTANT_DELTA = "assistant.delta"
    ASSISTANT_MESSAGE_COMPLETED = "assistant.message.completed"
    INPUT_REQUESTED = "input.requested"
    ARTIFACT_REFERENCED = "artifact.referenced"
    PHASE_REPORT_CREATED = "phase.report.created"
    PHASE_TRANSITIONED = "phase.transitioned"
    SAFETY_ESCALATED = "safety.escalated"
    EXECUTION_STEP_UPDATED = "execution.step.updated"
    TURN_COMPLETED = "turn.completed"
    ERROR = "error"
    HEARTBEAT = "heartbeat"


class DisplaySafetyLevel(StrEnum):
    """Assistant message display safety level."""

    NORMAL = "normal"
    CAUTION = "caution"
    WARNING = "warning"


class ExecutionStepStatus(StrEnum):
    """Execution step status."""

    PENDING = "pending"
    ACTIVE = "active"
    AWAITING_VERIFICATION = "awaiting_verification"
    VERIFIED = "verified"
    FAILED = "failed"
    SKIPPED = "skipped"


class TurnStartedEventData(APIBaseModel):
    """Turn started event payload."""

    turn_id: str
    phase: RepairSessionPhase


class AssistantDeltaEventData(APIBaseModel):
    """Assistant text delta payload."""

    text: str


class AssistantMessageCompletedEventData(APIBaseModel):
    """Assistant completed-message payload."""

    message_id: str
    full_text: str
    artifact_ids: list[str]
    display_safety_level: DisplaySafetyLevel = DisplaySafetyLevel.NORMAL


class InputRequestedEventData(APIBaseModel):
    """Input requested event payload."""

    input_request: InputRequest


class ArtifactReferencedEventData(APIBaseModel):
    """Artifact referenced event payload."""

    artifact: ArtifactRef


class PhaseReportCreatedEventData(APIBaseModel):
    """Phase report created event payload."""

    report_id: str
    report_type: PhaseReportType
    schema_version: str
    phase: RepairSessionPhase
    summary: str


class PhaseTransitionedEventData(APIBaseModel):
    """Phase transition event payload."""

    from_phase: RepairSessionPhase
    to_phase: RepairSessionPhase
    status: RepairSessionStatus


class SafetyEscalatedEventData(APIBaseModel):
    """Safety escalation event payload."""

    safety_state: SafetyState
    safety_flags: list[SafetyFlag]
    user_message: str
    blocks_repair_instructions: bool
    shop_referral_report_id: str | None = None


class ExecutionStepRecord(APIBaseModel):
    """Execution step record used by execution.step.updated events."""

    index: int = Field(ge=1)
    title: str
    instruction: str | None = None
    status: ExecutionStepStatus
    verification_required: bool
    verification_artifact_ids: list[str] = Field(default_factory=list)
    safety_flags: list[SafetyFlag] = Field(default_factory=list)


class ExecutionStepUpdatedEventData(APIBaseModel):
    """Execution step updated event payload."""

    step: ExecutionStepRecord
    input_request: InputRequest | None = None
    progress: ExecutionProgress | None = None


class TurnCompletedEventData(APIBaseModel):
    """Turn completed event payload."""

    turn_id: str
    session: RepairSession


class ErrorEventData(APIBaseModel):
    """Recoverable processing error event payload."""

    code: str
    message: str
    retryable: bool = False


class HeartbeatEventData(APIBaseModel):
    """Heartbeat event payload."""

    ok: Literal[True]


RepairSessionEventData = (
    TurnStartedEventData
    | AssistantDeltaEventData
    | AssistantMessageCompletedEventData
    | InputRequestedEventData
    | ArtifactReferencedEventData
    | PhaseReportCreatedEventData
    | PhaseTransitionedEventData
    | SafetyEscalatedEventData
    | ExecutionStepUpdatedEventData
    | TurnCompletedEventData
    | ErrorEventData
    | HeartbeatEventData
)

_EVENT_DATA_MODELS: dict[RepairSessionEventType, type[APIBaseModel]] = {
    RepairSessionEventType.TURN_STARTED: TurnStartedEventData,
    RepairSessionEventType.ASSISTANT_DELTA: AssistantDeltaEventData,
    RepairSessionEventType.ASSISTANT_MESSAGE_COMPLETED: (
        AssistantMessageCompletedEventData
    ),
    RepairSessionEventType.INPUT_REQUESTED: InputRequestedEventData,
    RepairSessionEventType.ARTIFACT_REFERENCED: ArtifactReferencedEventData,
    RepairSessionEventType.PHASE_REPORT_CREATED: PhaseReportCreatedEventData,
    RepairSessionEventType.PHASE_TRANSITIONED: PhaseTransitionedEventData,
    RepairSessionEventType.SAFETY_ESCALATED: SafetyEscalatedEventData,
    RepairSessionEventType.EXECUTION_STEP_UPDATED: ExecutionStepUpdatedEventData,
    RepairSessionEventType.TURN_COMPLETED: TurnCompletedEventData,
    RepairSessionEventType.ERROR: ErrorEventData,
    RepairSessionEventType.HEARTBEAT: HeartbeatEventData,
}


def validate_repair_session_event_data(
    event_type: RepairSessionEventType | str,
    data: dict[str, Any],
) -> dict[str, Any]:
    """Validate and serialize event-specific data for persistence."""

    public_event_type = RepairSessionEventType(event_type)
    target_model = _EVENT_DATA_MODELS[public_event_type]
    return target_model.model_validate(data).model_dump(mode="json")


class RepairSessionEvent(APIBaseModel):
    """Public repair session event."""

    id: str
    session_id: str
    turn_id: str | None = None
    type: RepairSessionEventType
    sequence: int = Field(ge=1)
    created_at: datetime
    data: RepairSessionEventData

    @model_validator(mode="after")
    def validate_data_matches_type(self) -> Self:
        """Validate event data against the model selected by event type."""

        target_model = _EVENT_DATA_MODELS[self.type]
        raw_data = (
            self.data.model_dump() if isinstance(self.data, APIBaseModel) else self.data
        )
        self.data = cast(
            RepairSessionEventData,
            target_model.model_validate(raw_data),
        )
        return self


def repair_session_event_from_model(
    event: RepairSessionEventModel,
) -> RepairSessionEvent:
    """Map a persisted event to the public event schema."""

    return RepairSessionEvent(
        id=str(event.sequence),
        session_id=event.repair_session_id,
        turn_id=event.turn_id,
        type=RepairSessionEventType(event.type),
        sequence=event.sequence,
        created_at=event.created_at,
        data=cast(RepairSessionEventData, event.data),
    )
