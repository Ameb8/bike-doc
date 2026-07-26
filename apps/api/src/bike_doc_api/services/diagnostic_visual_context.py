"""Prepare safe, app-owned visual context for one accepted diagnostic turn."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from time import monotonic
from typing import Any, Literal, Protocol

from bike_doc_api.core.errors import NotFoundError, ValidationAppError
from bike_doc_api.models.observation_extraction import (
    ObservationExtractionAttempt,
    ObservationExtractionRun,
)
from bike_doc_api.schemas.observation_extraction import (
    ArtifactProcessingStatus,
    DiagnosticVisualObservationProjection,
    NormalizedModelImage,
    ObservationExtractionOutput,
)
from bike_doc_api.services.image_preprocessing import (
    PREPROCESSING_VERSION,
    ImagePreprocessingError,
    NormalizedDiagnosticImage,
    normalize_diagnostic_image_async,
)
from bike_doc_api.services.observation_extraction import (
    DiagnosticObservationExtractor,
    ObservationExtractionRequest,
)

_ACCEPTED_IMAGE_MIME_TYPES = frozenset({"image/jpeg", "image/png", "image/webp"})


class RepairTurnRepositoryProtocol(Protocol):
    """Accepted-turn lookup needed to prepare visual context."""

    async def get(self, turn_id: str) -> RepairTurnRecord | None:
        """Return an accepted repair turn by ID."""


class RepairSessionRepositoryProtocol(Protocol):
    """Repair-session lookup needed to revalidate ownership."""

    async def get(self, repair_session_id: str) -> RepairSessionRecord | None:
        """Return a repair session by ID."""


class ArtifactRepositoryProtocol(Protocol):
    """Owner-scoped metadata lookup needed before reading an artifact."""

    async def get_owned(
        self,
        *,
        artifact_id: str,
        user_id: str,
    ) -> ArtifactRecord | None:
        """Return one artifact only when it belongs to the supplied user."""


class StorageProviderProtocol(Protocol):
    """Private artifact-byte read boundary used only for pixels-only mode."""

    async def get_object(self, *, path: str, bucket: str | None) -> bytes:
        """Read artifact bytes from app-owned private storage metadata."""


class ObservationExtractionRunRepositoryProtocol(Protocol):
    """Persistence boundary for one accepted turn's extraction lifecycle."""

    async def get_by_turn_id(self, turn_id: str) -> ObservationExtractionRun | None: ...

    async def add(self, run: ObservationExtractionRun) -> ObservationExtractionRun: ...

    async def set_preprocessing_manifest(
        self,
        run: ObservationExtractionRun,
        *,
        manifest: list[dict[str, Any]],
    ) -> ObservationExtractionRun: ...

    async def append_attempt(
        self, *, run_id: str, provider: str, model: str
    ) -> ObservationExtractionAttempt: ...

    async def finish_attempt(
        self,
        attempt: ObservationExtractionAttempt,
        *,
        outcome: str,
        failure_metadata: dict[str, Any] | None = None,
        latency_ms: int | None = None,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
        provider_cost_microunits: int | None = None,
        completed_at: datetime | None = None,
    ) -> ObservationExtractionAttempt: ...

    async def mark_completed(
        self, run: ObservationExtractionRun, *, validated_output: dict[str, Any]
    ) -> ObservationExtractionRun: ...

    async def mark_failed(
        self, run: ObservationExtractionRun, *, failure_metadata: dict[str, Any]
    ) -> ObservationExtractionRun: ...

    async def mark_diagnostic_agent_started(
        self, run: ObservationExtractionRun
    ) -> ObservationExtractionRun: ...

    async def list_usable_for_session(
        self, repair_session_id: str, *, limit: int = 50
    ) -> list[ObservationExtractionRun]: ...


Preprocessor = Callable[..., Awaitable[NormalizedDiagnosticImage]]


class RepairTurnRecord(Protocol):
    """The accepted-turn fields visual preparation is allowed to inspect."""

    repair_session_id: str
    image_analysis_mode: str | None
    message: Mapping[str, object]


class RepairSessionRecord(Protocol):
    """The owner fields needed to validate a turn's repair session."""

    id: str
    user_id: str


