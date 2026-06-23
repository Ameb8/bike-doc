"""Turn API schemas and mappers."""

from typing import Literal

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


class TurnCreate(APIBaseModel):
    """Create turn request."""

    schema_version: Literal["ai_turn.v1"]
    client_turn_id: str
    message: UserTurnMessage
    responds_to_input_request_id: str | None = None


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
