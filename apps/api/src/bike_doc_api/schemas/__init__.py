"""Pydantic API schema package."""

from bike_doc_api.schemas.artifact import ArtifactRef, artifact_ref_from_model
from bike_doc_api.schemas.bike import BikeProfile, bike_profile_from_model
from bike_doc_api.schemas.common import (
    ArtifactMediaType,
    ArtifactPurpose,
    ArtifactStatus,
    Confidence,
    ErrorResponse,
    PhaseReportType,
    RepairSessionPhase,
    RepairSessionStatus,
    SafetySeverity,
    SafetyState,
    UserSkillLevel,
)
from bike_doc_api.schemas.event import (
    ArtifactReferencedEventData,
    AssistantDeltaEventData,
    AssistantMessageCompletedEventData,
    ErrorEventData,
    ExecutionStepUpdatedEventData,
    HeartbeatEventData,
    InputRequestedEventData,
    PhaseReportCreatedEventData,
    PhaseTransitionedEventData,
    RepairSessionEvent,
    RepairSessionEventType,
    SafetyEscalatedEventData,
    TurnCompletedEventData,
    TurnStartedEventData,
    repair_session_event_from_model,
)
from bike_doc_api.schemas.repair_session import (
    ExecutionProgress,
    InputChoice,
    InputRequest,
    LatestReports,
    RepairSession,
    RepairSessionCreate,
    repair_session_from_model,
)
from bike_doc_api.schemas.report import (
    AlternateHypothesis,
    Diagnosis,
    DiagnosticReportV1,
    PhaseReportEnvelope,
    SafetyFlag,
    phase_report_envelope_from_model,
)
from bike_doc_api.schemas.turn import (
    TurnAccepted,
    TurnCreate,
    UserTurnMessage,
    turn_accepted_from_model,
)
from bike_doc_api.schemas.user import User, user_from_model
