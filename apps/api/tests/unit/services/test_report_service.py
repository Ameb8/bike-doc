"""Report service tests."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from bike_doc_api.core.errors import NotFoundError, ValidationAppError
from bike_doc_api.models.artifact import ArtifactRef as ArtifactRefModel
from bike_doc_api.models.event import RepairSessionEvent as RepairSessionEventModel
from bike_doc_api.models.phase_report import PhaseReport as PhaseReportModel
from bike_doc_api.models.repair_session import (
    RepairPhaseSession as RepairPhaseSessionModel,
)
from bike_doc_api.models.repair_session import RepairSession as RepairSessionModel
from bike_doc_api.models.user import User
from bike_doc_api.services.reports import ReportService
from bike_doc_api.services.safety import SafetyService

OWNED_SESSION_ID = "rs_report_service"
WRONG_SESSION_ID = "rs_wrong_report_service"
OWNED_ARTIFACT_ID = "art_report_service"
OTHER_ARTIFACT_ID = "art_other_report_service"
WRONG_SESSION_ARTIFACT_ID = "art_wrong_session_report_service"
PHASE_SESSION_ID = "phs_report_service"


class _ReportStore:
    """In-memory repository double for report service behavior."""

    def __init__(self) -> None:
        self.session = _session(OWNED_SESSION_ID, "usr_report")
        self.wrong_session = _session(WRONG_SESSION_ID, "usr_report")
        self.phase_session = RepairPhaseSessionModel(
            id=PHASE_SESSION_ID,
            repair_session_id=OWNED_SESSION_ID,
            phase="diagnostic",
            adk_session_id="opaque-internal",
            status="active",
            created_at=datetime(2026, 1, 1, tzinfo=UTC),
        )
        self.reports: list[PhaseReportModel] = []
        self.events: list[RepairSessionEventModel] = []
        self.artifacts = {
            OWNED_ARTIFACT_ID: _artifact(
                artifact_id=OWNED_ARTIFACT_ID,
                user_id="usr_report",
                repair_session_id=OWNED_SESSION_ID,
            ),
            OTHER_ARTIFACT_ID: _artifact(
                artifact_id=OTHER_ARTIFACT_ID,
                user_id="usr_other",
                repair_session_id=OWNED_SESSION_ID,
            ),
            WRONG_SESSION_ARTIFACT_ID: _artifact(
                artifact_id=WRONG_SESSION_ARTIFACT_ID,
                user_id="usr_report",
                repair_session_id=WRONG_SESSION_ID,
            ),
        }

    async def get_owned(
        self,
        *,
        repair_session_id: str | None = None,
        user_id: str,
        artifact_id: str | None = None,
    ) -> RepairSessionModel | ArtifactRefModel | None:
        if artifact_id is not None:
            artifact = self.artifacts.get(artifact_id)
            if artifact is None or artifact.user_id != user_id:
                return None
            return artifact
        if repair_session_id == self.session.id and user_id == self.session.user_id:
            return self.session
        if (
            repair_session_id == self.wrong_session.id
            and user_id == self.wrong_session.user_id
        ):
            return self.wrong_session
        return None

    async def get_owned_for_update(
        self,
        *,
        repair_session_id: str,
        user_id: str,
    ) -> RepairSessionModel | None:
        result = await self.get_owned(
            repair_session_id=repair_session_id,
            user_id=user_id,
        )
        return result if isinstance(result, RepairSessionModel) else None

    async def get(
        self,
        phase_session_id: str,
    ) -> RepairPhaseSessionModel | None:
        if phase_session_id == self.phase_session.id:
            return self.phase_session
        return None

    async def add(
        self,
        model: PhaseReportModel | RepairSessionEventModel,
    ) -> PhaseReportModel | RepairSessionEventModel:
        if isinstance(model, PhaseReportModel):
            if model.id is None:
                model.id = f"rpt_{len(self.reports) + 1}"
            model.created_at = datetime(
                2026,
                6,
                21,
                17,
                0,
                len(self.reports),
                tzinfo=UTC,
            )
            self.reports.append(model)
            return model
        if model.id is None:
            model.id = f"evt_{len(self.events) + 1}"
        model.created_at = datetime(2026, 6, 21, 17, 0, model.sequence, tzinfo=UTC)
        self.events.append(model)
        return model

    async def get_for_session(
        self,
        *,
        repair_session_id: str,
        report_id: str,
    ) -> PhaseReportModel | None:
        for report in self.reports:
            if report.id == report_id and report.repair_session_id == repair_session_id:
                return report
        return None

    async def list_for_session(
        self,
        repair_session_id: str,
        *,
        report_type: str | None = None,
        limit: int = 50,
        cursor_report: PhaseReportModel | None = None,
    ) -> list[PhaseReportModel]:
        reports = [
            report
            for report in self.reports
            if report.repair_session_id == repair_session_id
            and (report_type is None or report.type == report_type)
        ]
        return reports[:limit]


class _SpySafetyService(SafetyService):
    """Safety service double that records report-service delegation."""

    def __init__(self) -> None:
        super().__init__()
        self.validated_report_flags = False
        self.applied_report_flags = False

    def validate_report_safety_flags(self, **kwargs: Any) -> Any:
        self.validated_report_flags = True
        return super().validate_report_safety_flags(**kwargs)

    def apply_report_safety_flags(self, **kwargs: Any) -> Any:
        self.applied_report_flags = True
        return super().apply_report_safety_flags(**kwargs)


def _service(
    store: _ReportStore,
    *,
    safety: SafetyService | None = None,
) -> ReportService:
    return ReportService(store, store, store, store, store, safety=safety)


def _user(user_id: str = "usr_report") -> User:
    return User(
        id=user_id,
        auth_subject=f"auth|{user_id}",
        email=f"{user_id}@example.com",
        display_name=user_id,
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
    )


def _session(session_id: str, user_id: str) -> RepairSessionModel:
    return RepairSessionModel(
        id=session_id,
        user_id=user_id,
        bike_id="bike_report",
        phase="diagnostic",
        status="running",
        safety_state="ok",
        current_input_request=None,
        execution_progress=None,
        active_safety_flags=[],
        latest_event_sequence=0,
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
        updated_at=datetime(2026, 1, 1, tzinfo=UTC),
    )


def _artifact(
    *,
    artifact_id: str,
    user_id: str,
    repair_session_id: str,
) -> ArtifactRefModel:
    return ArtifactRefModel(
        id=artifact_id,
        user_id=user_id,
        repair_session_id=repair_session_id,
        purpose="diagnostic_photo",
        media_type="image",
        mime_type="image/jpeg",
        filename=f"{artifact_id}.jpg",
        byte_size=123,
        status="ready",
        content_sha256="a" * 64,
        storage_provider="local",
        storage_path=f"objects/{artifact_id}.jpg",
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
        updated_at=datetime(2026, 1, 1, tzinfo=UTC),
    )


def _payload(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "schema_version": "diagnostic_report.v1",
        "primary_diagnosis": {
            "component": "rear derailleur",
            "issue": "Cable tension appears low.",
            "confidence": "medium",
            "diy_suitability": "reasonable",
        },
        "alternate_hypotheses": [],
        "evidence_summary": "The symptom pattern points to rear indexing.",
        "key_artifact_ids": [OWNED_ARTIFACT_ID],
        "user_skill_level": "beginner",
        "safety_flags": [],
        "diagnostic_session_id": PHASE_SESSION_ID,
    }
    payload.update(overrides)
    return payload


async def _persist(
    service: ReportService,
    *,
    payload: dict[str, Any] | None = None,
    safety_flags: list[dict[str, Any]] | None = None,
    source_artifact_ids: list[str] | None = None,
) -> Any:
    return await service.persist_diagnostic_report(
        current_user=_user(),
        repair_session_id=OWNED_SESSION_ID,
        summary="Cable tension likely needs adjustment.",
        payload=payload if payload is not None else _payload(),
        safety_flags=safety_flags if safety_flags is not None else [],
        source_artifact_ids=(
            source_artifact_ids
            if source_artifact_ids is not None
            else [OWNED_ARTIFACT_ID]
        ),
    )


async def test_service_persists_valid_diagnostic_report_without_adk() -> None:
    store = _ReportStore()
    report = await _persist(_service(store))

    assert report.id == "rpt_1"
    assert report.payload.diagnostic_session_id == PHASE_SESSION_ID
    assert store.session.diagnostic_report_id == "rpt_1"
    assert store.session.status == "awaiting_decision"
    assert [event.type for event in store.events] == ["phase.report.created"]
    assert store.events[0].sequence == 1
    assert store.session.latest_event_sequence == 1


async def test_list_and_get_reports_return_public_envelopes() -> None:
    store = _ReportStore()
    service = _service(store)
    created = await _persist(service)

    listed = await service.list_reports(
        current_user=_user(),
        repair_session_id=OWNED_SESSION_ID,
    )
    fetched = await service.get_report(
        current_user=_user(),
        repair_session_id=OWNED_SESSION_ID,
        report_id=created.id,
    )

    assert [report.id for report in listed.items] == [created.id]
    assert fetched.id == created.id
    assert "adk" not in fetched.model_dump_json().lower()


async def test_unknown_or_not_owned_session_returns_not_found() -> None:
    store = _ReportStore()

    with pytest.raises(NotFoundError):
        await _service(store).list_reports(
            current_user=_user(),
            repair_session_id="rs_missing",
        )


async def test_mismatched_payload_and_envelope_safety_flags_are_rejected() -> None:
    store = _ReportStore()
    safety_flag = {
        "code": "insufficient_evidence_for_safe_guidance",
        "severity": "caution",
        "phase": "diagnostic",
        "message": "More evidence is needed.",
        "blocks_repair_instructions": False,
    }

    with pytest.raises(ValidationAppError):
        await _persist(
            _service(store),
            payload=_payload(safety_flags=[safety_flag]),
            safety_flags=[],
        )


async def test_non_owned_or_wrong_session_artifacts_are_rejected() -> None:
    for artifact_id in [OTHER_ARTIFACT_ID, WRONG_SESSION_ARTIFACT_ID]:
        store = _ReportStore()
        with pytest.raises(NotFoundError):
            await _persist(
                _service(store),
                source_artifact_ids=[artifact_id],
                payload=_payload(key_artifact_ids=[artifact_id]),
            )


async def test_invalid_diagnostic_session_id_is_rejected() -> None:
    store = _ReportStore()

    with pytest.raises(ValidationAppError):
        await _persist(
            _service(store),
            payload=_payload(diagnostic_session_id="phs_missing"),
        )


async def test_blocking_safety_report_updates_safety_state_and_event_order() -> None:
    store = _ReportStore()
    safety_flag = {
        "code": "brake_failure_suspected",
        "severity": "blocking",
        "phase": "diagnostic",
        "message": "Do not ride until a mechanic inspects the brake.",
        "blocks_repair_instructions": True,
    }

    await _persist(
        _service(store),
        payload=_payload(safety_flags=[safety_flag]),
        safety_flags=[safety_flag],
    )

    assert store.session.safety_state == "blocked"
    assert store.session.status == "blocked_safety"
    assert [event.type for event in store.events] == [
        "safety.escalated",
        "phase.report.created",
    ]


async def test_report_service_delegates_safety_behavior_to_safety_service() -> None:
    store = _ReportStore()
    safety = _SpySafetyService()

    await _persist(_service(store, safety=safety))

    assert safety.validated_report_flags is True
    assert safety.applied_report_flags is True


async def test_warning_report_sets_shop_recommended_without_blocking_status() -> None:
    store = _ReportStore()
    safety_flag = {
        "code": "uncertain_torque_spec",
        "severity": "warning",
        "phase": "diagnostic",
        "message": "A safety-critical torque value is not known.",
        "blocks_repair_instructions": False,
    }

    await _persist(
        _service(store),
        payload=_payload(safety_flags=[safety_flag]),
        safety_flags=[safety_flag],
    )

    assert store.session.safety_state == "shop_recommended"
    assert store.session.status == "awaiting_decision"


async def test_report_owned_flags_remain_unchanged_when_active_flags_reconcile() -> (
    None
):
    store = _ReportStore()
    store.session.active_safety_flags = [
        {
            "code": "brake_failure_suspected",
            "severity": "blocking",
            "phase": "diagnostic",
            "message": "Brake may not stop the bike.",
            "blocks_repair_instructions": True,
        },
    ]
    store.session.safety_state = "blocked"
    report_flag = {
        "code": "uncertain_torque_spec",
        "severity": "warning",
        "phase": "diagnostic",
        "message": "A safety-critical torque value is not known.",
        "blocks_repair_instructions": False,
    }

    await _persist(
        _service(store),
        payload=_payload(safety_flags=[report_flag]),
        safety_flags=[report_flag],
    )

    assert store.reports[0].safety_flags == [report_flag]
    assert store.reports[0].payload["safety_flags"] == [report_flag]
    assert {flag["code"] for flag in store.session.active_safety_flags} == {
        "brake_failure_suspected",
        "uncertain_torque_spec",
    }


async def test_later_report_omitting_existing_active_flag_does_not_clear_it() -> None:
    store = _ReportStore()
    store.session.active_safety_flags = [
        {
            "code": "brake_failure_suspected",
            "severity": "blocking",
            "phase": "diagnostic",
            "message": "Brake may not stop the bike.",
            "blocks_repair_instructions": True,
        },
    ]
    store.session.safety_state = "blocked"

    await _persist(_service(store))

    assert store.session.safety_state == "blocked"
    assert store.session.active_safety_flags == [
        {
            "code": "brake_failure_suspected",
            "severity": "blocking",
            "phase": "diagnostic",
            "message": "Brake may not stop the bike.",
            "blocks_repair_instructions": True,
        },
    ]


async def test_invalid_safety_flags_fail_before_persistence() -> None:
    store = _ReportStore()
    safety_flag = {
        "code": "older_freeform_code",
        "severity": "warning",
        "phase": "diagnostic",
        "message": "This should not persist.",
        "blocks_repair_instructions": False,
    }

    with pytest.raises(ValidationAppError):
        await _persist(
            _service(store),
            payload=_payload(safety_flags=[safety_flag]),
            safety_flags=[safety_flag],
        )

    assert store.reports == []
    assert store.events == []