class ArtifactRecord(Protocol):
    """App-owned artifact metadata used before private bytes are read."""

    id: str
    repair_session_id: str | None
    purpose: str
    media_type: str
    mime_type: str
    status: str
    storage_path: str
    storage_bucket: str | None


@dataclass(frozen=True, slots=True)
class DiagnosticVisualContextError:
    """A bounded recoverable preparation error, safe for later event mapping."""

    code: Literal[
        "image_not_ready",
        "image_decode_failed",
        "image_normalization_failed",
        "image_analysis_unavailable",
    ]
    artifact_id: str | None
    retryable: bool


@dataclass(frozen=True, slots=True)
class DiagnosticVisualContext:
    """All visual inputs and recovery state for one diagnostic-agent invocation."""

    invoke_agent: bool
    current_images: tuple[NormalizedModelImage, ...]
    current_observations: tuple[DiagnosticVisualObservationProjection, ...]
    prior_observations: tuple[DiagnosticVisualObservationProjection, ...]
    artifact_processing_statuses: tuple[ArtifactProcessingStatus, ...]
    recoverable_errors: tuple[DiagnosticVisualContextError, ...]


class DiagnosticVisualContextService:
    """Keep mode-specific visual preparation behind one orchestration seam.

    ``shadow`` performs isolated extraction and durable lifecycle recording
    while deliberately withholding every observation from diagnostic context.
    """

    def __init__(
        self,
        *,
        turns: RepairTurnRepositoryProtocol,
        repair_sessions: RepairSessionRepositoryProtocol,
        artifacts: ArtifactRepositoryProtocol,
        storage: StorageProviderProtocol,
        runs: ObservationExtractionRunRepositoryProtocol | None = None,
        extractor: DiagnosticObservationExtractor | None = None,
        extractor_version: str = "visual-observation-extractor.v1",
        prompt_version: str = "visual-observation-prompt.v1",
        preprocess: Preprocessor = normalize_diagnostic_image_async,
    ) -> None:
        self._turns = turns
        self._repair_sessions = repair_sessions
        self._artifacts = artifacts
        self._storage = storage
        self._runs = runs
        self._extractor = extractor
        self._extractor_version = extractor_version
        self._prompt_version = prompt_version
        self._preprocess = preprocess

    async def prepare_turn(
        self,
        *,
        user_id: str,
        turn_id: str,
    ) -> DiagnosticVisualContext:
        """Reload and prepare the current turn without exposing storage internals."""

        turn = await self._turns.get(turn_id)
        if turn is None:
            raise NotFoundError()
        repair_session = await self._repair_sessions.get(turn.repair_session_id)
        if repair_session is None or repair_session.user_id != user_id:
            raise NotFoundError()

        artifact_ids, text_bearing = _submitted_artifacts_and_text(turn)
        mode = getattr(turn, "image_analysis_mode", None)
        if not artifact_ids:
            return DiagnosticVisualContext(
                invoke_agent=text_bearing,
                current_images=(),
                current_observations=(),
                prior_observations=await self._prior_enabled_observations(
                    repair_session_id=repair_session.id,
                    current_turn_id=turn_id,
                ),
                artifact_processing_statuses=(),
                recoverable_errors=(),
            )
        if mode not in {"off", "pixels_only", "shadow", "enabled"}:
            raise ValidationAppError("Unsupported image-analysis mode for this turn.")

        artifacts = await self._revalidate_artifacts(
            artifact_ids=artifact_ids,
            user_id=user_id,
            repair_session_id=repair_session.id,
        )
        if mode == "off":
            return self._off_context(
                artifacts=artifacts,
                text_bearing=text_bearing,
            )
        if mode == "pixels_only":
            return await self._pixels_only_context(
                artifacts=artifacts,
                text_bearing=text_bearing,
            )
        return await self._extraction_context(
            artifacts=artifacts,
            text_bearing=text_bearing,
            turn_id=turn_id,
            repair_session_id=repair_session.id,
            artifact_ids=artifact_ids,
            mode=mode,
        )

    async def mark_diagnostic_agent_started(self, *, turn_id: str) -> None:
        """Durably close an extraction run immediately before runner invocation."""

        if self._runs is None:
            return
        run = await self._runs.get_by_turn_id(turn_id)
        if run is not None:
            await self._runs.mark_diagnostic_agent_started(run)

    async def _revalidate_artifacts(
        self,
        *,
        artifact_ids: tuple[str, ...],
        user_id: str,
        repair_session_id: str,
    ) -> tuple[ArtifactRecord, ...]:
        """Perform ownership and accepted-metadata checks before storage access."""

        artifacts: list[ArtifactRecord] = []
        for artifact_id in artifact_ids:
            artifact = await self._artifacts.get_owned(
                artifact_id=artifact_id,
                user_id=user_id,
            )
            if artifact is None or artifact.repair_session_id != repair_session_id:
                raise NotFoundError()
            if (
                artifact.purpose != "diagnostic_photo"
                or artifact.media_type != "image"
                or artifact.mime_type not in _ACCEPTED_IMAGE_MIME_TYPES
            ):
                raise ValidationAppError(
                    "Artifact is not an accepted diagnostic image."
                )
            artifacts.append(artifact)
        return tuple(artifacts)

    def _off_context(
        self,
        *,
        artifacts: tuple[ArtifactRecord, ...],
        text_bearing: bool,
    ) -> DiagnosticVisualContext:
        """Represent deliberately uninspected images without touching their bytes."""

        statuses = tuple(
            _unavailable_status(
                artifact.id,
                "image_analysis_unavailable"
                if artifact.status == "ready"
                else "image_not_ready",
                artifact.status in {"uploaded", "processing"},
            )
            for artifact in artifacts
        )
        errors: tuple[DiagnosticVisualContextError, ...] = ()
        if not text_bearing:
            errors = (
                DiagnosticVisualContextError(
                    code="image_analysis_unavailable",
                    artifact_id=None,
                    retryable=False,
                ),
            )
        return DiagnosticVisualContext(
            invoke_agent=text_bearing,
            current_images=(),
            current_observations=(),
            prior_observations=(),
            artifact_processing_statuses=statuses,
            recoverable_errors=errors,
        )

    async def _pixels_only_context(
        self,
        *,
        artifacts: tuple[ArtifactRecord, ...],
        text_bearing: bool,
    ) -> DiagnosticVisualContext:
        """Load and normalize current artifacts independently for graceful fallback."""

        images: list[NormalizedModelImage] = []
        statuses: list[ArtifactProcessingStatus] = []
        errors: list[DiagnosticVisualContextError] = []
        for artifact in artifacts:
            artifact_id = artifact.id
            status = artifact.status
            if status != "ready":
                retryable = status in {"uploaded", "processing"}
                statuses.append(
                    _unavailable_status(artifact_id, "image_not_ready", retryable)
                )
                errors.append(
                    DiagnosticVisualContextError(
                        "image_not_ready", artifact_id, retryable
                    )
                )
                continue
            try:
                content = await self._storage.get_object(
                    path=artifact.storage_path,
                    bucket=artifact.storage_bucket,
                )
            except Exception:
                statuses.append(
                    _unavailable_status(artifact_id, "image_not_ready", True)
                )
                errors.append(
                    DiagnosticVisualContextError("image_not_ready", artifact_id, True)
                )
                continue
            try:
                normalized = await self._preprocess(
                    artifact_id=artifact_id,
                    declared_mime_type=artifact.mime_type,
                    effective_mime_type=artifact.mime_type,
                    content=content,
                )
            except ImagePreprocessingError as exc:
                statuses.append(_unavailable_status(artifact_id, exc.code))
                errors.append(
                    DiagnosticVisualContextError(exc.code, artifact_id, False)
                )
                continue
            images.append(_model_image(normalized))
            statuses.append(
                ArtifactProcessingStatus(artifact_id=artifact_id, status="available")
            )

        if not images and not text_bearing:
            errors.append(
                DiagnosticVisualContextError(
                    code="image_analysis_unavailable",
                    artifact_id=None,
                    retryable=False,
                ),
            )
        return DiagnosticVisualContext(
            invoke_agent=text_bearing or bool(images),
            current_images=tuple(images),
            current_observations=(),
            prior_observations=(),
            artifact_processing_statuses=tuple(statuses),
            recoverable_errors=tuple(errors),
        )

    async def _extraction_context(
        self,
        *,
        artifacts: tuple[ArtifactRecord, ...],
        text_bearing: bool,
        turn_id: str,
        repair_session_id: str,
        artifact_ids: tuple[str, ...],
        mode: Literal["shadow", "enabled"],
    ) -> DiagnosticVisualContext:
        """Extract current evidence and expose it only in enabled mode."""

        if self._runs is None or self._extractor is None:
            raise ValidationAppError("Observation image analysis is not configured.")
        run = await self._get_or_create_run(
            turn_id=turn_id,
            repair_session_id=repair_session_id,
            artifact_ids=artifact_ids,
            mode=mode,
        )
        context = await self._pixels_only_context(
            artifacts=artifacts,
            text_bearing=text_bearing,
        )
        await self._runs.set_preprocessing_manifest(
            run,
            manifest=_preprocessing_manifest_for_context(context),
        )
        if not context.current_images:
            if run.status == "pending":
                await self._runs.mark_failed(
                    run,
                    failure_metadata={"code": "no_usable_images", "retryable": False},
                )
            return DiagnosticVisualContext(
                invoke_agent=context.invoke_agent,
                current_images=(),
                current_observations=(),
                prior_observations=await self._prior_enabled_observations(
                    repair_session_id=repair_session_id,
                    current_turn_id=turn_id,
                ),
                artifact_processing_statuses=context.artifact_processing_statuses,
                recoverable_errors=context.recoverable_errors,
            )

        if run.status == "pending" and (run.provider_attempt_count or 0) == 0:
            await self._extract_once(run=run, images=context.current_images)

        current_observations: tuple[DiagnosticVisualObservationProjection, ...] = ()
        if mode == "enabled" and run.status == "completed":
            projection = _projection_from_run(run)
            if projection is not None:
                current_observations = (projection,)

        return DiagnosticVisualContext(
            invoke_agent=context.invoke_agent,
            current_images=context.current_images,
            current_observations=current_observations,
            prior_observations=await self._prior_enabled_observations(
                repair_session_id=repair_session_id,
                current_turn_id=turn_id,
            ),
            artifact_processing_statuses=context.artifact_processing_statuses,
            recoverable_errors=context.recoverable_errors,
        )

    async def _get_or_create_run(
        self,
        *,
        turn_id: str,
        repair_session_id: str,
        artifact_ids: tuple[str, ...],
        mode: Literal["shadow", "enabled"],
    ) -> ObservationExtractionRun:
        assert self._runs is not None and self._extractor is not None
        run = await self._runs.get_by_turn_id(turn_id)
        if run is not None:
            return run
        return await self._runs.add(
            ObservationExtractionRun(
                turn_id=turn_id,
                repair_session_id=repair_session_id,
                image_analysis_mode=mode,
                input_artifact_ids=list(artifact_ids),
                preprocessing_version=PREPROCESSING_VERSION,
                extractor_version=self._extractor_version,
                prompt_version=self._prompt_version,
                output_schema_version="visual-observation.v1",
                provider=self._extractor.provider,
                model=self._extractor.model,
            )
        )

    async def _prior_enabled_observations(
        self,
        *,
        repair_session_id: str,
        current_turn_id: str,
    ) -> tuple[DiagnosticVisualObservationProjection, ...]:
        """Project prior enabled evidence without reading historical bytes."""

        if self._runs is None:
            return ()
        prior_runs = await self._runs.list_usable_for_session(repair_session_id)
        projections: list[DiagnosticVisualObservationProjection] = []
        for run in prior_runs:
            if (
                run.turn_id == current_turn_id
                or getattr(run, "repair_session_id", None) != repair_session_id
                or getattr(run, "image_analysis_mode", None) != "enabled"
                or getattr(run, "status", None) != "completed"
                or getattr(run, "redacted_at", None) is not None
            ):
                continue
            projection = _projection_from_run(run)
            if projection is not None:
                projections.append(projection)
        return tuple(projections)

    async def _extract_once(
        self,
        *,
        run: ObservationExtractionRun,
        images: tuple[NormalizedModelImage, ...],
    ) -> None:
        assert self._runs is not None and self._extractor is not None
        attempt = await self._runs.append_attempt(
            run_id=run.id,
            provider=self._extractor.provider,
            model=self._extractor.model,
        )
        started = monotonic()
        try:
            result = await self._extractor.extract(
                ObservationExtractionRequest(images=list(images)),
            )
            # Adapters validate too, but lifecycle owns the trust boundary.
            from bike_doc_api.schemas.observation_extraction import (
                validate_observation_output,
            )

            validated = validate_observation_output(result.output, images)
        except Exception:
            failure = {"code": "observation_extraction_failed", "retryable": False}
            await self._runs.finish_attempt(
                attempt,
                outcome="failed",
                failure_metadata=failure,
                latency_ms=_elapsed_ms(started),
            )
            await self._runs.mark_failed(run, failure_metadata=failure)
            return

        usage = result.usage
        cost = round(usage.cost_usd * 1_000_000) if usage.cost_usd is not None else None
        await self._runs.finish_attempt(
            attempt,
            outcome="completed",
            latency_ms=_elapsed_ms(started),
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            provider_cost_microunits=cost,
        )
        await self._runs.mark_completed(
            run,
            validated_output=validated.model_dump(mode="json"),
        )


