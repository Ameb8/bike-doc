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
    PlanReportV1,
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
        "repair_estimate": {
            "difficulty": "easy",
            "difficulty_notes": "Basic barrel-adjuster tuning is common at home.",
            "tools_required": ["bike stand or safe way to lift rear wheel"],
            "parts_required": [],
            "repair_time": {"low_minutes": 10, "high_minutes": 25},
            "shop_repair_cost": {
                "low_usd": 20,
                "high_usd": 50,
                "notes": "Most shops would treat this as a minor adjustment.",
            },
        },
        "key_artifact_ids": ["art_123"],
        "user_skill_level": "intermediate",
        "safety_flags": [],
        "diagnostic_session_id": "phs_123",
    }


def make_plan_payload() -> dict[str, object]:
    """Return a schema-valid plan report payload with cost estimates."""

    return {
        "schema_version": "plan_report.v1",
        "diagnosis_summary": "The chain is likely worn.",
        "parts_needed": [
            {
                "item": "Shimano HG54 10-speed chain",
                "specification": "10-speed",
                "quantity": 1,
                "required": True,
                "estimated_price": {
                    "currency": "USD",
                    "min_amount": 27.99,
                    "max_amount": 27.99,
                    "confidence": "high",
                    "source": "search_provider",
                },
                "price_lookup": _price_lookup_result("part"),
            },
        ],
        "tools_needed": [],
        "diy_estimate": {
            "currency": "USD",
            "min_amount": 27.99,
            "max_amount": 27.99,
            "confidence": "high",
            "source": "search_provider",
        },
        "shop_estimate": {
            "currency": "USD",
            "min_amount": 40,
            "max_amount": 80,
            "confidence": "medium",
            "source": "labor_reference_table",
        },
        "cost_estimate": {
            "parts_total": {
                "currency": "USD",
                "min_amount": 27.99,
                "max_amount": 27.99,
                "confidence": "high",
                "source": "search_provider",
            },
            "tools_total": {
                "currency": "USD",
                "min_amount": 0,
                "max_amount": 0,
                "confidence": "high",
                "source": "search_provider",
            },
            "diy_total": {
                "currency": "USD",
                "min_amount": 27.99,
                "max_amount": 27.99,
                "confidence": "high",
                "source": "search_provider",
            },
            "items": [_price_lookup_result("part")],
        },
        "recommendation": "diy_reasonable",
        "recommendation_basis": "A chain replacement is reasonable at home.",
        "requires_user_decision": True,
        "safety_concerns": [],
    }


def _price_lookup_result(item_type: str) -> dict[str, object]:
    """Return a schema-valid price lookup result."""

    return {
        "item_type": item_type,
        "requirement_name": "Shimano HG54 10-speed chain",
        "quantity": 1,
        "status": "priced_listing_found",
        "estimate_confidence": "high",
        "looked_up_at": NOW.isoformat(),
        "primary_listing": {
            "title": "Shimano HG54 10-Speed Chain",
            "retailer": "Example Retailer",
            "observed_price": 27.99,
            "currency": "USD",
            "url": "https://example.com/chain",
            "observed_at": NOW.isoformat(),
            "match_confidence": "high",
            "match_rationale": "Listing title matches model and speed.",
        },
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


def test_plan_report_envelope_validates_cost_estimate_payload() -> None:
    envelope = PhaseReportEnvelope(
        id="rpt_plan",
        repair_session_id="rs_123",
        type="plan",
        schema_version="plan_report.v1",
        phase="planning",
        summary="Replace the worn chain.",
        safety_flags=[],
        source_artifact_ids=[],
        created_at=NOW,
        payload=make_plan_payload(),
    )

    assert isinstance(envelope.payload, PlanReportV1)
    assert envelope.payload.cost_estimate is not None
    assert envelope.payload.cost_estimate.items[0].primary_listing is not None


def test_plan_report_envelope_requires_matching_safety_concerns() -> None:
    with pytest.raises(ValidationError):
        PhaseReportEnvelope(
            id="rpt_plan",
            repair_session_id="rs_123",
            type="plan",
            schema_version="plan_report.v1",
            phase="planning",
            summary="Replace the worn chain.",
            safety_flags=[
                {
                    "code": "uncertain_torque_spec",
                    "severity": "warning",
                    "phase": "planning",
                    "message": "Torque value still needs verification.",
                    "blocks_repair_instructions": False,
                },
            ],
            source_artifact_ids=[],
            created_at=NOW,
            payload=make_plan_payload(),
        )


def test_blocking_safety_flags_require_instruction_block() -> None:
    with pytest.raises(ValidationError):
        SafetyFlag(
            code="brake_failure_suspected",
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


@pytest.mark.parametrize(
    "artifact_ids",
    [[], ["art_1"], ["art_1", "art_2"], ["art_1", "art_2", "art_3"]],
)
def test_turn_create_accepts_up_to_three_distinct_artifact_ids(
    artifact_ids: list[str],
) -> None:
    turn = TurnCreate.model_validate(
        {
            "schema_version": "ai_turn.v1",
            "client_turn_id": "mobile-turn-artifact-limit",
            "message": {
                "text": "Please inspect these photos.",
                "artifact_ids": artifact_ids,
            },
        }
    )

    assert turn.message.artifact_ids == artifact_ids


@pytest.mark.parametrize(
    "artifact_ids",
    [
        ["art_1", "art_2", "art_3", "art_4"],
        ["art_1", "art_1"],
    ],
)
def test_turn_create_rejects_oversized_or_duplicate_artifact_ids(
    artifact_ids: list[str],
) -> None:
    with pytest.raises(ValidationError):
        TurnCreate.model_validate(
            {
                "schema_version": "ai_turn.v1",
                "client_turn_id": "mobile-turn-invalid-artifacts",
                "message": {
                    "text": "Please inspect these photos.",
                    "artifact_ids": artifact_ids,
                },
            }
        )


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
