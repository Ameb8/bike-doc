"""Turn API schemas and mappers."""

from typing import Literal, Self

from pydantic import Field, field_validator, model_validator

from bike_doc_api.models.repair_session import RepairSession as RepairSessionModel
from bike_doc_api.models.repair_session import RepairTurn as RepairTurnModel
from bike_doc_api.schemas.common import APIBaseModel
from bike_doc_api.schemas.repair_session import (
    RepairSession,
    repair_session_from_model,
)


class UserTurnMessage(APIBaseModel):
    """User-authored turn content."""

    text: str | None = None
    artifact_ids: list[str]

    @field_validator("text")
    @classmethod
    def normalize_text(cls, value: str | None) -> str | None:
        """Trim user text and keep blank text as empty for payload validation."""

        if value is None:
            return None
        return value.strip()

    @field_validator("artifact_ids")
    @classmethod
    def validate_artifact_ids(cls, value: list[str]) -> list[str]:
        """Reject blank artifact IDs in public payloads."""

        if any(not artifact_id.strip() for artifact_id in value):
            raise ValueError("artifact_ids must not contain blank IDs")
        return value

    @model_validator(mode="after")
    def validate_usable_input(self) -> Self:
        """Require text, artifact references, or both."""

        if not self.text and not self.artifact_ids:
            raise ValueError("message must include text, artifact_ids, or both")
        return self


class TurnCreate(APIBaseModel):
    """Create turn request."""

    schema_version: Literal["ai_turn.v1"]
    client_turn_id: str = Field(min_length=1)
    message: UserTurnMessage
    responds_to_input_request_id: str | None = None

    @field_validator("client_turn_id")
    @classmethod
    def validate_client_turn_id(cls, value: str) -> str:
        """Normalize and validate the session-scoped idempotency key."""

        normalized = value.strip()
        if not normalized:
            raise ValueError("client_turn_id must not be blank")
        return normalized

    @field_validator("responds_to_input_request_id")
    @classmethod
    def normalize_input_request_id(cls, value: str | None) -> str | None:
        """Normalize optional input-request IDs."""

        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("responds_to_input_request_id must not be blank")
        return normalized


class TurnAccepted(APIBaseModel):
    """Accepted turn response."""

    turn_id: str
    repair_session_id: str
    start_event_id: str
    event_stream_url: str
    session: RepairSession


def turn_accepted_from_model(
    turn: RepairTurnModel,
    repair_session: RepairSessionModel,
) -> TurnAccepted:
    """Map a persisted turn and session to the public accepted-turn response."""

    start_event_id = str(turn.start_event_sequence)
    return TurnAccepted(
        turn_id=turn.id,
        repair_session_id=turn.repair_session_id,
        start_event_id=start_event_id,
        event_stream_url=(
            f"/v1/repair-sessions/{turn.repair_session_id}/events"
            f"?after={start_event_id}"
        ),
        session=repair_session_from_model(repair_session),
    )
