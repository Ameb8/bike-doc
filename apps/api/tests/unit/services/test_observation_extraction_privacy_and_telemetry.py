"""Privacy and telemetry boundary tests for diagnostic image extraction."""

from __future__ import annotations

import pytest

from bike_doc_api.services.observation_extraction_privacy import (
    ObservationExtractionPrivacyError,
    validate_privacy_safe_observation_output,
)
from bike_doc_api.services.observation_extraction_telemetry import (
    RecordingObservationExtractionTelemetry,
)


def test_privacy_boundary_rejects_personal_scene_data_without_redaction() -> None:
    output = {"observations": [{"finding": "Serial number AB1234567890 is visible"}]}

    with pytest.raises(ObservationExtractionPrivacyError):
        validate_privacy_safe_observation_output(output)


def test_recording_telemetry_drops_sensitive_media_and_storage_values() -> None:
    telemetry = RecordingObservationExtractionTelemetry()
    secret = "BASE64_MEDIA_AABBCC location 49.123,-123.123 private/bucket.jpg"

    telemetry.event(
        "observation_extraction_completed",
        fields={
            "provider": "google_ai",
            "model": "gemini-2.5-flash",
            "schema_version": "visual-observation.v1",
            "observation_count": 2,
            "artifact_id": "art_sensitive",
            "storage_path": secret,
            "raw_response": secret,
        },
    )

    record = telemetry.records[0]
    assert record.fields == {
        "provider": "google_ai",
        "model": "gemini-2.5-flash",
        "schema_version": "visual-observation.v1",
        "observation_count": 2,
    }
    assert secret not in repr(telemetry.records)
