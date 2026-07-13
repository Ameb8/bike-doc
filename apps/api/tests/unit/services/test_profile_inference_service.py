"""Shadow bike-profile inference service behavior."""

from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime, timedelta

import pytest

from bike_doc_api.models.artifact import ArtifactRef
from bike_doc_api.models.bike import BikeProfile
from bike_doc_api.models.repair_session import RepairSession, RepairTurn
from bike_doc_api.services.profile_inference import (
    ProfileInferenceService,
    ProfileInferenceStatus,
)


class _Extractor:
    def __init__(
        self,
        output: dict[str, object] | None = None,
        error: Exception | None = None,
    ) -> None:
        self.output = output
        self.error = error
        self.requests: list[object] = []

    async def extract(self, _request: object) -> dict[str, object]:
        self.requests.append(_request)
        if self.error is not None:
            raise self.error
        return self.output or {
            "schema_version": "bike_profile_inference.v1",
            "scene": {
                "contains_bicycle": True,
                "multiple_bicycles": False,
                "target_relation": "installed_on_target_bike",
                "confidence_score": 0.99,
            },
            "claims": [
                {
                    "field_path": "brakes.rear.mechanism",
                    "value": "disc",
                    "subject_relation": "installed_on_target_bike",
                    "evidence_basis": "direct_visual",
                    "visibility": "clear",
                    "confidence_score": 0.99,
                    "artifact_ids": ["art_rear"],
                    "observed_text": None,
                    "evidence_cues": ["A rotor and rear caliper are visible."],
                },
                {
                    "field_path": "brakes.rear.actuation",
                    "value": "hydraulic",
                    "subject_relation": "installed_on_target_bike",
                    "evidence_basis": "direct_visual",
                    "visibility": "clear",
                    "confidence_score": 0.98,
                    "artifact_ids": ["art_rear"],
                    "observed_text": None,
                    "evidence_cues": ["A hose enters the rear caliper."],
                },
            ],
            "abstentions": [],
        }


class _Store:
    def __init__(self) -> None:
        now = datetime(2026, 7, 11, tzinfo=UTC)
        self.turn = RepairTurn(
            id="turn_rear",
            repair_session_id="rs_rear",
            repair_phase_session_id="phs_rear",
            client_turn_id="client-rear",
            request_hash="request-hash",
            schema_version="ai_turn.v1",
            phase="diagnostic",
            message={"text": "This is the rear brake.", "artifact_ids": ["art_rear"]},
            start_event_sequence=1,
            created_at=now,
        )
        self.session = RepairSession(
            id="rs_rear",
            user_id="usr_rear",
            bike_id="bike_rear",
            phase="diagnostic",
            status="running",
            safety_state="ok",
            current_input_request=None,
            execution_progress=None,
            active_safety_flags=[],
            latest_event_sequence=1,
            created_at=now,
            updated_at=now,
        )
        self.bike = BikeProfile(
            id="bike_rear",
            user_id="usr_rear",
            display_name="Rear brake bike",
            bike_type="unknown",
            frame_material="unknown",
            brake_type="unknown",
            technical_profile={
                "identity": {},
                "frame": {},
                "brakes": {"front": {}, "rear": {}},
                "drivetrain": {},
                "rolling_system": {"front": {}, "rear": {}},
                "suspension": {},
                "cockpit": {},
                "seating": {},
                "electric_assist": {},
            },
            profile_revision=0,
            created_at=now,
            updated_at=now,
        )
        self.artifact = ArtifactRef(
            id="art_rear",
            user_id="usr_rear",
            repair_session_id="rs_rear",
            purpose="diagnostic_photo",
            media_type="image",
            mime_type="image/jpeg",
            filename="rear.jpg",
            byte_size=3,
            status="ready",
            content_sha256="a" * 64,
            storage_provider="fake",
            storage_path="rear.jpg",
            created_at=now,
            updated_at=now,
        )
        self.artifacts = {self.artifact.id: self.artifact}
        self.storage_error: Exception | None = None
        self.claims: list[object] = []
        self.runs: list[object] = []

    async def get(self, identifier: str) -> object | None:
        if identifier == self.turn.id:
            return self.turn
        if identifier == self.session.id:
            return self.session
        return None

    async def get_owned_active(
        self,
        *,
        bike_id: str,
        user_id: str,
    ) -> BikeProfile | None:
        if self.bike.id == bike_id and self.bike.user_id == user_id:
            return self.bike
        return None

    async def get_owned(self, *, artifact_id: str, user_id: str) -> ArtifactRef | None:
        artifact = self.artifacts.get(artifact_id)
        if artifact is not None and artifact.user_id == user_id:
            return artifact
        return None

    async def get_by_identity(
        self,
        *,
        turn_id: str,
        inference_schema_version: str,
        extractor_version: str,
    ) -> object | None:
        for run in self.runs:
            if (
                run.turn_id == turn_id
                and run.inference_schema_version == inference_schema_version
                and run.extractor_version == extractor_version
            ):
                return run
        return None

    async def add(self, item: object) -> object:
        if hasattr(item, "field_path"):
            self.claims.append(item)
        else:
            self.runs.append(item)
        return item

    async def save(self, item: object) -> object:
        return item

    async def add_claim(self, claim: object) -> object:
        self.claims.append(claim)
        return claim

    async def get_object(self, *, path: str, bucket: str | None) -> bytes:
        if self.storage_error is not None:
            raise self.storage_error
        assert path in {"rear.jpg", "rear-second.jpg"}
        assert bucket is None
        return b"jpg"


