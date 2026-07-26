"""Visual-context preparation tests at the service seam."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from bike_doc_api.schemas.observation_extraction import ObservationExtractionOutput
from bike_doc_api.services.diagnostic_visual_context import (
    DiagnosticVisualContextService,
)
from bike_doc_api.services.image_preprocessing import (
    ImagePreprocessingError,
    NormalizedDiagnosticImage,
)
from bike_doc_api.services.observation_extraction import (
    ObservationExtractionResult,
    ObservationExtractionUsage,
)


class _Repositories:
    def __init__(self, *, mode: str, text: str | None = "Describe the noise.") -> None:
        self.turn = SimpleNamespace(
            id="turn_current",
            repair_session_id="rs_current",
            image_analysis_mode=mode,
            message={"text": text, "artifact_ids": ["art_current"]},
        )
        self.session = SimpleNamespace(id="rs_current", user_id="usr_current")
        self.artifacts: dict[str, object] = {
            "art_current": SimpleNamespace(
                id="art_current",
                user_id="usr_current",
                repair_session_id="rs_current",
                purpose="diagnostic_photo",
                media_type="image",
                mime_type="image/jpeg",
                status="ready",
                storage_path="private/current.jpg",
                storage_bucket=None,
            )
        }

    async def get_turn(self, turn_id: str) -> object | None:
        return self.turn if turn_id == self.turn.id else None

    async def get_session(self, session_id: str) -> object | None:
        return self.session if session_id == self.session.id else None

    async def get_artifact(self, *, artifact_id: str, user_id: str) -> object | None:
        artifact = self.artifacts.get(artifact_id)
        return artifact if getattr(artifact, "user_id", None) == user_id else None


class _Storage:
    provider_name = "fake"

    def __init__(self) -> None:
        self.requests: list[tuple[str, str | None]] = []

    async def get_object(self, *, path: str, bucket: str | None) -> bytes:
        self.requests.append((path, bucket))
        return b"source-bytes"


class _Runs:
    def __init__(self) -> None:
        self.run: object | None = None
        self.attempts: list[object] = []

    async def get_by_turn_id(self, turn_id: str) -> object | None:
        return self.run

    async def list_usable_for_session(
        self, repair_session_id: str, *, limit: int = 50
    ) -> list[object]:
        assert repair_session_id == "rs_current"
        assert limit == 50
        return [self.run] if self.run is not None else []

    async def add(self, run: object) -> object:
        run.provider_attempt_count = 0
        run.status = "pending"
        self.run = run
        return run

    async def set_preprocessing_manifest(
        self, run: object, *, manifest: list[dict[str, object]]
    ) -> object:
        run.preprocessing_manifest = manifest
        return run

    async def append_attempt(self, **values: object) -> object:
        attempt = SimpleNamespace(**values, outcome="pending")
        self.attempts.append(attempt)
        assert self.run is not None
        self.run.provider_attempt_count += 1
        return attempt

    async def finish_attempt(self, attempt: object, **values: object) -> object:
        for key, value in values.items():
            setattr(attempt, key, value)
        return attempt

    async def mark_completed(self, run: object, **values: object) -> object:
        run.status = "completed"
        run.validated_output = values["validated_output"]
        return run

    async def mark_failed(self, run: object, **values: object) -> object:
        run.status = "failed"
        run.validated_output = None
        run.failure_metadata = values["failure_metadata"]
        return run


class _Extractor:
    provider = "fake-provider"
    model = "fake-model"

    def __init__(self) -> None:
        self.requests: list[object] = []

    async def extract(self, request: object) -> ObservationExtractionResult:
        self.requests.append(request)
        return ObservationExtractionResult(
            output=ObservationExtractionOutput.model_validate(
                {
                    "schema_version": "visual-observation.v1",
                    "image_assessments": [
                        {
                            "artifact_id": "art_current",
                            "assessability": "usable",
                            "visible_areas": ["chain"],
                            "limitations": [],
                        }
                    ],
                    "observations": [],
                }
            ),
            usage=ObservationExtractionUsage(input_tokens=11, output_tokens=7),
        )


@pytest.mark.asyncio
async def test_off_text_turn_revalidates_metadata_without_pixel_access() -> None:
    repositories = _Repositories(mode="off")
    storage = _Storage()
    preprocess_calls: list[str] = []

    async def preprocess(**values: Any) -> object:
        preprocess_calls.append(str(values["artifact_id"]))
        raise AssertionError("off mode must not preprocess")

    service = DiagnosticVisualContextService(
        turns=SimpleNamespace(get=repositories.get_turn),
        repair_sessions=SimpleNamespace(get=repositories.get_session),
        artifacts=SimpleNamespace(get_owned=repositories.get_artifact),
        storage=storage,
        preprocess=preprocess,
    )

    context = await service.prepare_turn(user_id="usr_current", turn_id="turn_current")

    assert context.invoke_agent is True
    assert context.current_images == ()
    assert context.current_observations == ()
    assert context.prior_observations == ()
    assert context.artifact_processing_statuses[0].model_dump() == {
        "artifact_id": "art_current",
        "status": "unavailable",
        "failure_code": "image_analysis_unavailable",
        "retryable": False,
    }
    assert context.recoverable_errors == ()
    assert storage.requests == []
    assert preprocess_calls == []


@pytest.mark.asyncio
async def test_off_image_only_turn_forbids_agent_and_returns_canonical_error() -> None:
    repositories = _Repositories(mode="off", text=None)
    storage = _Storage()
    service = DiagnosticVisualContextService(
        turns=SimpleNamespace(get=repositories.get_turn),
        repair_sessions=SimpleNamespace(get=repositories.get_session),
        artifacts=SimpleNamespace(get_owned=repositories.get_artifact),
        storage=storage,
    )

    context = await service.prepare_turn(user_id="usr_current", turn_id="turn_current")

    assert context.invoke_agent is False
    assert context.current_images == ()
    assert [error.code for error in context.recoverable_errors] == [
        "image_analysis_unavailable"
    ]
    assert storage.requests == []


@pytest.mark.asyncio
async def test_off_revalidates_not_ready_metadata_without_reading_pixels() -> None:
    repositories = _Repositories(mode="off")
    repositories.artifacts["art_current"].status = "processing"
    storage = _Storage()
    service = DiagnosticVisualContextService(
        turns=SimpleNamespace(get=repositories.get_turn),
        repair_sessions=SimpleNamespace(get=repositories.get_session),
        artifacts=SimpleNamespace(get_owned=repositories.get_artifact),
        storage=storage,
    )

    context = await service.prepare_turn(user_id="usr_current", turn_id="turn_current")

    assert context.invoke_agent is True
    assert context.artifact_processing_statuses[0].failure_code == "image_not_ready"
    assert context.artifact_processing_statuses[0].retryable is True
    assert storage.requests == []


@pytest.mark.asyncio
async def test_pixels_only_keeps_valid_current_image_when_another_cannot_decode() -> (
    None
):
    repositories = _Repositories(mode="pixels_only")
    repositories.turn.message["artifact_ids"].append("art_bad")
    repositories.artifacts["art_bad"] = SimpleNamespace(
        **{
            **vars(repositories.artifacts["art_current"]),
            "id": "art_bad",
            "storage_path": "private/bad.jpg",
        },
    )
    storage = _Storage()
    preprocessing_calls: list[str] = []

    async def preprocess(**values: Any) -> NormalizedDiagnosticImage:
        artifact_id = str(values["artifact_id"])
        preprocessing_calls.append(artifact_id)
        if artifact_id == "art_bad":
            raise ImagePreprocessingError("image_decode_failed")
        return _normalized(artifact_id)

    service = DiagnosticVisualContextService(
        turns=SimpleNamespace(get=repositories.get_turn),
        repair_sessions=SimpleNamespace(get=repositories.get_session),
        artifacts=SimpleNamespace(get_owned=repositories.get_artifact),
        storage=storage,
        preprocess=preprocess,
    )

    context = await service.prepare_turn(user_id="usr_current", turn_id="turn_current")

    assert context.invoke_agent is True
    assert [image.artifact_id for image in context.current_images] == ["art_current"]
    assert [status.model_dump() for status in context.artifact_processing_statuses] == [
        {
            "artifact_id": "art_current",
            "status": "available",
            "failure_code": None,
            "retryable": False,
        },
        {
            "artifact_id": "art_bad",
            "status": "unavailable",
            "failure_code": "image_decode_failed",
            "retryable": False,
        },
    ]
    assert [error.artifact_id for error in context.recoverable_errors] == ["art_bad"]
    assert preprocessing_calls == ["art_current", "art_bad"]
    assert storage.requests == [
        ("private/current.jpg", None),
        ("private/bad.jpg", None),
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "text, expected_invoke", [("Use the description.", True), (None, False)]
)
async def test_pixels_only_all_invalid_images_fall_back_based_on_text(
    text: str | None,
    expected_invoke: bool,
) -> None:
    repositories = _Repositories(mode="pixels_only", text=text)
    storage = _Storage()

    async def preprocess(**_values: Any) -> NormalizedDiagnosticImage:
        raise ImagePreprocessingError("image_normalization_failed")

    service = DiagnosticVisualContextService(
        turns=SimpleNamespace(get=repositories.get_turn),
        repair_sessions=SimpleNamespace(get=repositories.get_session),
        artifacts=SimpleNamespace(get_owned=repositories.get_artifact),
        storage=storage,
        preprocess=preprocess,
    )

    context = await service.prepare_turn(user_id="usr_current", turn_id="turn_current")

    assert context.invoke_agent is expected_invoke
    assert context.current_images == ()
    assert (
        context.artifact_processing_statuses[0].failure_code
        == "image_normalization_failed"
    )
    expected_errors = ["image_normalization_failed"]
    if not expected_invoke:
        expected_errors.append("image_analysis_unavailable")
    assert [error.code for error in context.recoverable_errors] == expected_errors


@pytest.mark.asyncio
async def test_pixels_only_never_reads_unsubmitted_historical_artifacts() -> None:
    repositories = _Repositories(mode="pixels_only")
    repositories.artifacts["art_historical"] = SimpleNamespace(
        **{
            **vars(repositories.artifacts["art_current"]),
            "id": "art_historical",
            "storage_path": "private/historical.jpg",
        },
    )
    storage = _Storage()

    async def preprocess(**values: Any) -> NormalizedDiagnosticImage:
        return _normalized(str(values["artifact_id"]))

    service = DiagnosticVisualContextService(
        turns=SimpleNamespace(get=repositories.get_turn),
        repair_sessions=SimpleNamespace(get=repositories.get_session),
        artifacts=SimpleNamespace(get_owned=repositories.get_artifact),
        storage=storage,
        preprocess=preprocess,
    )

    await service.prepare_turn(user_id="usr_current", turn_id="turn_current")

    assert storage.requests == [("private/current.jpg", None)]


@pytest.mark.asyncio
async def test_shadow_persists_a_valid_zero_observation_run_without_leaking_it() -> (
    None
):
    repositories = _Repositories(mode="shadow")
    storage = _Storage()
    runs = _Runs()
    extractor = _Extractor()

    async def preprocess(**values: Any) -> NormalizedDiagnosticImage:
        return _normalized(str(values["artifact_id"]))

    service = DiagnosticVisualContextService(
        turns=SimpleNamespace(get=repositories.get_turn),
        repair_sessions=SimpleNamespace(get=repositories.get_session),
        artifacts=SimpleNamespace(get_owned=repositories.get_artifact),
        storage=storage,
        runs=runs,
        extractor=extractor,
        preprocess=preprocess,
    )

    context = await service.prepare_turn(user_id="usr_current", turn_id="turn_current")

    assert context.current_images[0].content == b"normalized"
    assert extractor.requests[0].images[0].content == context.current_images[0].content
    assert context.current_observations == ()
    assert context.prior_observations == ()
    assert runs.run.status == "completed"
    assert runs.run.validated_output["observations"] == []
    assert runs.run.provider_attempt_count == 1
    assert len(runs.attempts) == 1


@pytest.mark.asyncio
async def test_enabled_projects_current_and_prior_evidence_without_history_reads() -> (
    None
):
    repositories = _Repositories(mode="enabled")
    storage = _Storage()
    runs = _Runs()

    prior = SimpleNamespace(
        turn_id="turn_prior",
        repair_session_id="rs_current",
        image_analysis_mode="enabled",
        status="completed",
        redacted_at=None,
        validated_output={
            "schema_version": "visual-observation.v1",
            "image_assessments": [
                {
                    "artifact_id": "art_prior",
                    "assessability": "limited",
                    "visible_areas": ["rear brake"],
                    "limitations": [
                        {"type": "glare", "description": "pad edge obscured"}
                    ],
                }
            ],
            "observations": [
                {
                    "artifact_ids": ["art_prior"],
                    "component_or_area": "rear brake",
                    "position": "rear",
                    "finding": "dark residue is visible",
                    "evidence_cues": ["dark material below caliper"],
                    "visibility": "partial",
                    "raw_model_score": 0.91,
                    "safety_relevant": True,
                }
            ],
        },
    )

    class _RunsWithPrior(_Runs):
        async def list_usable_for_session(
            self, repair_session_id: str, *, limit: int = 50
        ) -> list[object]:
            assert repair_session_id == "rs_current"
            return [prior, self.run] if self.run is not None else [prior]

    runs = _RunsWithPrior()

    class _ObservedExtractor(_Extractor):
        async def extract(self, request: object) -> ObservationExtractionResult:
            self.requests.append(request)
            return ObservationExtractionResult(
                output=ObservationExtractionOutput.model_validate(
                    {
                        "schema_version": "visual-observation.v1",
                        "image_assessments": [
                            {
                                "artifact_id": "art_current",
                                "assessability": "usable",
                                "visible_areas": ["chain"],
                                "limitations": [],
                            }
                        ],
                        "observations": [
                            {
                                "artifact_ids": ["art_current"],
                                "component_or_area": "chain",
                                "position": "rear",
                                "finding": "surface rust is visible",
                                "evidence_cues": ["orange discoloration"],
                                "visibility": "clear",
                                "raw_model_score": 0.83,
                                "safety_relevant": False,
                            }
                        ],
                    }
                ),
                usage=ObservationExtractionUsage(),
            )

    async def preprocess(**values: Any) -> NormalizedDiagnosticImage:
        return _normalized(str(values["artifact_id"]))

    service = DiagnosticVisualContextService(
        turns=SimpleNamespace(get=repositories.get_turn),
        repair_sessions=SimpleNamespace(get=repositories.get_session),
        artifacts=SimpleNamespace(get_owned=repositories.get_artifact),
        storage=storage,
        runs=runs,
        extractor=_ObservedExtractor(),
        preprocess=preprocess,
    )

    context = await service.prepare_turn(user_id="usr_current", turn_id="turn_current")

    assert context.current_images[0].artifact_id == "art_current"
    assert context.current_observations[0].observations[0].artifact_ids == [
        "art_current"
    ]
    assert context.prior_observations[0].observations[0].artifact_ids == ["art_prior"]
    assert context.prior_observations[0].image_assessments[0].assessability == "limited"
    assert "raw_model_score" not in repr(context)
    assert storage.requests == [("private/current.jpg", None)]


@pytest.mark.asyncio
async def test_prior_context_excludes_unusable_and_current_runs() -> None:
    repositories = _Repositories(mode="pixels_only", text="A later question.")
    repositories.turn.message["artifact_ids"] = []
    storage = _Storage()

    valid_output = {
        "schema_version": "visual-observation.v1",
        "image_assessments": [
            {
                "artifact_id": "art_prior",
                "assessability": "usable",
                "visible_areas": ["chain"],
                "limitations": [],
            }
        ],
        "observations": [],
    }

    def run(**values: object) -> object:
        defaults: dict[str, object] = {
            "repair_session_id": "rs_current",
            "image_analysis_mode": "enabled",
            "status": "completed",
            "redacted_at": None,
            "validated_output": valid_output,
        }
        return SimpleNamespace(**(defaults | values))

    candidates = [
        run(turn_id="turn_prior"),
        run(turn_id="turn_current"),
        run(turn_id="turn_shadow", image_analysis_mode="shadow"),
        run(turn_id="turn_failed", status="failed"),
        run(turn_id="turn_redacted", redacted_at=object()),
        run(turn_id="turn_other", repair_session_id="rs_other"),
        run(turn_id="turn_invalid", validated_output={"observations": []}),
    ]

    class _PriorRuns:
        async def list_usable_for_session(
            self, repair_session_id: str, *, limit: int = 50
        ) -> list[object]:
            assert repair_session_id == "rs_current"
            return candidates

    service = DiagnosticVisualContextService(
        turns=SimpleNamespace(get=repositories.get_turn),
        repair_sessions=SimpleNamespace(get=repositories.get_session),
        artifacts=SimpleNamespace(get_owned=repositories.get_artifact),
        storage=storage,
        runs=_PriorRuns(),
    )

    context = await service.prepare_turn(user_id="usr_current", turn_id="turn_current")

    assert len(context.prior_observations) == 1
    assert context.prior_observations[0].image_assessments[0].artifact_id == "art_prior"
    assert storage.requests == []


@pytest.mark.asyncio
async def test_shadow_all_invalid_images_fail_without_provider_attempt() -> None:
    repositories = _Repositories(mode="shadow", text="Describe the noise.")
    storage = _Storage()
    runs = _Runs()
    extractor = _Extractor()

    async def preprocess(**_values: Any) -> NormalizedDiagnosticImage:
        raise ImagePreprocessingError("image_decode_failed")

    service = DiagnosticVisualContextService(
        turns=SimpleNamespace(get=repositories.get_turn),
        repair_sessions=SimpleNamespace(get=repositories.get_session),
        artifacts=SimpleNamespace(get_owned=repositories.get_artifact),
        storage=storage,
        runs=runs,
        extractor=extractor,
        preprocess=preprocess,
    )

    context = await service.prepare_turn(user_id="usr_current", turn_id="turn_current")

    assert context.invoke_agent is True
    assert context.current_images == ()
    assert context.artifact_processing_statuses[0].failure_code == "image_decode_failed"
    assert extractor.requests == []
    assert runs.run.status == "failed"
    assert runs.run.provider_attempt_count == 0


@pytest.mark.asyncio
async def test_shadow_provider_failure_keeps_pixels_and_does_not_retry_on_replay() -> (
    None
):
    repositories = _Repositories(mode="shadow")
    storage = _Storage()
    runs = _Runs()

    class _FailingExtractor(_Extractor):
        async def extract(self, request: object) -> ObservationExtractionResult:
            self.requests.append(request)
            raise TimeoutError("provider timed out")

    extractor = _FailingExtractor()

    async def preprocess(**values: Any) -> NormalizedDiagnosticImage:
        return _normalized(str(values["artifact_id"]))

    service = DiagnosticVisualContextService(
        turns=SimpleNamespace(get=repositories.get_turn),
        repair_sessions=SimpleNamespace(get=repositories.get_session),
        artifacts=SimpleNamespace(get_owned=repositories.get_artifact),
        storage=storage,
        runs=runs,
        extractor=extractor,
        preprocess=preprocess,
    )

    first = await service.prepare_turn(user_id="usr_current", turn_id="turn_current")
    replay = await service.prepare_turn(user_id="usr_current", turn_id="turn_current")

    assert first.current_images[0].content == b"normalized"
    assert replay.current_images[0].content == b"normalized"
    assert first.current_observations == replay.current_observations == ()
    assert runs.run.status == "failed"
    assert runs.run.failure_metadata == {
        "code": "observation_extraction_failed",
        "retryable": False,
    }
    assert len(extractor.requests) == 1
    assert len(runs.attempts) == 1


@pytest.mark.asyncio
async def test_shadow_invalid_extractor_output_is_never_persisted_as_observation() -> (
    None
):
    repositories = _Repositories(mode="shadow")
    storage = _Storage()
    runs = _Runs()

    class _InvalidExtractor(_Extractor):
        async def extract(self, request: object) -> object:
            self.requests.append(request)
            return SimpleNamespace(
                output=SimpleNamespace(), usage=ObservationExtractionUsage()
            )

    extractor = _InvalidExtractor()

    async def preprocess(**values: Any) -> NormalizedDiagnosticImage:
        return _normalized(str(values["artifact_id"]))

    service = DiagnosticVisualContextService(
        turns=SimpleNamespace(get=repositories.get_turn),
        repair_sessions=SimpleNamespace(get=repositories.get_session),
        artifacts=SimpleNamespace(get_owned=repositories.get_artifact),
        storage=storage,
        runs=runs,
        extractor=extractor,
        preprocess=preprocess,
    )

    context = await service.prepare_turn(user_id="usr_current", turn_id="turn_current")

    assert context.current_images[0].content == b"normalized"
    assert context.current_observations == ()
    assert runs.run.status == "failed"
    assert runs.run.validated_output is None
    assert runs.attempts[0].outcome == "failed"


def _normalized(artifact_id: str) -> NormalizedDiagnosticImage:
    return NormalizedDiagnosticImage(
        artifact_id=artifact_id,
        content=b"normalized",
        mime_type="image/jpeg",
        original_width=100,
        original_height=80,
        normalized_width=100,
        normalized_height=80,
        content_sha256="a" * 64,
        preprocessing_version="test-v1",
    )
