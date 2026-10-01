"""Seeded diagnostic role on real PostgreSQL/JetStream, durable ADK and fake model."""

import json
import os
import uuid
from collections.abc import AsyncGenerator, AsyncIterator

import pytest
from google.adk.models.base_llm import BaseLlm
from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse
from google.genai import types
from pydantic import PrivateAttr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from bike_doc_api.adk.execution import DiagnosticResources
from bike_doc_api.adk.resources import InactiveObservationExtractor
from bike_doc_api.adk.sessions import DIAGNOSTIC_ADK_APP_NAME, DIAGNOSTIC_ADK_USER_ID
from bike_doc_api.adk.storage import open_adk_session_service
from bike_doc_api.api.v1.turns import get_turn_service
from bike_doc_api.core.config import Settings
from bike_doc_api.models.artifact import ArtifactRef
from bike_doc_api.models.background_job import BackgroundJob
from bike_doc_api.models.bike import BikeProfile
from bike_doc_api.models.event import RepairSessionEvent
from bike_doc_api.models.repair_session import RepairPhaseSession, RepairSession
from bike_doc_api.models.user import User
from bike_doc_api.providers.storage import LocalStorageProvider
from bike_doc_api.repositories.background_jobs import BackgroundJobRepository, JobRecord
from bike_doc_api.schemas.turn import TurnCreate
from bike_doc_api.workers.adapters import NatsDelivery
from bike_doc_api.workers.diagnostic_role import diagnostic_worker

DB_URL = os.getenv("BIKE_DOC_API_MAINTENANCE_TEST_DATABASE_URL")
NATS_URL = os.getenv("BIKE_DOC_API_MAINTENANCE_TEST_NATS_URL")
pytestmark = [
    pytest.mark.nats,
    pytest.mark.skipif(
        not DB_URL or not NATS_URL,
        reason="requires disposable migrated PostgreSQL/JetStream",
    ),
]


class FakeModel(BaseLlm):
    _requests: list[str] = PrivateAttr(default_factory=list)
    _fail: bool = PrivateAttr(default=False)

    async def generate_content_async(
        self, llm_request: LlmRequest, stream: bool = False
    ) -> AsyncGenerator[LlmResponse, None]:
        self._requests.append(llm_request.model_dump_json())
        if self._fail:
            raise RuntimeError("private provider response; must never reach job state")
        yield LlmResponse(
            content=types.Content(
                role="model",
                parts=[types.Part(text="Please describe when the noise happens.")],
            )
        )


@pytest.fixture
async def context(tmp_path) -> AsyncIterator[tuple]:
    suffix = uuid.uuid4().hex[:12]
    settings = Settings(
        environment="test",
        database_url=DB_URL,
        nats_url=NATS_URL,
        nats_work_stream=f"DIAGNOSTIC_{suffix}",
        nats_profile_subject=f"diagnostic.{suffix}.profile",
        nats_diagnostic_subject=f"diagnostic.{suffix}.diagnostic",
        nats_profile_consumer=f"profile_{suffix}",
        nats_diagnostic_consumer=f"diagnostic_{suffix}",
        profile_inference_execution="legacy",
        image_analysis_mode="off",
        artifact_local_storage_root=tmp_path,
    )
    engine = create_async_engine(DB_URL)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    adk = await open_adk_session_service(settings, pool_size=4)
    async with sessions() as session, session.begin():
        user = User(
            auth_subject=suffix, email="worker@example.test", display_name="Test"
        )
        session.add(user)
        await session.flush()
        bike = BikeProfile(user_id=user.id, display_name="Before acceptance")
        session.add(bike)
        await session.flush()
        repair = RepairSession(user_id=user.id, bike_id=bike.id)
        session.add(repair)
        await session.flush()
        user_id, bike_id, repair_id = user.id, bike.id, repair.id
        artifact = ArtifactRef(
            user_id=user_id,
            repair_session_id=repair_id,
            purpose="diagnostic_photo",
            media_type="image",
            mime_type="image/jpeg",
            filename="private.jpg",
            byte_size=18,
            status="ready",
            content_sha256="a" * 64,
            storage_provider="local",
            storage_path="private/artifact/path.jpg",
        )
        session.add(artifact)
        await session.flush()
        artifact_id = artifact.id
    async with sessions() as session:
        accepted = await get_turn_service(session, settings).accept_turn(
            current_user=await session.get(User, user_id),
            repair_session_id=repair_id,
            request=TurnCreate.model_validate(
                {
                    "schema_version": "ai_turn.v1",
                    "client_turn_id": suffix,
                    "message": {
                        "text": "private accepted chain skips",
                        "artifact_ids": [artifact_id],
                    },
                }
            ),
        )
        # The producer remains absent. Seed one strict job only for this role test.
        assert not list(
            await session.scalars(
                select(BackgroundJob).where(
                    BackgroundJob.input["turn_id"].astext == accepted.turn_id
                )
            )
        )
        job = await BackgroundJobRepository(session).record(
            JobRecord(
                "diagnostic_turn",
                "diagnostic",
                1,
                {"turn_id": accepted.turn_id},
                f"diagnostic:{suffix}",
                3,
            )
        )
        await session.commit()
    async with sessions() as session, session.begin():
        bike = await session.get(BikeProfile, bike_id)
        bike.display_name = "Current authoritative bike"
    model = FakeModel(model="fake-diagnostic")
    resources = DiagnosticResources(
        adk, LocalStorageProvider(tmp_path), InactiveObservationExtractor(), model=model
    )
    try:
        yield settings, sessions, job, repair_id, model, resources, artifact_id
    finally:
        await adk.close()
        await engine.dispose()


