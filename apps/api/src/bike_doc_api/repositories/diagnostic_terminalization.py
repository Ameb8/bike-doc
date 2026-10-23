"""Validated diagnostic completion writes under the caller's session lock."""

from datetime import datetime
from typing import Protocol

from bike_doc_api.models.event import RepairSessionEvent
from bike_doc_api.models.repair_session import RepairSession
from bike_doc_api.schemas.event import (
    RepairSessionEventType,
    validate_repair_session_event_data,
)
from bike_doc_api.schemas.repair_session import repair_session_from_model


class CompletionEvents(Protocol):
    async def add(self, event: RepairSessionEvent) -> RepairSessionEvent: ...


async def append_diagnostic_completion(
    events: CompletionEvents,
    repair: RepairSession,
    *,
    turn_id: str,
    status: str,
    now: datetime,
    processing_error: bool,
) -> None:
    """Append an optional error and completion together, without committing.

    The caller owns locking, durable identity checks, and the job transition.
    This operation is shared by normal orchestration, worker preparation failure,
    and durable job terminalization. Repository commit hooks release hints.
    """
    repair.status = status
    repair.updated_at = now
    if processing_error:
        repair.latest_event_sequence += 1
        await events.add(
            RepairSessionEvent(
                repair_session_id=repair.id,
                turn_id=turn_id,
                sequence=repair.latest_event_sequence,
                type="error",
                data=validate_repair_session_event_data(
                    RepairSessionEventType.ERROR,
                    {
                        "code": "diagnostic_processing_error",
                        "message": "Diagnostic processing could not be completed.",
                        "retryable": True,
                    },
                ),
            )
        )
    repair.latest_event_sequence += 1
    await events.add(
        RepairSessionEvent(
            repair_session_id=repair.id,
            turn_id=turn_id,
            sequence=repair.latest_event_sequence,
            type="turn.completed",
            data=validate_repair_session_event_data(
                RepairSessionEventType.TURN_COMPLETED,
                {
                    "turn_id": turn_id,
                    "session": repair_session_from_model(repair).model_dump(
                        mode="json"
                    ),
                },
            ),
        )
    )
