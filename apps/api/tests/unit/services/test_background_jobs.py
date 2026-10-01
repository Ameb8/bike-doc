"""Strict internal input and producer contract tests without infrastructure."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from bike_doc_api.repositories.background_jobs import (
    ExecutionPolicy,
    JobOutcome,
    JobRecord,
    JobSnapshot,
)
from bike_doc_api.schemas.background_jobs import ProfileInferenceInputV1
from bike_doc_api.services.background_jobs import BackgroundJobService

VALID = {
    "turn_id": "turn_01K00000000000000000000000",
    "inference_schema_version": "bike_profile_inference.v1",
    "extractor_version": "drivetrain-specifications.v1",
}


def test_profile_input_has_exact_pinned_immutable_fields() -> None:
    model = ProfileInferenceInputV1.model_validate(VALID)
    assert model.model_dump() == VALID
    assert (
        model.deduplication_key()
        == ProfileInferenceInputV1.model_validate(
            dict(reversed(list(VALID.items())))
        ).deduplication_key()
    )
    with pytest.raises(ValidationError):
        model.turn_id = VALID["turn_id"]


@pytest.mark.parametrize("field", list(VALID))
@pytest.mark.parametrize(
    "invalid", ["", " ", "user text here", "../artifact", "key\nsecret", 42, None]
)
def test_profile_input_rejects_blank_malformed_and_non_string_identifiers(
    field: str, invalid: object
) -> None:
    with pytest.raises(ValidationError):
        ProfileInferenceInputV1.model_validate({**VALID, field: invalid})


@pytest.mark.parametrize("field", list(VALID))
def test_profile_input_requires_every_pinned_identifier(field: str) -> None:
    with pytest.raises(ValidationError):
        ProfileInferenceInputV1.model_validate(
            {k: v for k, v in VALID.items() if k != field}
        )


@pytest.mark.parametrize(
    "extra", ["artifact_id", "bike_id", "user_id", "prompt", "job_kind"]
)
def test_profile_input_forbids_additional_data(extra: str) -> None:
    with pytest.raises(ValidationError):
        ProfileInferenceInputV1.model_validate({**VALID, extra: "sensitive"})


def test_profile_identity_separates_pinned_versions() -> None:
    original = ProfileInferenceInputV1.model_validate(VALID)
    for field in ("inference_schema_version", "extractor_version"):
        changed = ProfileInferenceInputV1.model_validate(
            {**VALID, field: VALID[field] + "2"}
        )
        assert changed.deduplication_key() != original.deduplication_key()


class Recorder:
    def __init__(self) -> None:
        self.records: list[JobRecord] = []

    async def record(self, definition: JobRecord) -> JobSnapshot:
        self.records.append(definition)
        return JobSnapshot(
            id="job_01K00000000000000000000000",
            job_kind=definition.job_kind,
            workload_class=definition.workload_class,
            input_version=definition.input_version,
            input=definition.input,
            state="queued",
            attempt_count=0,
            attempt_limit=definition.attempt_limit,
            desired_generation=1,
            confirmed_generation=0,
            eligible_at=datetime.now(UTC),
            execution_token=None,
            execution_deadline=None,
            effect_boundary_at=None,
            latest_error_category=None,
        )


async def test_service_records_only_pinned_input_and_validated_identity() -> None:
    recorder = Recorder()
    model = ProfileInferenceInputV1.model_validate(VALID)
    job = await BackgroundJobService(recorder).record_profile_inference(
        model, attempt_limit=3, deduplication_key=model.deduplication_key()
    )
    assert job.input == VALID
    assert recorder.records[0].deduplication_key == model.deduplication_key()
    assert job.input_version == 1
    assert job.job_kind == "profile_inference"
    assert job.workload_class == "profile_inference"


@pytest.mark.parametrize(
    "kwargs",
    [
        {"attempt_limit": 0},
        {"attempt_limit": 6},
        {"attempt_limit": True},
        {"attempt_limit": 3, "deduplication_key": "forged"},
    ],
)
async def test_service_rejects_invalid_definition_before_persistence(
    kwargs: dict[str, object],
) -> None:
    recorder = Recorder()
    with pytest.raises(ValueError):
        await BackgroundJobService(recorder).record_profile_inference(
            ProfileInferenceInputV1.model_validate(VALID), **kwargs
        )
    assert recorder.records == []


def test_outcomes_and_execution_policy_reject_unbounded_or_raw_errors() -> None:
    with pytest.raises(ValueError):
        ExecutionPolicy(timedelta(0))
    with pytest.raises(ValueError):
        ExecutionPolicy(timedelta(days=8))
    with pytest.raises(ValueError):
        JobOutcome("retrying")
    with pytest.raises(ValueError):
        replace(JobOutcome("dead"), error="provider secret")
    with pytest.raises(ValueError):
        JobOutcome("succeeded", retry_after=timedelta(seconds=1))
