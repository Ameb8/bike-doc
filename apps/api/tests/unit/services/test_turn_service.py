"""Turn acceptance tests at the application-service seam."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from bike_doc_api.core.errors import (
    ArtifactNotReadyError,
    NotFoundError,
    ValidationAppError,
)
from bike_doc_api.models.artifact import ArtifactRef
from bike_doc_api.models.event import RepairSessionEvent
from bike_doc_api.models.repair_session import (
    RepairPhaseSession,
    RepairSession,
    RepairTurn,
)
from bike_doc_api.models.user import User
from bike_doc_api.schemas.turn import TurnCreate
from bike_doc_api.services.turns import TurnService


class _TurnRepositories:
    """Repository fake exposing accepted turn outcomes at the service boundary."""

    def __init__(self) -> None:
        self.session = RepairSession(
            id="rs_turn_service",
            user_id="usr_turn_service",
            bike_id="bike_turn_service",
            phase="diagnostic",
            status="created",
            safety_state="ok",
            current_input_request=None,
            execution_progress=None,
            active_safety_flags=[],
            latest_event_sequence=0,
            created_at=datetime(2026, 1, 1, tzinfo=UTC),
            updated_at=datetime(2026, 1, 1, tzinfo=UTC),
        )
        self.artifacts: dict[str, ArtifactRef] = {"art_ready": _artifact("art_ready")}
        self.lookups: list[str] = []
        self.turns: list[RepairTurn] = []
        self.events: list[RepairSessionEvent] = []

    async def get_owned(
        self,
        *,
        user_id: str,
        repair_session_id: str | None = None,
        artifact_id: str | None = None,
    ) -> RepairSession | ArtifactRef | None:
        if repair_session_id is not None:
            if repair_session_id == self.session.id and user_id == self.session.user_id:
                return self.session
            return None
        if artifact_id is not None:
            self.lookups.append(artifact_id)
            artifact = self.artifacts.get(artifact_id)
            if artifact is not None and artifact.user_id == user_id:
                return artifact
        return None

    async def get_owned_for_update(
        self,
        *,
        repair_session_id: str,
        user_id: str,
    ) -> RepairSession | None:
        result = await self.get_owned(
            repair_session_id=repair_session_id,
            user_id=user_id,
        )
        return result if isinstance(result, RepairSession) else None

    async def get_by_client_turn_id(
        self,
        *,
        repair_session_id: str,
        client_turn_id: str,
    ) -> RepairTurn | None:
        return next(
            (
                turn
                for turn in self.turns
                if turn.repair_session_id == repair_session_id
                and turn.client_turn_id == client_turn_id
            ),
            None,
        )

    async def add(
        self,
        model: RepairTurn | RepairSessionEvent,
    ) -> RepairTurn | RepairSessionEvent:
        if isinstance(model, RepairTurn):
            model.id = f"turn_{len(self.turns) + 1}"
            self.turns.append(model)
        else:
            model.id = f"evt_{len(self.events) + 1}"
            self.events.append(model)
        return model


class _PhaseSessionManager:
    async def ensure_diagnostic_session(
        self,
        *,
        repair_session_id: str,
    ) -> RepairPhaseSession:
        return RepairPhaseSession(
            id="phs_turn_service",
            repair_session_id=repair_session_id,
            phase="diagnostic",
            adk_session_id="internal-test-session",
            status="active",
            created_at=datetime(2026, 1, 1, tzinfo=UTC),
        )


class _OrchestratorSpy:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def process_turn(self, *, current_user: User, turn: RepairTurn) -> None:
        self.calls.append(turn.id)


def _artifact(artifact_id: str, **values: Any) -> ArtifactRef:
    defaults: dict[str, Any] = {
        "id": artifact_id,
        "user_id": "usr_turn_service",
        "repair_session_id": "rs_turn_service",
        "purpose": "diagnostic_photo",
        "media_type": "image",
        "mime_type": "image/jpeg",
        "filename": "photo.jpg",
        "byte_size": 12,
        "status": "ready",
        "content_sha256": "a" * 64,
        "storage_provider": "local",
        "storage_path": "objects/photo.jpg",
        "created_at": datetime(2026, 1, 1, tzinfo=UTC),
        "updated_at": datetime(2026, 1, 1, tzinfo=UTC),
    }
    defaults.update(values)
    return ArtifactRef(**defaults)


def _request(*artifact_ids: str) -> TurnCreate:
    return TurnCreate.model_validate(
        {
            "schema_version": "ai_turn.v1",
            "client_turn_id": "turn-service-test",
            "message": {"text": "Please inspect these.", "artifact_ids": artifact_ids},
        },
    )


def _user() -> User:
    return User(
        id="usr_turn_service",
        auth_subject="auth|turn-service",
        email="turn-service@example.com",
        display_name="Turn Service",
        skill_level="unknown",
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
    )


def _service(
    repositories: _TurnRepositories,
    orchestrator: _OrchestratorSpy,
    *,
    image_analysis_mode: str = "enabled",
) -> TurnService:
    return TurnService(
        repositories,
        _PhaseSessionManager(),
        repositories,
        repositories,
        repositories,
        phase_session_manager=_PhaseSessionManager(),
        orchestrator=orchestrator,
        image_analysis_mode=image_analysis_mode,
    )


async def test_accept_turn_accepts_ready_owned_diagnostic_image() -> None:
    repositories = _TurnRepositories()
    orchestrator = _OrchestratorSpy()

    accepted = await _service(repositories, orchestrator).accept_turn(
        current_user=_user(),
        repair_session_id=repositories.session.id,
        request=_request("art_ready"),
    )

    assert accepted.turn_id == "turn_1"
    assert [turn.id for turn in repositories.turns] == ["turn_1"]
    assert orchestrator.calls == ["turn_1"]


async def test_accept_turn_snapshots_the_configured_image_analysis_mode() -> None:
    repositories = _TurnRepositories()

    await _service(
        repositories,
        _OrchestratorSpy(),
        image_analysis_mode="shadow",
    ).accept_turn(
        current_user=_user(),
        repair_session_id=repositories.session.id,
        request=_request("art_ready"),
    )

    assert repositories.turns[0].image_analysis_mode == "shadow"


async def test_idempotent_image_turn_replay_keeps_the_original_mode_snapshot() -> None:
    repositories = _TurnRepositories()
    request = _request("art_ready")

    await _service(
        repositories,
        _OrchestratorSpy(),
        image_analysis_mode="shadow",
    ).accept_turn(
        current_user=_user(),
        repair_session_id=repositories.session.id,
        request=request,
    )
    await _service(
        repositories,
        _OrchestratorSpy(),
        image_analysis_mode="off",
    ).accept_turn(
        current_user=_user(),
        repair_session_id=repositories.session.id,
        request=request,
    )

    assert len(repositories.turns) == 1
    assert repositories.turns[0].image_analysis_mode == "shadow"


async def test_text_only_turn_has_no_image_analysis_mode_snapshot() -> None:
    repositories = _TurnRepositories()
    text_only = TurnCreate.model_validate(
        {
            "schema_version": "ai_turn.v1",
            "client_turn_id": "text-only-turn",
            "message": {"text": "The chain skips.", "artifact_ids": []},
        },
    )

    await _service(
        repositories,
        _OrchestratorSpy(),
        image_analysis_mode="enabled",
    ).accept_turn(
        current_user=_user(),
        repair_session_id=repositories.session.id,
        request=text_only,
    )

    assert repositories.turns[0].image_analysis_mode is None


@pytest.mark.parametrize(
    ("artifact", "error"),
    [
        (_artifact("art_other_user", user_id="usr_other"), NotFoundError),
        (_artifact("art_wrong_session", repair_session_id="rs_other"), NotFoundError),
        (
            _artifact("art_wrong_purpose", purpose="verification_photo"),
            ValidationAppError,
        ),
        (_artifact("art_not_image", media_type="document"), ValidationAppError),
        (_artifact("art_wrong_mime", mime_type="image/gif"), ValidationAppError),
        (_artifact("art_uploading", status="uploaded"), ArtifactNotReadyError),
        (_artifact("art_processing", status="processing"), ArtifactNotReadyError),
        (
            _artifact("art_rejected", status="rejected", rejection_reason="invalid"),
            ValidationAppError,
        ),
    ],
)
async def test_accept_turn_rejects_artifact_that_fails_diagnostic_metadata_acceptance(
    artifact: ArtifactRef,
    error: type[Exception],
) -> None:
    repositories = _TurnRepositories()
    repositories.artifacts = {artifact.id: artifact}
    orchestrator = _OrchestratorSpy()

    with pytest.raises(error):
        await _service(repositories, orchestrator).accept_turn(
            current_user=_user(),
            repair_session_id=repositories.session.id,
            request=_request(artifact.id),
        )

    assert repositories.turns == []
    assert repositories.events == []
    assert orchestrator.calls == []


async def test_accept_turn_rejects_missing_artifact_without_disclosing_it() -> None:
    repositories = _TurnRepositories()
    orchestrator = _OrchestratorSpy()

    with pytest.raises(NotFoundError):
        await _service(repositories, orchestrator).accept_turn(
            current_user=_user(),
            repair_session_id=repositories.session.id,
            request=_request("art_missing"),
        )

    assert repositories.turns == []
    assert orchestrator.calls == []


async def test_accept_turn_validates_every_mixed_artifact_before_rejecting_all() -> (
    None
):
    repositories = _TurnRepositories()
    invalid = _artifact("art_invalid_mixed", mime_type="image/gif")
    repositories.artifacts[invalid.id] = invalid
    orchestrator = _OrchestratorSpy()

    with pytest.raises(ValidationAppError):
        await _service(repositories, orchestrator).accept_turn(
            current_user=_user(),
            repair_session_id=repositories.session.id,
            request=_request("art_ready", invalid.id),
        )

    assert repositories.lookups == ["art_ready", invalid.id]
    assert repositories.turns == []
    assert repositories.events == []
    assert orchestrator.calls == []
