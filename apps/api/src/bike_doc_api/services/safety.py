"""Diagnostic safety invariant service boundary."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError as PydanticValidationError

from bike_doc_api.core.errors import SafetyPolicyViolationError, ValidationAppError
from bike_doc_api.models.repair_session import RepairSession as RepairSessionModel
from bike_doc_api.schemas.common import RepairSessionPhase, SafetySeverity, SafetyState
from bike_doc_api.schemas.report import SafetyFlag

DIAGNOSTIC_V1_SAFETY_CODES = frozenset(
    {
        "frame_or_fork_damage_suspected",
        "brake_failure_suspected",
        "carbon_damage_suspected",
        "ebike_electrical_concern",
        "suspension_internal_concern",
        "safety_critical_fastener_damaged",
        "uncertain_torque_spec",
        "contradictory_evidence",
        "insufficient_evidence_for_safe_guidance",
        "unsafe_riding_condition",
    }
)

_SEVERITY_RANK: dict[SafetySeverity, int] = {
    SafetySeverity.INFO: 0,
    SafetySeverity.CAUTION: 1,
    SafetySeverity.WARNING: 2,
    SafetySeverity.BLOCKING: 3,
}


@dataclass(frozen=True, slots=True)
class ReportSafetyFlags:
    """Validated diagnostic report safety flags."""

    payload_flags: list[SafetyFlag]
    envelope_flags: list[SafetyFlag]


@dataclass(frozen=True, slots=True)
class SafetySessionUpdate:
    """Result of reconciling active safety flags onto a repair session."""

    previous_safety_state: str
    safety_state: str
    active_safety_flags: list[SafetyFlag]
    emit_safety_escalated: bool
    event_data: dict[str, Any] | None


class SafetyService:
    """Diagnostic V1 safety validation, reconciliation, and state derivation."""

    def validate_flag(self, flag: Any) -> SafetyFlag:
        """Validate and normalize one diagnostic V1 safety flag."""

        raw = _coerce_flag_mapping(flag)
        normalized = _normalize_flag_mapping(raw)
        _validate_flag_policy(normalized)
        try:
            return SafetyFlag.model_validate(normalized)
        except (PydanticValidationError, ValueError) as exc:
            raise ValidationAppError() from exc

    def validate_report_safety_flags(
        self,
        *,
        payload_flags: Sequence[Any],
        envelope_flags: Sequence[Any],
    ) -> ReportSafetyFlags:
        """Validate diagnostic report safety flags and envelope/payload equality."""

        validated_payload = self.validate_flags(
            payload_flags,
            reject_contradictory_duplicates=True,
        )
        validated_envelope = self.validate_flags(
            envelope_flags,
            reject_contradictory_duplicates=True,
        )
        if _dump_flags(validated_payload) != _dump_flags(validated_envelope):
            raise ValidationAppError()
        return ReportSafetyFlags(
            payload_flags=validated_payload,
            envelope_flags=validated_envelope,
        )

    def validate_flags(
        self,
        flags: Sequence[Any],
        *,
        reject_contradictory_duplicates: bool = False,
    ) -> list[SafetyFlag]:
        """Validate and normalize diagnostic V1 safety flags."""

        validated = [self.validate_flag(flag) for flag in flags]
        if reject_contradictory_duplicates:
            _reject_contradictory_duplicates(validated)
        return validated

    def derive_safety_state(self, active_safety_flags: list[SafetyFlag]) -> str:
        """Derive repair_sessions.safety_state from active safety flags."""

        if any(
            flag.severity is SafetySeverity.BLOCKING for flag in active_safety_flags
        ):
            return SafetyState.BLOCKED.value
        if any(flag.severity is SafetySeverity.WARNING for flag in active_safety_flags):
            return SafetyState.SHOP_RECOMMENDED.value
        if any(flag.severity is SafetySeverity.CAUTION for flag in active_safety_flags):
            return SafetyState.CAUTION.value
        return SafetyState.OK.value

    def reconcile_active_flags(
        self,
        flags: Sequence[Any],
    ) -> list[SafetyFlag]:
        """Validate and deduplicate active flags by diagnostic `(code, phase)`."""

        return _deduplicate_active_flags(self.validate_flags(flags))

    def apply_report_safety_flags(
        self,
        *,
        repair_session: RepairSessionModel,
        report_flags: list[SafetyFlag],
    ) -> SafetySessionUpdate:
        """Reconcile report flags with existing active flags and update the session."""

        existing_flags = self.validate_flags(repair_session.active_safety_flags)
        return self._apply_active_flags(
            repair_session=repair_session,
            proposed_flags=[*existing_flags, *report_flags],
        )

    def raise_safety_flag(
        self,
        *,
        repair_session: RepairSessionModel,
        safety_flag: Any,
    ) -> SafetySessionUpdate:
        """Validate and apply one newly raised diagnostic safety flag."""

        new_flag = self.validate_flag(safety_flag)
        existing_flags = self.validate_flags(repair_session.active_safety_flags)
        return self._apply_active_flags(
            repair_session=repair_session,
            proposed_flags=[*existing_flags, new_flag],
        )

    def safety_escalated_event_data(
        self,
        *,
        safety_state: str,
        active_safety_flags: list[SafetyFlag],
    ) -> dict[str, Any]:
        """Build public safety.escalated event data."""

        serialized_flags = _dump_flags(active_safety_flags)
        return {
            "safety_state": safety_state,
            "safety_flags": serialized_flags,
            "user_message": _safety_event_message(serialized_flags),
            "blocks_repair_instructions": any(
                flag.blocks_repair_instructions for flag in active_safety_flags
            ),
        }

    def _apply_active_flags(
        self,
        *,
        repair_session: RepairSessionModel,
        proposed_flags: list[SafetyFlag],
    ) -> SafetySessionUpdate:
        """Apply reconciled active flags and derived state to a session model."""

        previous_flags = self.reconcile_active_flags(repair_session.active_safety_flags)
        previous_state = repair_session.safety_state
        active_flags = _deduplicate_active_flags(proposed_flags)
        safety_state = self.derive_safety_state(active_flags)
        should_emit_event = _should_emit_safety_escalated(
            previous_flags=previous_flags,
            previous_state=previous_state,
            active_flags=active_flags,
            safety_state=safety_state,
        )
        repair_session.active_safety_flags = _dump_flags(active_flags)
        repair_session.safety_state = safety_state
        event_data = (
            self.safety_escalated_event_data(
                safety_state=safety_state,
                active_safety_flags=active_flags,
            )
            if should_emit_event
            else None
        )
        return SafetySessionUpdate(
            previous_safety_state=previous_state,
            safety_state=safety_state,
            active_safety_flags=active_flags,
            emit_safety_escalated=should_emit_event,
            event_data=event_data,
        )


def _coerce_flag_mapping(flag: Any) -> Mapping[str, Any]:
    """Return raw flag data from a public model or dict-like model output."""

    if isinstance(flag, SafetyFlag):
        return flag.model_dump(mode="json")
    if isinstance(flag, Mapping):
        return flag
    model_dump = getattr(flag, "model_dump", None)
    if callable(model_dump):
        dumped = model_dump(mode="json")
        if isinstance(dumped, Mapping):
            return dumped
    raise ValidationAppError()


def _normalize_flag_mapping(raw: Mapping[str, Any]) -> dict[str, Any]:
    """Trim string fields before diagnostic V1 validation."""

    required_fields = {
        "code",
        "severity",
        "phase",
        "message",
        "blocks_repair_instructions",
    }
    if any(field not in raw for field in required_fields):
        raise ValidationAppError()

    normalized: dict[str, Any] = {}
    for field in ("code", "severity", "phase", "message"):
        value = raw[field]
        if not isinstance(value, str):
            raise ValidationAppError()
        normalized[field] = value.strip()
    blocks_repair_instructions = raw["blocks_repair_instructions"]
    if type(blocks_repair_instructions) is not bool:
        raise ValidationAppError()
    normalized["blocks_repair_instructions"] = blocks_repair_instructions
    return normalized


def _validate_flag_policy(normalized: Mapping[str, Any]) -> None:
    """Apply diagnostic V1 safety policy before Pydantic enum validation."""

    if normalized["code"] not in DIAGNOSTIC_V1_SAFETY_CODES:
        raise ValidationAppError()
    if normalized["severity"] not in {severity.value for severity in SafetySeverity}:
        raise ValidationAppError()
    if normalized["phase"] != RepairSessionPhase.DIAGNOSTIC.value:
        raise ValidationAppError()
    if not normalized["message"]:
        raise ValidationAppError()
    if (
        normalized["severity"] == SafetySeverity.BLOCKING.value
        and not normalized["blocks_repair_instructions"]
    ):
        raise SafetyPolicyViolationError()


def _reject_contradictory_duplicates(flags: list[SafetyFlag]) -> None:
    """Reject one report payload containing one key at multiple severities."""

    seen: dict[tuple[str, str], SafetySeverity] = {}
    for flag in flags:
        key = (flag.code, flag.phase.value)
        previous = seen.get(key)
        if previous is not None and previous is not flag.severity:
            raise ValidationAppError()
        seen[key] = flag.severity


def _deduplicate_active_flags(flags: list[SafetyFlag]) -> list[SafetyFlag]:
    """Deduplicate active flags by `(code, phase)`, keeping highest severity."""

    deduplicated: dict[tuple[str, str], SafetyFlag] = {}
    for flag in flags:
        key = (flag.code, flag.phase.value)
        current = deduplicated.get(key)
        if current is None:
            deduplicated[key] = flag
            continue

        current_rank = _SEVERITY_RANK[current.severity]
        candidate_rank = _SEVERITY_RANK[flag.severity]
        if candidate_rank > current_rank:
            deduplicated[key] = flag
            continue
        if candidate_rank == current_rank and (
            flag.blocks_repair_instructions and not current.blocks_repair_instructions
        ):
            deduplicated[key] = current.model_copy(
                update={"blocks_repair_instructions": True},
            )
    return list(deduplicated.values())


def _should_emit_safety_escalated(
    *,
    previous_flags: list[SafetyFlag],
    previous_state: str,
    active_flags: list[SafetyFlag],
    safety_state: str,
) -> bool:
    """Return whether this active-flag change requires a safety event."""

    if previous_state != safety_state:
        return True

    previous_by_key = {(flag.code, flag.phase.value): flag for flag in previous_flags}
    for flag in active_flags:
        previous = previous_by_key.get((flag.code, flag.phase.value))
        if previous is None:
            return True
        if _SEVERITY_RANK[flag.severity] > _SEVERITY_RANK[previous.severity]:
            return True
        if (
            flag.severity in {SafetySeverity.WARNING, SafetySeverity.BLOCKING}
            and flag.blocks_repair_instructions
            and not previous.blocks_repair_instructions
        ):
            return True
    return False


def _dump_flags(flags: list[SafetyFlag]) -> list[dict[str, Any]]:
    """Return public safety flag JSON."""

    return [flag.model_dump(mode="json") for flag in flags]


def _safety_event_message(serialized_flags: list[dict[str, Any]]) -> str:
    """Return a public safety event message without exposing internals."""

    if serialized_flags:
        return str(serialized_flags[0]["message"])
    return "Safety state updated."
