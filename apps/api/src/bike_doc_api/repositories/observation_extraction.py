"""Persistence operations for diagnostic observation-extraction runs."""

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bike_doc_api.models.observation_extraction import (
    ObservationExtractionAttempt,
    ObservationExtractionRun,
)


class ObservationExtractionRunRepository:
    """Store one visual-evidence run and its ordered provider attempts."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, run: ObservationExtractionRun) -> ObservationExtractionRun:
        """Add a logical extraction run in the current transaction."""

        self._session.add(run)
        await self._session.flush()
        return run

    async def get_by_turn_id(self, turn_id: str) -> ObservationExtractionRun | None:
        """Return the one logical run for an accepted turn."""

        result = await self._session.execute(
            select(ObservationExtractionRun).where(
                ObservationExtractionRun.turn_id == turn_id,
            ),
        )
        return result.scalar_one_or_none()

    async def get_by_turn_id_for_update(
        self,
        turn_id: str,
    ) -> ObservationExtractionRun | None:
        """Return and lock a turn's run for lifecycle or retry changes."""

        result = await self._session.execute(
            select(ObservationExtractionRun)
            .where(ObservationExtractionRun.turn_id == turn_id)
            .with_for_update(),
        )
        return result.scalar_one_or_none()

    async def mark_completed(
        self,
        run: ObservationExtractionRun,
        *,
        validated_output: dict[str, Any],
        completed_at: datetime | None = None,
    ) -> ObservationExtractionRun:
        """Persist a schema-valid extraction result, including zero observations."""

        if run.diagnostic_agent_started_at is not None and run.status != "completed":
            raise ValueError("cannot complete extraction after diagnostic agent start")
        run.status = "completed"
        run.validated_output = validated_output
        run.failure_metadata = None
        run.completed_at = completed_at or datetime.now(UTC)
        run.failed_at = None
        await self._session.flush()
        return run

    async def mark_failed(
        self,
        run: ObservationExtractionRun,
        *,
        failure_metadata: dict[str, Any],
        failed_at: datetime | None = None,
    ) -> ObservationExtractionRun:
        """Persist a terminal or retryable failure without usable output."""

        run.status = "failed"
        run.validated_output = None
        run.failure_metadata = failure_metadata
        run.failed_at = failed_at or datetime.now(UTC)
        run.completed_at = None
        await self._session.flush()
        return run

    async def set_preprocessing_manifest(
        self,
        run: ObservationExtractionRun,
        *,
        manifest: list[dict[str, Any]],
    ) -> ObservationExtractionRun:
        """Persist byte-free preprocessing outcomes before provider access."""

        run.preprocessing_manifest = manifest
        await self._session.flush()
        return run

    async def append_attempt(
        self,
        *,
        run_id: str,
        provider: str,
        model: str,
        started_at: datetime | None = None,
    ) -> ObservationExtractionAttempt:
        """Atomically append the provider call that is about to begin.

        The first attempt is allowed only from a fresh pending run. Later
        attempts require an explicit retryable failure and must precede the
        durable diagnostic-agent-start marker.
        """

        run = await self._get_for_update(run_id)
        if run is None:
            raise ValueError("observation extraction run does not exist")
        is_initial_attempt = run.status == "pending" and run.provider_attempt_count == 0
        is_eligible_recovery = (
            run.status == "failed"
            and run.diagnostic_agent_started_at is None
            and bool((run.failure_metadata or {}).get("retryable"))
        )
        if not (is_initial_attempt or is_eligible_recovery):
            raise ValueError("observation extraction attempt is not eligible")

        attempt = ObservationExtractionAttempt(
            run_id=run.id,
            attempt_number=run.provider_attempt_count + 1,
            provider=provider,
            model=model,
            outcome="pending",
            started_at=started_at or datetime.now(UTC),
        )
        self._session.add(attempt)
        run.provider_attempt_count += 1
        if is_eligible_recovery:
            run.status = "pending"
            run.failure_metadata = None
            run.failed_at = None
        await self._session.flush()
        return attempt

    async def mark_diagnostic_agent_started(
        self,
        run: ObservationExtractionRun,
        *,
        started_at: datetime | None = None,
    ) -> ObservationExtractionRun:
        """Set the write-once gate that forbids later extraction retries."""

        if run.diagnostic_agent_started_at is None:
            run.diagnostic_agent_started_at = started_at or datetime.now(UTC)
            await self._session.flush()
        return run

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
    ) -> ObservationExtractionAttempt:
        """Record a completed or failed provider attempt without raw output."""

        if outcome not in {"completed", "failed"}:
            raise ValueError("attempt outcome must be completed or failed")
        attempt.outcome = outcome
        attempt.failure_metadata = failure_metadata
        attempt.latency_ms = latency_ms
        attempt.input_tokens = input_tokens
        attempt.output_tokens = output_tokens
        attempt.provider_cost_microunits = provider_cost_microunits
        attempt.completed_at = completed_at or datetime.now(UTC)
        run = await self._get_for_update(attempt.run_id)
        if run is None:
            raise ValueError("observation extraction run does not exist")
        run.provider_latency_ms += latency_ms or 0
        if input_tokens is not None:
            run.input_tokens = (run.input_tokens or 0) + input_tokens
        if output_tokens is not None:
            run.output_tokens = (run.output_tokens or 0) + output_tokens
        if provider_cost_microunits is not None:
            run.provider_cost_microunits = (
                run.provider_cost_microunits or 0
            ) + provider_cost_microunits
        await self._session.flush()
        return attempt

    async def redact(
        self,
        run: ObservationExtractionRun,
        *,
        reason: str,
        redacted_at: datetime | None = None,
    ) -> ObservationExtractionRun:
        """Irreversibly remove artifact-derived evidence from a run."""

        run.redacted_at = redacted_at or datetime.now(UTC)
        run.redaction_reason = reason
        run.validated_output = None
        run.preprocessing_manifest = []
        await self._session.flush()
        return run

    async def list_usable_for_session(
        self,
        repair_session_id: str,
        *,
        limit: int = 50,
    ) -> list[ObservationExtractionRun]:
        """Return completed, non-redacted evidence available to later turns."""

        result = await self._session.execute(
            select(ObservationExtractionRun)
            .where(
                ObservationExtractionRun.repair_session_id == repair_session_id,
                ObservationExtractionRun.image_analysis_mode == "enabled",
                ObservationExtractionRun.status == "completed",
                ObservationExtractionRun.redacted_at.is_(None),
            )
            .order_by(
                ObservationExtractionRun.created_at.asc(),
                ObservationExtractionRun.id.asc(),
            )
            .limit(limit),
        )
        return list(result.scalars().all())

    async def _get_for_update(
        self,
        run_id: str,
    ) -> ObservationExtractionRun | None:
        result = await self._session.execute(
            select(ObservationExtractionRun)
            .where(ObservationExtractionRun.id == run_id)
            .with_for_update(),
        )
        return result.scalar_one_or_none()
