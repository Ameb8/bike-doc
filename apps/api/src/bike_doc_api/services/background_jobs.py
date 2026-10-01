"""Typed producer boundary for durable work, independent of HTTP and transport."""

from typing import Protocol

from bike_doc_api.repositories.background_jobs import JobRecord, JobSnapshot
from bike_doc_api.schemas.background_jobs import ProfileInferenceInputV1

PROFILE_INFERENCE_KIND = "profile_inference"
PROFILE_INFERENCE_WORKLOAD = "profile_inference"


class JobRecorder(Protocol):
    async def record(self, definition: JobRecord) -> JobSnapshot: ...


class BackgroundJobService:
    """Record strict instructions inside the product caller's transaction."""

    def __init__(self, repository: JobRecorder) -> None:
        self._repository = repository

    async def record_profile_inference(
        self,
        input: ProfileInferenceInputV1,
        *,
        attempt_limit: int,
        deduplication_key: str | None = None,
    ) -> JobSnapshot:
        if type(attempt_limit) is not int or not 1 <= attempt_limit <= 5:
            raise ValueError("profile attempt limit must be between 1 and 5")
        # Revalidate even models built with model_construct; never copy arbitrary
        # caller data into the generic persistence payload.
        validated = ProfileInferenceInputV1.model_validate(input.model_dump())
        identity = validated.deduplication_key()
        if deduplication_key is not None and deduplication_key != identity:
            raise ValueError(
                "profile deduplication identity does not match pinned tuple"
            )
        return await self._repository.record(
            JobRecord(
                job_kind=PROFILE_INFERENCE_KIND,
                workload_class=PROFILE_INFERENCE_WORKLOAD,
                input_version=1,
                input=validated.model_dump(mode="json"),
                deduplication_key=identity,
                attempt_limit=attempt_limit,
            )
        )