async def test_rear_brake_claims_are_persisted_as_pending_shadow_evidence() -> None:
    store = _Store()
    extractor = _Extractor()
    service = ProfileInferenceService(
        turns=store,
        repair_sessions=store,
        bikes=store,
        artifacts=store,
        runs=store,
        storage=store,
        extractor=extractor,
        extractor_version="rear-brake-shadow.v1",
    )

    outcome = await service.process_submitted_profile_evidence("turn_rear")

    assert outcome.status is ProfileInferenceStatus.COMPLETED
    assert outcome.claim_count == 2
    assert len(store.runs) == 1
    assert [claim.field_path for claim in store.claims] == [
        "brakes.rear.mechanism",
        "brakes.rear.actuation",
    ]
    assert {claim.disposition for claim in store.claims} == {"pending"}
    assert {claim.source_type for claim in store.claims} == {"image_inference"}
    assert {claim.scope_assumption for claim in store.claims} == {
        "installed_on_target_bike"
    }
    assert store.bike.technical_profile["brakes"]["rear"] == {}
    assert store.bike.profile_revision == 0
    request = extractor.requests[0]
    assert request.images[0].artifact_id == "art_rear"
    assert request.caption == "This is the rear brake."
    assert not hasattr(request, "profile")
    assert not hasattr(request, "diagnostic_history")


async def test_explicit_abstention_completes_without_claims() -> None:
    store = _Store()
    extractor = _Extractor(
        {
            "schema_version": "bike_profile_inference.v1",
            "scene": {
                "contains_bicycle": True,
                "multiple_bicycles": False,
                "target_relation": "installed_on_target_bike",
                "confidence_score": 0.9,
            },
            "claims": [],
            "abstentions": [
                {"field_path": "brakes.rear.actuation", "reason": "not_visible"},
            ],
        },
    )
    service = _service(store, extractor)

    outcome = await service.process_submitted_profile_evidence("turn_rear")

    assert outcome.status is ProfileInferenceStatus.ABSTAINED
    assert store.claims == []
    assert store.runs[0].status == "abstained"