def _submitted_artifacts_and_text(turn: object) -> tuple[tuple[str, ...], bool]:
    """Read accepted message fields defensively without trusting mutable JSON."""

    message = getattr(turn, "message", None)
    if not isinstance(message, Mapping):
        raise ValidationAppError("Accepted turn has invalid message data.")
    raw_ids = message.get("artifact_ids", [])
    if not isinstance(raw_ids, list) or any(
        not isinstance(item, str) or not item for item in raw_ids
    ):
        raise ValidationAppError("Accepted turn has invalid artifact data.")
    if len(raw_ids) != len(set(raw_ids)):
        raise ValidationAppError("Accepted turn has duplicate artifact data.")
    text = message.get("text")
    return tuple(raw_ids), isinstance(text, str) and bool(text.strip())


def _empty_context(*, invoke_agent: bool) -> DiagnosticVisualContext:
    return DiagnosticVisualContext(invoke_agent, (), (), (), (), ())


def _projection_from_run(
    run: ObservationExtractionRun,
) -> DiagnosticVisualObservationProjection | None:
    """Revalidate durable output before exposing a score-free projection."""

    if run.validated_output is None:
        return None
    try:
        return ObservationExtractionOutput.model_validate(
            run.validated_output
        ).diagnostic_agent_projection()
    except ValueError:
        return None


