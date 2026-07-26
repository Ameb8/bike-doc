"""Internal artifact lifecycle invalidation seam tests."""

from __future__ import annotations

import pytest

from bike_doc_api.services.diagnostic_evidence_invalidation import (
    DiagnosticEvidenceInvalidationService,
)


class _Runs:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    async def redact_citing_artifact(self, *, artifact_id: str, reason: str) -> int:
        self.calls.append((artifact_id, reason))
        return 2


class _Reports:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    async def invalidate_citing_artifact(self, *, artifact_id: str, reason: str) -> int:
        self.calls.append((artifact_id, reason))
        return 1


@pytest.mark.asyncio
async def test_invalidating_one_artifact_redacts_every_citing_evidence_kind() -> None:
    runs = _Runs()
    reports = _Reports()
    service = DiagnosticEvidenceInvalidationService(runs=runs, reports=reports)

    await service.invalidate_artifact(artifact_id="art_one", reason="retention_expired")

    assert runs.calls == [("art_one", "retention_expired")]
    assert reports.calls == [("art_one", "retention_expired")]
