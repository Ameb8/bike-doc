"""Profile audit lifecycle coupled to generic job transitions, never eligibility."""

from pydantic import ValidationError
from sqlalchemy import select

from bike_doc_api.models.background_job import BackgroundJob
from bike_doc_api.models.profile_inference import ProfileInferenceRun
from bike_doc_api.repositories.background_jobs import BackgroundJobRepository
from bike_doc_api.schemas.background_jobs import ProfileInferenceInputV1


class ProfileJobRepository(BackgroundJobRepository):
    async def _persist_transition(self, row: BackgroundJob) -> None:
        if row.job_kind == "profile_inference" and row.input_version == 1:
            try:
                instruction = ProfileInferenceInputV1.model_validate(row.input)
            except ValidationError:
                instruction = None
            if instruction is not None:
                run = (
                    await self._session.scalars(
                        select(ProfileInferenceRun)
                        .where(
                            ProfileInferenceRun.turn_id == instruction.turn_id,
                            ProfileInferenceRun.inference_schema_version
                            == instruction.inference_schema_version,
                            ProfileInferenceRun.extractor_version
                            == instruction.extractor_version,
                        )
                        .with_for_update()
                        .execution_options(populate_existing=True)
                    )
                ).first()
                if run is not None and run.status in {"started", "retryable_failure"}:
                    if row.state == "retrying":
                        status = "retryable_failure"
                    elif row.state == "succeeded":
                        status = "abstained"
                    elif row.latest_error_category == "attempts_exhausted":
                        status = "exhausted"
                    else:
                        status = "terminal_failure"
                    run.status = status
                    run.attempt_count = row.attempt_count
                    run.retry_count = max(0, row.attempt_count - 1)
                    run.max_attempts = row.attempt_limit
                    run.failure_class = "job"
                    run.failure_code = row.latest_error_category
                    run.completed_at = row.terminal_at
                    run.lifecycle_outcomes = [*run.lifecycle_outcomes, status][-32:]
        await super()._persist_transition(row)