async def test_schema_invalid_output_fails_without_claims() -> None:
    store = _Store()
    extractor = _Extractor(
        {
            "schema_version": "bike_profile_inference.v1",
            "scene": {
                "contains_bicycle": True,
                "multiple_bicycles": False,
                "target_relation": "installed_on_target_bike",
                "confidence_score": 0.9,
                "unexpected": "field",
            },
            "claims": [],
            "abstentions": [],
        },
    )

    outcome = await _service(store, extractor).process_submitted_profile_evidence(
        "turn_rear",
    )

    assert outcome.status is ProfileInferenceStatus.FAILED
    assert store.runs[0].failure_code == "schema_invalid"
    assert store.claims == []


@pytest.mark.parametrize(
    "claim_overrides",
    [
        {"field_path": "brakes.front.mechanism"},
        {"field_path": "brakes.rear.unknown"},
        {"artifact_ids": ["art_not_in_turn"]},
    ],
)
async def test_invalid_scope_unknown_path_or_artifact_reference_fails_run(
    claim_overrides: dict[str, object],
) -> None:
    store = _Store()
    output = _valid_single_claim_output()
    output["claims"][0].update(claim_overrides)

    service = _service(store, _Extractor(output))
    outcome = await service.process_submitted_profile_evidence("turn_rear")

    assert outcome.status is ProfileInferenceStatus.FAILED
    assert store.runs[0].failure_code == "schema_invalid"
    assert store.claims == []


@pytest.mark.parametrize(
    ("scene_overrides", "subject_relation"),
    [
        ({"contains_bicycle": False}, "installed_on_target_bike"),
        ({"multiple_bicycles": True}, "installed_on_target_bike"),
        ({"target_relation": "other_bike"}, "installed_on_target_bike"),
        ({}, "loose_component"),
    ],
)
async def test_scene_or_subject_that_cannot_prove_target_installed_brake_fails_run(
    scene_overrides: dict[str, object],
    subject_relation: str,
) -> None:
    store = _Store()
    output = _valid_single_claim_output()
    output["scene"].update(scene_overrides)
    output["claims"][0]["subject_relation"] = subject_relation

    outcome = await _service(
        store, _Extractor(output)
    ).process_submitted_profile_evidence(
        "turn_rear",
    )

    assert outcome.status is ProfileInferenceStatus.FAILED
    assert store.runs[0].failure_code == "schema_invalid"
    assert store.claims == []


async def test_unavailable_artifact_is_retryable_without_provider_access() -> None:
    store = _Store()
    store.artifact.status = "processing"
    extractor = _Extractor()

    outcome = await _service(store, extractor).process_submitted_profile_evidence(
        "turn_rear",
    )

    assert outcome.status is ProfileInferenceStatus.RETRYABLE
    assert outcome.run_id == store.runs[0].id
    assert store.runs[0].failure_code == "artifact_unavailable"
    assert extractor.requests == []


async def test_storage_unavailability_is_distinct_from_provider_failure() -> None:
    store = _Store()
    store.storage_error = FileNotFoundError("object not found")
    extractor = _Extractor()

    outcome = await _service(store, extractor).process_submitted_profile_evidence(
        "turn_rear",
    )

    assert outcome.status is ProfileInferenceStatus.RETRYABLE
    assert store.runs[0].failure_code == "artifact_unavailable"
    assert extractor.requests == []


async def test_running_run_replays_by_resuming_the_existing_identity() -> None:
    store = _Store()
    initial = await _service(
        store, _Extractor(error=RuntimeError())
    ).process_submitted_profile_evidence(
        "turn_rear",
    )
    assert initial.status is ProfileInferenceStatus.RETRYABLE
    store.runs[0].status = ProfileInferenceStatus.RUNNING
    store.runs[0].started_at = datetime.now(UTC) - timedelta(seconds=61)

    extractor = _Extractor()
    resumed = await _service(store, extractor).process_submitted_profile_evidence(
        "turn_rear",
    )

    assert resumed.status is ProfileInferenceStatus.COMPLETED
    assert len(store.runs) == 1
    assert len(store.claims) == 2
    assert len(extractor.requests) == 1


