"""Repair session API schemas and mappers."""

from datetime import datetime
from enum import StrEnum

from pydantic import Field

from bike_doc_api.models.repair_session import RepairSession as RepairSessionModel
from bike_doc_api.schemas.common import (
    APIBaseModel,
    RepairSessionPhase,
    RepairSessionStatus,
    SafetyState,
)


class InputRequestType(StrEnum):
    """User input request type."""

    TEXT = "text"
    PHOTO = "photo"
    MULTIPLE_CHOICE = "multiple_choice"
    DECISION = "decision"
    CONFIRMATION = "confirmation"
    NONE = "none"


class ExecutionProgress(APIBaseModel):
    """Lightweight execution phase progress."""

    current_step_index: int | None = Field(ge=1)
    total_steps: int | None = Field(ge=1)


class InputChoice(APIBaseModel):
    """A selectable user input choice."""

    value: str
    label: str
    description: str | None = None


class InputRequest(APIBaseModel):
    """Public request for more user input."""

    id: str
    type: InputRequestType
    prompt: str
    required: bool
    accepted_media_types: list[str]
    choices: list[InputChoice]
    min_artifacts: int | None = None
    max_artifacts: int | None = None
    created_at: datetime


class LatestReports(APIBaseModel):
    """Latest phase report IDs on a repair session."""

    diagnostic_report_id: str | None
    plan_report_id: str | None
    execution_report_id: str | None
    shop_referral_report_id: str | None


class RepairSessionCreate(APIBaseModel):
    """Create repair session request."""

    bike_id: str
    client_session_id: str | None = None


class RepairSession(APIBaseModel):
    """Public repair session state."""

    id: str
    user_id: str
    bike_id: str
    phase: RepairSessionPhase
    status: RepairSessionStatus
    safety_state: SafetyState
    current_input_request: InputRequest | None = None
    execution_progress: ExecutionProgress | None = None
    latest_reports: LatestReports
    latest_event_id: str
    created_at: datetime
    updated_at: datetime


def repair_session_from_model(repair_session: RepairSessionModel) -> RepairSession:
    """Map a persistence repair session to the public schema."""

    return RepairSession(
        id=repair_session.id,
        user_id=repair_session.user_id,
        bike_id=repair_session.bike_id,
        phase=RepairSessionPhase(repair_session.phase),
        status=RepairSessionStatus(repair_session.status),
        safety_state=SafetyState(repair_session.safety_state),
        current_input_request=(
            None
            if repair_session.current_input_request is None
            else InputRequest.model_validate(repair_session.current_input_request)
        ),
        execution_progress=(
            None
            if repair_session.execution_progress is None
            else ExecutionProgress.model_validate(repair_session.execution_progress)
        ),
        latest_reports=LatestReports(
            diagnostic_report_id=repair_session.diagnostic_report_id,
            plan_report_id=repair_session.plan_report_id,
            execution_report_id=repair_session.execution_report_id,
            shop_referral_report_id=repair_session.shop_referral_report_id,
        ),
        latest_event_id=str(repair_session.latest_event_sequence),
        created_at=repair_session.created_at,
        updated_at=repair_session.updated_at,
    )
