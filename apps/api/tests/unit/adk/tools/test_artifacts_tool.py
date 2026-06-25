"""list_diagnostic_artifacts ADK tool tests."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

from bike_doc_api.adk.tools.artifacts import ListDiagnosticArtifactsTool
from bike_doc_api.adk.tools.common import DiagnosticToolContext
from bike_doc_api.core.errors import NotFoundError, SessionStateConflictError


class _ArtifactService:
    """Fake artifact service for tool tests."""

    def __init__(self, *, error: Exception | None = None) -> None:
        self.error = error
        self.calls: list[dict[str, Any]] = []

    async def list_diagnostic_artifacts(self, **kwargs: Any) -> list[Any]:
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return [
            SimpleNamespace(
                id="art_1",
                user_id="usr_hidden",
                repair_session_id="rs_tool",
                purpose="diagnostic_photo",
                media_type="image",
                mime_type="image/jpeg",
                filename="derailleur.jpg",
                byte_size=123,
                status="ready",
                width=1600,
                height=1200,
                duration_seconds=None,
                rejection_reason=None,
                created_at=datetime(2026, 6, 21, 17, 3, tzinfo=UTC),
                storage_path="must/not/leak",
            ),
        ]


def _context() -> DiagnosticToolContext:
    return DiagnosticToolContext(
        user_id="usr_tool",
        repair_session_id="rs_tool",
        diagnostic_session_id="phs_tool",
    )


async def test_list_diagnostic_artifacts_returns_metadata_only() -> None:
    service = _ArtifactService()

    result = await ListDiagnosticArtifactsTool(service).run(
        {"repair_session_id": "rs_tool"},
        _context(),
    )

    artifact = result["data"]["artifacts"][0]
    assert result["ok"] is True
    assert artifact["id"] == "art_1"
    assert artifact["purpose"] == "diagnostic_photo"
    assert "storage_path" not in artifact
    assert service.calls[0]["purpose"] == "diagnostic_photo"


async def test_list_diagnostic_artifacts_rejects_unsupported_purpose() -> None:
    service = _ArtifactService()

    result = await ListDiagnosticArtifactsTool(service).run(
        {"repair_session_id": "rs_tool", "purpose": "verification_photo"},
        _context(),
    )

    assert result["ok"] is False
    assert result["error"]["code"] == "validation_error"
    assert service.calls == []


async def test_list_diagnostic_artifacts_rejects_context_mismatch() -> None:
    service = _ArtifactService()

    result = await ListDiagnosticArtifactsTool(service).run(
        {"repair_session_id": "rs_other"},
        _context(),
    )

    assert result["error"]["code"] == "validation_error"
    assert service.calls == []


async def test_list_diagnostic_artifacts_maps_domain_errors() -> None:
    missing = await ListDiagnosticArtifactsTool(
        _ArtifactService(error=NotFoundError()),
    ).run({"repair_session_id": "rs_tool"}, _context())
    invalid_phase = await ListDiagnosticArtifactsTool(
        _ArtifactService(error=SessionStateConflictError()),
    ).run({"repair_session_id": "rs_tool"}, _context())

    assert missing["error"]["code"] == "not_found"
    assert invalid_phase["error"]["code"] == "invalid_phase"