def _unavailable_status(
    artifact_id: str,
    code: Literal[
        "image_not_ready",
        "image_decode_failed",
        "image_normalization_failed",
        "image_analysis_unavailable",
    ],
    retryable: bool = False,
) -> ArtifactProcessingStatus:
    return ArtifactProcessingStatus(
        artifact_id=artifact_id,
        status="unavailable",
        failure_code=code,
        retryable=retryable,
    )


def _model_image(image: NormalizedDiagnosticImage) -> NormalizedModelImage:
    return NormalizedModelImage(
        artifact_id=image.artifact_id,
        mime_type=image.mime_type,
        content=image.content,
        original_width=image.original_width,
        original_height=image.original_height,
        normalized_width=image.normalized_width,
        normalized_height=image.normalized_height,
        content_sha256=image.content_sha256,
        preprocessing_version=image.preprocessing_version,
    )


def _preprocessing_manifest(image: NormalizedModelImage) -> dict[str, Any]:
    """Build the byte-free durable record for one normalized input."""

    return {
        "artifact_id": image.artifact_id,
        "effective_mime_type": image.mime_type,
        "original_width": image.original_width,
        "original_height": image.original_height,
        "normalized_width": image.normalized_width,
        "normalized_height": image.normalized_height,
        "normalized_content_sha256": image.content_sha256,
        "preprocessing_version": image.preprocessing_version,
        "outcome": "available",
    }


def _preprocessing_manifest_for_context(
    context: DiagnosticVisualContext,
) -> list[dict[str, Any]]:
    """Record every submitted image outcome without retaining any image bytes."""

    available = {
        image.artifact_id: _preprocessing_manifest(image)
        for image in context.current_images
    }
    manifest: list[dict[str, Any]] = []
    for status in context.artifact_processing_statuses:
        entry = available.get(status.artifact_id)
        if entry is None:
            entry = {
                "artifact_id": status.artifact_id,
                "outcome": status.failure_code or "image_analysis_unavailable",
            }
        manifest.append(entry)
    return manifest


def _elapsed_ms(started: float) -> int:
    """Return non-negative wall-clock independent provider latency."""

    return max(0, round((monotonic() - started) * 1000))
