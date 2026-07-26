"""Internal artifact-lifecycle hook for invalidating derived visual evidence."""

from __future__ import annotations

from typing import Protocol


class CitingObservationRunRepository(Protocol):
    async def redact_citing_artifact(self, *, artifact_id: str, reason: str) -> int: ...


class CitingReportRepository(Protocol):
    async def invalidate_citing_artifact(
        self, *, artifact_id: str, reason: str
    ) -> int: ...


class DiagnosticEvidenceInvalidationService:
    """Redact all visual evidence derived from one inaccessible artifact.

    This is deliberately an internal seam for future retention/deletion callers;
    it does not create a public artifact deletion API.
    """

    def __init__(
        self, *, runs: CitingObservationRunRepository, reports: CitingReportRepository
    ) -> None:
        self._runs = runs
        self._reports = reports

    async def invalidate_artifact(self, *, artifact_id: str, reason: str) -> None:
        if not artifact_id or not reason:
            raise ValueError("artifact invalidation requires an artifact ID and reason")
        await self._runs.redact_citing_artifact(artifact_id=artifact_id, reason=reason)
        await self._reports.invalidate_citing_artifact(
            artifact_id=artifact_id, reason=reason
        )
