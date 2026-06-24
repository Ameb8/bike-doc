"""Safety service tests."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from bike_doc_api.core.errors import SafetyPolicyViolationError, ValidationAppError
from bike_doc_api.models.repair_session import RepairSession as RepairSessionModel
from bike_doc_api.services.safety import SafetyService


def _flag(**overrides: Any) -> dict[str, Any]:
    flag = {
        "code": "uncertain_torque_spec",
        "severity": "warning",
        "phase": "diagnostic",
        "message": "A safety-critical torque value is not known.",
        "blocks_repair_instructions": False,
    }
    flag.update(overrides)
    return flag


def _session(
    *,
    active_safety_flags: list[dict[str, Any]] | None = None,
    safety_state: str = "ok",
) -> RepairSessionModel:
    return RepairSessionModel(
        id="rs_safety",
        user_id="usr_safety",
        bike_id="bike_safety",
        phase="diagnostic",
        status="running",
        safety_state=safety_state,
        current_input_request=None,
        execution_progress=None,
        active_safety_flags=active_safety_flags or [],
        latest_event_sequence=0,
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
        updated_at=datetime(2026, 1, 1, tzinfo=UTC),
    )


@pytest.mark.parametrize(
    ("severity", "expected_state"),
    [
        ("info", "ok"),
        ("caution", "caution"),
        ("warning", "shop_recommended"),
        ("blocking", "blocked"),
    ],
)
def test_severity_derives_safety_state(severity: str, expected_state: str) -> None:
    flag = _flag(
        severity=severity,
        blocks_repair_instructions=severity == "blocking",
    )

    update = SafetyService().raise_safety_flag(
        repair_session=_session(),
        safety_flag=flag,
    )

    assert update.safety_state == expected_state


def test_multiple_severities_use_highest_active_severity() -> None:
    service = SafetyService()
    flags = service.reconcile_active_flags(
        [
            _flag(code="insufficient_evidence_for_safe_guidance", severity="caution"),
            _flag(code="uncertain_torque_spec", severity="warning"),
            _flag(code="contradictory_evidence", severity="info"),
        ],
    )

    assert service.derive_safety_state(flags) == "shop_recommended"


def test_unknown_code_is_rejected() -> None:
    with pytest.raises(ValidationAppError):
        SafetyService().validate_flag(_flag(code="diagnosis_uncertain"))


@pytest.mark.parametrize("severity", ["critical", "", 3])
def test_malformed_or_unsupported_severity_is_rejected(severity: object) -> None:
    with pytest.raises(ValidationAppError):
        SafetyService().validate_flag(_flag(severity=severity))


def test_non_diagnostic_phase_is_rejected() -> None:
    with pytest.raises(ValidationAppError):
        SafetyService().validate_flag(_flag(phase="planning"))


def test_blank_message_is_rejected_after_trimming() -> None:
    with pytest.raises(ValidationAppError):
        SafetyService().validate_flag(_flag(message="  "))


def test_string_fields_are_trimmed_before_validation() -> None:
    flag = SafetyService().validate_flag(
        _flag(
            code=" uncertain_torque_spec ",
            severity=" warning ",
            phase=" diagnostic ",
            message=" Check the manufacturer torque chart. ",
        ),
    )

    assert flag.code == "uncertain_torque_spec"
    assert flag.severity == "warning"
    assert flag.phase == "diagnostic"
    assert flag.message == "Check the manufacturer torque chart."


def test_non_boolean_blocks_repair_instructions_is_rejected() -> None:
    with pytest.raises(ValidationAppError):
        SafetyService().validate_flag(_flag(blocks_repair_instructions="false"))


def test_blocking_without_instruction_block_is_policy_violation() -> None:
    with pytest.raises(SafetyPolicyViolationError):
        SafetyService().validate_flag(
            _flag(severity="blocking", blocks_repair_instructions=False),
        )


@pytest.mark.parametrize("severity", ["info", "caution"])
def test_advisory_flags_do_not_derive_blocked_when_they_block_instructions(
    severity: str,
) -> None:
    update = SafetyService().raise_safety_flag(
        repair_session=_session(),
        safety_flag=_flag(
            severity=severity,
            blocks_repair_instructions=True,
        ),
    )

    assert update.safety_state in {"ok", "caution"}
    assert update.safety_state != "blocked"


def test_duplicate_active_flags_keep_highest_severity() -> None:
    flags = SafetyService().reconcile_active_flags(
        [
            _flag(severity="caution", message="Use care."),
            _flag(severity="warning", message="Use shop-level care."),
        ],
    )

    assert len(flags) == 1
    assert flags[0].severity == "warning"
    assert flags[0].message == "Use shop-level care."


def test_duplicate_active_flags_keep_instruction_block_for_same_severity() -> None:
    flags = SafetyService().reconcile_active_flags(
        [
            _flag(severity="warning", blocks_repair_instructions=False),
            _flag(severity="warning", blocks_repair_instructions=True),
        ],
    )

    assert len(flags) == 1
    assert flags[0].severity == "warning"
    assert flags[0].blocks_repair_instructions is True


def test_report_payload_contradictory_duplicates_are_rejected() -> None:
    with pytest.raises(ValidationAppError):
        SafetyService().validate_report_safety_flags(
            payload_flags=[
                _flag(severity="caution"),
                _flag(severity="warning"),
            ],
            envelope_flags=[
                _flag(severity="caution"),
                _flag(severity="warning"),
            ],
        )


def test_existing_active_flags_are_reconciled_with_report_flags() -> None:
    session = _session(
        active_safety_flags=[
            _flag(
                code="brake_failure_suspected",
                severity="blocking",
                message="Brake may not stop the bike.",
                blocks_repair_instructions=True,
            ),
        ],
        safety_state="blocked",
    )

    update = SafetyService().apply_report_safety_flags(
        repair_session=session,
        report_flags=[
            SafetyService().validate_flag(
                _flag(code="uncertain_torque_spec", severity="warning"),
            ),
        ],
    )

    assert update.safety_state == "blocked"
    assert {flag.code for flag in update.active_safety_flags} == {
        "brake_failure_suspected",
        "uncertain_torque_spec",
    }


def test_later_report_omitting_existing_active_flag_does_not_clear_it() -> None:
    session = _session(
        active_safety_flags=[
            _flag(
                code="brake_failure_suspected",
                severity="blocking",
                message="Brake may not stop the bike.",
                blocks_repair_instructions=True,
            ),
        ],
        safety_state="blocked",
    )

    update = SafetyService().apply_report_safety_flags(
        repair_session=session,
        report_flags=[],
    )

    assert update.safety_state == "blocked"
    assert session.active_safety_flags[0]["code"] == "brake_failure_suspected"


def test_raise_safety_flag_reports_event_when_materially_new_flag_is_added() -> None:
    update = SafetyService().raise_safety_flag(
        repair_session=_session(safety_state="blocked"),
        safety_flag=_flag(severity="info"),
    )

    assert update.emit_safety_escalated is True
    assert update.event_data is not None
    assert update.event_data["safety_state"] == "ok"
    assert update.event_data["blocks_repair_instructions"] is False


def test_blocks_repair_instruction_change_on_warning_reports_event() -> None:
    session = _session(
        active_safety_flags=[_flag(blocks_repair_instructions=False)],
        safety_state="shop_recommended",
    )

    update = SafetyService().raise_safety_flag(
        repair_session=session,
        safety_flag=_flag(blocks_repair_instructions=True),
    )

    assert update.emit_safety_escalated is True
    assert update.event_data is not None
    assert update.event_data["blocks_repair_instructions"] is True
