"""Event service unit tests."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from typing import Any

from bike_doc_api.models.event import RepairSessionEvent as RepairSessionEventModel
from bike_doc_api.models.repair_session import RepairSession as RepairSessionModel
from bike_doc_api.schemas.event import RepairSessionEventType
from bike_doc_api.services.events import EventService, EventStream


class _EventStore:
    """In-memory repository double for service behavior tests."""

    def __init__(self) -> None:
        self.session = RepairSessionModel(
            id="rs_service",
            user_id="usr_service",
            bike_id="bike_service",
            phase="diagnostic",
            status="awaiting_user",
            safety_state="caution",
            current_input_request={"id": "req_existing"},
            execution_progress={"current_step_index": 1},
            active_safety_flags=[{"code": "existing_flag"}],
            latest_event_sequence=0,
        )
        self.events: list[RepairSessionEventModel] = []

    async def get_owned(
        self,
        *,
        repair_session_id: str,
        user_id: str,
    ) -> RepairSessionModel | None:
        if repair_session_id != self.session.id or user_id != self.session.user_id:
            return None
        return self.session

    async def append_for_session(
        self,
        *,
        repair_session_id: str,
        event_type: str,
        data: dict[str, Any],
        turn_id: str | None = None,
    ) -> RepairSessionEventModel:
        sequence = self.session.latest_event_sequence + 1
        event = RepairSessionEventModel(
            id=f"evt_internal_{sequence}",
            repair_session_id=repair_session_id,
            turn_id=turn_id,
            sequence=sequence,
            type=event_type,
            data=data,
            created_at=datetime(2026, 6, 21, 17, 0, sequence, tzinfo=UTC),
        )
        self.events.append(event)
        self.session.latest_event_sequence = sequence
        return event

    async def list_after_sequence(
        self,
        *,
        repair_session_id: str,
        after_sequence: int,
        limit: int = 100,
    ) -> list[RepairSessionEventModel]:
        return [
            event
            for event in self.events
            if event.repair_session_id == repair_session_id
            and event.sequence > after_sequence
        ][:limit]


async def test_append_event_updates_latest_sequence_and_public_id() -> None:
    store = _EventStore()
    commit_count = 0

    async def commit() -> None:
        nonlocal commit_count
        commit_count += 1

    event = await EventService(store, store, commit=commit).append_event(
        repair_session_id=store.session.id,
        event_type=RepairSessionEventType.ASSISTANT_DELTA,
        data={"text": "Check the limit screws."},
    )

    assert commit_count == 1
    assert store.session.latest_event_sequence == 1
    assert store.events[0].id == "evt_internal_1"
    assert event.id == "1"
    assert event.sequence == 1


async def test_heartbeat_append_does_not_mutate_unrelated_session_state() -> None:
    store = _EventStore()
    before = {
        "phase": store.session.phase,
        "status": store.session.status,
        "safety_state": store.session.safety_state,
        "current_input_request": store.session.current_input_request,
        "execution_progress": store.session.execution_progress,
        "active_safety_flags": store.session.active_safety_flags,
        "diagnostic_report_id": store.session.diagnostic_report_id,
        "plan_report_id": store.session.plan_report_id,
        "execution_report_id": store.session.execution_report_id,
        "shop_referral_report_id": store.session.shop_referral_report_id,
    }

    event = await EventService(store, store).append_event(
        repair_session_id=store.session.id,
        event_type=RepairSessionEventType.HEARTBEAT,
        data={"ok": True},
    )

    after = {
        "phase": store.session.phase,
        "status": store.session.status,
        "safety_state": store.session.safety_state,
        "current_input_request": store.session.current_input_request,
        "execution_progress": store.session.execution_progress,
        "active_safety_flags": store.session.active_safety_flags,
        "diagnostic_report_id": store.session.diagnostic_report_id,
        "plan_report_id": store.session.plan_report_id,
        "execution_report_id": store.session.execution_report_id,
        "shop_referral_report_id": store.session.shop_referral_report_id,
    }

    assert event.type == RepairSessionEventType.HEARTBEAT
    assert event.turn_id is None
    assert event.data.ok is True
    assert store.session.latest_event_sequence == 1
    assert after == before


async def test_open_sse_stream_yields_event_appended_through_event_service() -> None:
    store = _EventStore()
    commit_count = 0

    async def commit() -> None:
        nonlocal commit_count
        commit_count += 1

    service = EventService(store, store, commit=commit)
    frames = service.stream_sse_frames(
        EventStream(
            repair_session_id=store.session.id,
            after_sequence=store.session.latest_event_sequence,
            timeout_seconds=6,
        ),
    )
    pending_frame = asyncio.create_task(anext(frames))

    await asyncio.sleep(0)
    await service.append_event(
        repair_session_id=store.session.id,
        event_type=RepairSessionEventType.ASSISTANT_DELTA,
        data={"text": "Check the derailleur hanger alignment."},
        turn_id="turn_service",
    )

    frame = await asyncio.wait_for(pending_frame, timeout=1)
    await frames.aclose()

    fields = dict(line.split(": ", maxsplit=1) for line in frame.strip().splitlines())
    payload = json.loads(fields["data"])

    assert commit_count == 1
    assert store.events[0].id == "evt_internal_1"
    assert fields["id"] == "1"
    assert fields["event"] == "assistant.delta"
    assert payload == {
        "id": "1",
        "session_id": store.session.id,
        "turn_id": "turn_service",
        "type": "assistant.delta",
        "sequence": 1,
        "created_at": "2026-06-21T17:00:01Z",
        "data": {"text": "Check the derailleur hanger alignment."},
    }