async def test_active_running_run_is_not_restarted_by_replay() -> None:
    store = _Store()
    initial = await _service(
        store, _Extractor(error=RuntimeError())
    ).process_submitted_profile_evidence(
        "turn_rear",
    )
    assert initial.status is ProfileInferenceStatus.RETRYABLE
    store.runs[0].status = ProfileInferenceStatus.RUNNING
    store.runs[0].started_at = datetime.now(UTC)
    extractor = _Extractor()

    replay = await _service(store, extractor).process_submitted_profile_evidence(
        "turn_rear",
    )

    assert replay.status is ProfileInferenceStatus.RUNNING
    assert len(store.runs) == 1
    assert store.claims == []
    assert extractor.requests == []


async def test_all_submitted_ready_images_are_sent_in_one_extractor_request() -> None:
    store = _Store()
    now = datetime(2026, 7, 11, tzinfo=UTC)
    store.artifacts["art_rear_second"] = ArtifactRef(
        id="art_rear_second",
        user_id="usr_rear",
        repair_session_id="rs_rear",
        purpose="diagnostic_photo",
        media_type="image",
        mime_type="image/jpeg",
        filename="rear-second.jpg",
        byte_size=3,
        status="ready",
        content_sha256="b" * 64,
        storage_provider="fake",
        storage_path="rear-second.jpg",
        created_at=now,
        updated_at=now,
    )
    store.turn.message["artifact_ids"] = ["art_rear", "art_rear_second"]
    extractor = _Extractor()

    outcome = await _service(store, extractor).process_submitted_profile_evidence(
        "turn_rear",
    )

    assert outcome.status is ProfileInferenceStatus.COMPLETED
    assert [image.artifact_id for image in extractor.requests[0].images] == [
        "art_rear",
        "art_rear_second",
    ]


async def test_retryable_failure_replay_does_not_duplicate_evidence() -> None:
    store = _Store()
    failing = _Extractor(error=RuntimeError("provider unavailable"))
    service = _service(store, failing)

    failed = await service.process_submitted_profile_evidence("turn_rear")

    assert failed.status is ProfileInferenceStatus.RETRYABLE
    assert store.runs[0].failure_code == "extractor_failure"
    successful = _Extractor()
    retry = _service(store, successful)
    completed = await retry.process_submitted_profile_evidence("turn_rear")
    replay = await retry.process_submitted_profile_evidence("turn_rear")

    assert completed.status is ProfileInferenceStatus.COMPLETED
    assert replay == completed
    assert len(store.runs) == 1
    assert len(store.claims) == 2
    assert len(successful.requests) == 1


def _service(store: _Store, extractor: _Extractor) -> ProfileInferenceService:
    return ProfileInferenceService(
        turns=store,
        repair_sessions=store,
        bikes=store,
        artifacts=store,
        runs=store,
        storage=store,
        extractor=extractor,
        extractor_version="rear-brake-shadow.v1",
    )


def _valid_single_claim_output() -> dict[str, object]:
    return deepcopy(
        {
            "schema_version": "bike_profile_inference.v1",
            "scene": {
                "contains_bicycle": True,
                "multiple_bicycles": False,
                "target_relation": "installed_on_target_bike",
                "confidence_score": 0.99,
            },
            "claims": [
                {
                    "field_path": "brakes.rear.mechanism",
                    "value": "disc",
                    "subject_relation": "installed_on_target_bike",
                    "evidence_basis": "direct_visual",
                    "visibility": "clear",
                    "confidence_score": 0.99,
                    "artifact_ids": ["art_rear"],
                    "observed_text": None,
                    "evidence_cues": ["A rotor and rear caliper are visible."],
                },
            ],
            "abstentions": [],
        },
    )
