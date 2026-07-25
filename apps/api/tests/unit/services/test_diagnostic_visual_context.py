"""Visual-context preparation tests at the service seam."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from bike_doc_api.services.diagnostic_visual_context import (
    DiagnosticVisualContextService,
)
from bike_doc_api.services.image_preprocessing import (
    ImagePreprocessingError,
    NormalizedDiagnosticImage,
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