@pytest.mark.parametrize("fail_provider", [False, True])
async def test_seeded_role_commits_product_before_ack_and_duplicate_is_noop(
    context: tuple, fail_provider: bool, caplog: pytest.LogCaptureFixture
) -> None:
    settings, sessions, job, repair_id, model, resources, artifact_id = context
    model._fail = fail_provider
    async with diagnostic_worker(settings, resources=resources) as runtime:
        client = runtime.transport.client

        async def notify():
            await client.jetstream().publish(
                runtime.options.subject,
                json.dumps(
                    {"version": 1, "job_id": job.id, "publication_generation": 1}
                ).encode(),
            )
            return NatsDelivery(
                (await runtime.transport.subscription.fetch(1, timeout=3))[0]
            )

        await runtime.process(await notify())
        async with sessions() as session:
            row = await session.get(BackgroundJob, job.id)
            assert row.state == "succeeded" and row.attempt_count == 1
            assert row.latest_error_category is None
            phase = (
                await session.scalars(
                    select(RepairPhaseSession).where(
                        RepairPhaseSession.repair_session_id == repair_id
                    )
                )
            ).one()
            assert phase.adk_session_id == f"adk_diagnostic_{phase.id}"
            repair = await session.get(RepairSession, repair_id)
            assert repair.status == "awaiting_user"
            events = list(
                await session.scalars(
                    select(RepairSessionEvent)
                    .where(RepairSessionEvent.turn_id == job.input["turn_id"])
                    .order_by(RepairSessionEvent.sequence)
                )
            )
            assert (
                events[0].type == "turn.started" and events[-1].type == "turn.completed"
            )
            if fail_provider:
                assert any(event.type == "error" for event in events)
                assert "private provider response" not in repr(
                    [event.data for event in events]
                )
        await runtime.process(await notify())
        assert len(model._requests) == 1
        assert "private accepted chain skips" in model._requests[0]
        stored = await resources.session_service.get_session(
            app_name=DIAGNOSTIC_ADK_APP_NAME,
            user_id=DIAGNOSTIC_ADK_USER_ID,
            session_id=phase.adk_session_id,
        )
        assert (
            stored.state["app_context"]["bike_profile"]["display_name"]
            == "Current authoritative bike"
        )
        assert stored.state["app_context"]["repair_history"] == []
        assert stored.state["app_context"]["artifact_ids"] == [artifact_id]
        info = await client.jetstream().consumer_info(
            settings.nats_work_stream, settings.nats_diagnostic_consumer
        )
        assert info.num_ack_pending == 0
        # The independent profile consumer receives no diagnostic delivery.
        profile_info = await client.jetstream().consumer_info(
            settings.nats_work_stream, settings.nats_profile_consumer
        )
        assert profile_info.num_pending == 0
    with pytest.raises(RuntimeError, match="worker is closed"):
        runtime.start()
    assert client.is_closed
    assert "private provider response" not in caplog.text
    assert "private accepted chain skips" not in caplog.text
    assert "private/artifact/path.jpg" not in caplog.text
    assert stored.state["app_context"]["user_id"] not in caplog.text


async def test_confirmed_missing_binding_is_pre_effect_failure(context: tuple) -> None:
    settings, sessions, job, repair_id, model, resources, _artifact_id = context
    async with sessions() as session, session.begin():
        phase = (
            await session.scalars(
                select(RepairPhaseSession).where(
                    RepairPhaseSession.repair_session_id == repair_id
                )
            )
        ).one()
        phase.adk_session_id = "confirmed_missing_bound_session"
    async with diagnostic_worker(settings, resources=resources) as runtime:
        await runtime.transport.client.jetstream().publish(
            runtime.options.subject,
            json.dumps(
                {"version": 1, "job_id": job.id, "publication_generation": 1}
            ).encode(),
        )
        delivery = NatsDelivery(
            (await runtime.transport.subscription.fetch(1, timeout=3))[0]
        )
        await runtime.process(delivery)
        async with sessions() as session:
            row = await session.get(BackgroundJob, job.id)
            assert (
                row.state == "dead" and row.latest_error_category == "permanent_failure"
            )
            phase = (
                await session.scalars(
                    select(RepairPhaseSession).where(
                        RepairPhaseSession.repair_session_id == repair_id
                    )
                )
            ).one()
            assert phase.adk_session_id == "confirmed_missing_bound_session"
        assert model._requests == []
