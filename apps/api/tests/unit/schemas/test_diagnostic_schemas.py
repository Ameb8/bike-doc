"""Diagnostic API schema validation tests."""

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from pydantic import ValidationError

from bike_doc_api.models.artifact import ArtifactRef as ArtifactRefModel
from bike_doc_api.models.event import RepairSessionEvent as RepairSessionEventModel
from bike_doc_api.models.phase_report import PhaseReport as PhaseReportModel
from bike_doc_api.models.repair_session import RepairSession as RepairSessionModel
from bike_doc_api.schemas.artifact import artifact_ref_from_model
from bike_doc_api.schemas.event import (
    RepairSessionEvent,
    RepairSessionEventType,
    repair_session_event_from_model,
)
from bike_doc_api.schemas.repair_session import repair_session_from_model
from bike_doc_api.schemas.report import (
    DiagnosticReportV1,
    PhaseReportEnvelope,
    SafetyFlag,
    phase_report_envelope_from_model,
)
from bike_doc_api.schemas.turn import TurnCreate

NOW = datetime(2026, 6, 21, 17, 0, tzinfo=UTC)


def make_diagnostic_payload() -> dict[str, object]:
    """Return a schema-valid diagnostic report payload."""

    return {
        "schema_version": "diagnostic_report.v1",
        "primary_diagnosis": {
            "component": "rear derailleur",
            "issue": "Cable tension appears low.",
            "confidence": "high",
            "diy_suitability": "reasonable",
        },
        "alternate_hypotheses": [
            {
                "component": "chain",
                "issue": "A dry chain can make shifts feel rough.",
                "confidence": "low",
                "ruled_out_by": "The symptom is gear-specific.",
            },
        ],
        "evidence_summary": "The user reports slow upshifts after cable work.",
        "key_artifact_ids": ["art_123"],
        "user_skill_level": "intermediate",
        "safety_flags": [],
        "diagnostic_session_id": "phs_123",
    }


def test_diagnostic_report_envelope_validates_payload() -> None:
    envelope = PhaseReportEnvelope(
        id="rpt_123",
        repair_session_id="rs_123",
        type="diagnostic",
        schema_version="diagnostic_report.v1",
        phase="diagnostic",
        summary="Cable tension is likely low.",
        safety_flags=[],
        source_artifact_ids=["art_123"],
        created_at=NOW,
        payload=make_diagnostic_payload(),
    )

    assert isinstance(envelope.payload, DiagnosticReportV1)
    assert envelope.payload.schema_version == "diagnostic_report.v1"


def test_blocking_safety_flags_require_instruction_block() -> None:
    with pytest.raises(ValidationError):
        SafetyFlag(
            code="front_brake_failure_suspected",
            severity="blocking",
            phase="diagnostic",
            message="Do not ride the bike.",
            blocks_repair_instructions=False,
        )


def test_turn_create_accepts_openapi_example_shape() -> None:
    turn = TurnCreate.model_validate(
        {
            "schema_version": "ai_turn.v1",
            "client_turn_id": "mobile-turn-001",
            "message": {
                "text": "The chain skips when I pedal hard.",
                "artifact_ids": ["art_123"],
            },
            "responds_to_input_request_id": "req_123",
        }
    )

    assert turn.schema_version == "ai_turn.v1"
    assert turn.message.artifact_ids == ["art_123"]


def test_turn_message_requires_artifact_ids() -> None:
    with pytest.raises(ValidationError):
        TurnCreate.model_validate(
            {
                "schema_version": "ai_turn.v1",
                "client_turn_id": "mobile-turn-001",
                "message": {"text": "The chain skips when I pedal hard."},
            }
        )


def test_artifact_reference_maps_public_fields_from_orm_model() -> None:
    artifact = ArtifactRefModel(
        id="art_123",
        user_id="usr_123",
        repair_session_id="rs_123",
        bike_id=None,
        purpose="diagnostic_photo",
        media_type="image",
        mime_type="image/jpeg",
        filename="derailleur.jpg",
        byte_size=12345,
        width=1024,
        height=768,
        duration_seconds=Decimal("1.250"),
        status="ready",
        rejection_reason=None,
        content_sha256="a" * 64,
        storage_provider="gcs",
        storage_bucket="private-bucket",
        storage_path="private/path/derailleur.jpg",
        created_at=NOW,
        updated_at=NOW,
    )

    public = artifact_ref_from_model(artifact)

    assert public.id == "art_123"
    assert public.duration_seconds == 1.25
    assert "storage_path" not in public.model_dump()
    assert "content_sha256" not in public.model_dump()


def test_repair_session_mapper_sets_latest_event_id_from_sequence() -> None:
    repair_session = RepairSessionModel(
        id="rs_123",
        user_id="usr_123",
        bike_id="bike_123",
        phase="diagnostic",
        status="awaiting_user",
        safety_state="ok",
        current_input_request=None,
        execution_progress=None,
        latest_event_sequence=42,
        diagnostic_report_id="rpt_diagnostic",
        plan_report_id=None,
        execution_report_id=None,
        shop_referral_report_id=None,
        created_at=NOW,
        updated_at=NOW,
    )

    public = repair_session_from_model(repair_session)

    assert public.latest_event_id == "42"
    assert public.latest_reports.diagnostic_report_id == "rpt_diagnostic"


def test_event_mapper_exposes_sequence_as_public_id() -> None:
    event = RepairSessionEventModel(
        id="evt_internal",
        repair_session_id="rs_123",
        turn_id="turn_123",
        sequence=7,
        type="assistant.delta",
        data={"text": "Check the rear derailleur."},
        created_at=NOW,
    )

    public = repair_session_event_from_model(event)

    assert public.id == "7"
    assert public.sequence == 7
    assert public.session_id == "rs_123"


def test_invalid_event_data_for_event_type_fails_validation() -> None:
    with pytest.raises(ValidationError):
        RepairSessionEvent(
            id="2",
            session_id="rs_123",
            turn_id="turn_123",
            type=RepairSessionEventType.ASSISTANT_DELTA,
            sequence=2,
            created_at=NOW,
            data={"turn_id": "turn_123", "phase": "diagnostic"},
        )


def test_phase_report_mapper_validates_diagnostic_payload() -> None:
    report = PhaseReportModel(
        id="rpt_123",
        repair_session_id="rs_123",
        repair_phase_session_id="phs_123",
        type="diagnostic",
        schema_version="diagnostic_report.v1",
        phase="diagnostic",
        summary="Cable tension is likely low.",
        safety_flags=[],
        source_artifact_ids=["art_123"],
        payload=make_diagnostic_payload(),
        created_at=NOW,
    )

    public = phase_report_envelope_from_model(report)

    assert isinstance(public.payload, DiagnosticReportV1)
    assert public.payload.diagnostic_session_id == "phs_123"
