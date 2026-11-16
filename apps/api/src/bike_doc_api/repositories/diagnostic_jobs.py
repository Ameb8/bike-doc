"""Atomic diagnostic job/product terminalization in the caller's transaction."""

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bike_doc_api.models.background_job import BackgroundJob
from bike_doc_api.models.event import RepairSessionEvent
from bike_doc_api.repositories.background_jobs import JobError
from bike_doc_api.repositories.diagnostic_terminalization import (
    append_diagnostic_completion,
)
from bike_doc_api.repositories.events import RepairSessionEventRepository
from bike_doc_api.repositories.repair_sessions import (
    RepairSessionRepository,
    RepairTurnRepository,
)
from bike_doc_api.schemas.background_jobs import DiagnosticTurnInputV1


async def terminalize_diagnostic(session: AsyncSession, row: BackgroundJob) -> None:
    try:
        identity = DiagnosticTurnInputV1.model_validate(
            {"turn_id": row.input.get("turn_id")}
        )
    except ValidationError:
        return
    turn = await RepairTurnRepository(session).get(identity.turn_id)
    if turn is None or turn.phase != "diagnostic":
        return
    if row.effect_boundary_at is not None and row.state == "dead":
        row.state = "interrupted"
        row.latest_error_category = JobError.EXECUTION_LOST.value
    repair = await RepairSessionRepository(session).get_for_update(
        turn.repair_session_id
    )
    if repair is None:
        return
    completed = (
        await session.scalars(
            select(RepairSessionEvent).where(
                RepairSessionEvent.turn_id == turn.id,
                RepairSessionEvent.type == "turn.completed",
            )
        )
    ).first()
    if completed is not None:
        # Crash after product commit but before job settlement: completion
        # proves safe processing. Preserve any newer turn's session state.
        row.state = "succeeded"
        row.latest_error_category = None
        return
    assert row.terminal_at is not None
    await append_diagnostic_completion(
        RepairSessionEventRepository(session),
        repair,
        turn_id=turn.id,
        status="awaiting_user",
        now=row.terminal_at,
        processing_error=True,
    )
