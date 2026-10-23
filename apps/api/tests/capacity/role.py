"""Test-only composition: real roles and stores, deterministic external adapters."""

from __future__ import annotations

import asyncio
import json
import os
import signal
import sys
import time
from collections.abc import AsyncGenerator, AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import asdict
from pathlib import Path
from typing import TYPE_CHECKING, Any

from budget import PROVIDER, ROOT

if TYPE_CHECKING:
    from google.adk.models.base_llm import BaseLlm

    from bike_doc_api.core.config import Settings
    from bike_doc_api.core.queue_telemetry import Observation
    from bike_doc_api.schemas.profile_inference import ProfileInferenceRequest
    from bike_doc_api.workers.runtime import PullWorker


def control() -> dict[str, Any]:
    return json.loads((ROOT / "control.json").read_text())


def model() -> BaseLlm:
    from google.adk.models.base_llm import BaseLlm
    from google.adk.models.llm_request import LlmRequest
    from google.adk.models.llm_response import LlmResponse
    from google.genai import types

    class Model(BaseLlm):
        async def generate_content_async(
            self, llm_request: LlmRequest, stream: bool = False
        ) -> AsyncGenerator[LlmResponse, None]:
            options = control()
            await asyncio.sleep(options.get("diagnostic_delay", 0.1))
            if options.get("diagnostic_failure"):
                raise TimeoutError(PROVIDER)
            yield LlmResponse(
                content=types.Content(role="model", parts=[types.Part(text=PROVIDER)])
            )

    return Model(model="capacity-fake")


class Extractor:
    async def extract(self, request: ProfileInferenceRequest) -> dict[str, object]:
        options = control()
        await asyncio.sleep(options.get("profile_delay", 0.25))
        if options.get("profile_failure"):
            raise TimeoutError(PROVIDER)
        return {
            "schema_version": "bike_profile_inference.v1",
            "scene": {
                "contains_bicycle": True,
                "multiple_bicycles": False,
                "target_relation": "installed_on_target_bike",
                "confidence_score": 0.99,
            },
            "claims": [
                {
                    "field_path": "brakes.rear.mechanism",
                    "value": "disc",
                    "subject_relation": "installed_on_target_bike",
                    "evidence_basis": "direct_visual",
                    "visibility": "clear",
                    "confidence_score": 0.99,
                    "artifact_ids": [request.images[0].artifact_id],
                    "observed_text": PROVIDER,
                    "evidence_cues": ["A rotor is visible."],
                }
            ],
            "abstentions": [],
        }


async def worker(role: str, settings: Settings) -> None:
    from bike_doc_api.core.telemetry import initialize_telemetry
    from bike_doc_api.providers.storage import LocalStorageProvider
    from bike_doc_api.workers.adapters import NatsDelivery

    progress = NatsDelivery.progress

    async def record_progress(self: NatsDelivery) -> None:
        await progress(self)
        with (ROOT / f"progress-{role}-{os.uname().nodename}.jsonl").open(
            "a"
        ) as output:
            output.write(json.dumps({"time": time.time(), "role": role}) + "\n")

    NatsDelivery.progress = record_progress

    telemetry = initialize_telemetry(settings)
    storage = LocalStorageProvider(settings.artifact_local_storage_root)
    stop = asyncio.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        asyncio.get_running_loop().add_signal_handler(sig, stop.set)

    @asynccontextmanager
    async def composition() -> AsyncIterator[PullWorker]:
        if role == "diagnostic":
            from bike_doc_api.adk.execution import DiagnosticResources
            from bike_doc_api.adk.resources import InactiveObservationExtractor
            from bike_doc_api.adk.storage import open_adk_session_service
            from bike_doc_api.workers.diagnostic_role import diagnostic_worker

            adk = await open_adk_session_service(
                settings, pool_size=settings.diagnostic_worker_adk_pool_size
            )
            try:
                resources = DiagnosticResources(
                    adk,
                    storage,
                    InactiveObservationExtractor(),
                    model=model(),
                )
                async with diagnostic_worker(settings, resources=resources) as runtime:
                    yield runtime
            finally:
                await adk.close()
        else:
            from bike_doc_api.workers.profile_role import profile_worker

            async with profile_worker(
                settings, extractor=Extractor(), storage=storage
            ) as runtime:
                yield runtime

    try:
        async with composition() as runtime:
            runtime.start()
            await stop.wait()
    finally:
        await telemetry.shutdown()


def main() -> None:
    from bike_doc_api.operations.deployment import (
        instance_environment,
        validate_transport,
    )

    values = json.loads(Path("/run/secrets/role.json").read_text())
    validate_transport(values)
    os.environ.update(instance_environment(values, os.uname().nodename))
    # Production role secrets/permissions remain in use. Dev auth is confined
    # to this disposable topology with no public listener.
    os.environ["BIKE_DOC_API_ENVIRONMENT"] = "test"
    os.environ["BIKE_DOC_API_AUTH_MODE"] = "dev"
    os.environ["BIKE_DOC_API_LOG_FORMAT"] = "json"
    os.environ["OTEL_METRIC_EXPORT_INTERVAL"] = "1000"
    from bike_doc_api.core.config import get_settings
    from bike_doc_api.core.logging import configure_logging
    from bike_doc_api.core.queue_telemetry import queue_telemetry

    settings = get_settings()
    role = sys.argv[1]
    configure_logging(environment="test", log_level="INFO", log_format="json")
    if role == "driver":
        from driver import run

        asyncio.run(run(settings, sys.argv[2]))
        return
    # Tap the Task 4.2 observation seam for exact samples, retaining normal OTLP
    # export as independent privacy evidence. No content or job identities.
    # A restarted container retains its hostname, but starts fresh counters.
    target = ROOT / f"signals-{role}-{os.uname().nodename}-{time.time_ns()}.jsonl"
    original = queue_telemetry._sink
    output = target.open("a", buffering=65536)
    last_flush = time.monotonic()

    def record(observation: Observation) -> None:
        nonlocal last_flush
        output.write(json.dumps(dict(time=time.time(), **asdict(observation))) + "\n")
        now = time.monotonic()
        if now - last_flush >= 1:
            output.flush()
            last_flush = now
        original(observation)

    queue_telemetry._sink = record
    if role == "api":
        from bike_doc_api.core.event_notifications import NatsEventNotifications

        receive = NatsEventNotifications._receive

        async def receive_hint(self: NatsEventNotifications, message: Any) -> None:
            if not control().get("lose_hints"):
                await receive(self, message)

        NatsEventNotifications._receive = receive_hint
        import uvicorn

        try:
            uvicorn.run(
                "bike_doc_api.main:app",
                host="0.0.0.0",
                port=8000,
                access_log=False,
                timeout_graceful_shutdown=35,
            )
        finally:
            output.close()
    else:
        try:
            asyncio.run(worker(role, settings))
        finally:
            output.close()


if __name__ == "__main__":
    main()
