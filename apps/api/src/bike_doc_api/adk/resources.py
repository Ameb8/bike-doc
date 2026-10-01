"""Closeable diagnostic transports owned by the external process host."""

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import AsyncExitStack, asynccontextmanager

import structlog
from google import genai
from google.adk.models.google_llm import Gemini
from google.genai import types
from pydantic import PrivateAttr

from bike_doc_api.adk.execution import DiagnosticResources
from bike_doc_api.adk.storage import open_adk_session_service
from bike_doc_api.adk.telemetry import (
    validate_diagnostic_telemetry_runtime_configuration,
)
from bike_doc_api.core.config import (
    Settings,
    validate_diagnostic_runtime_configuration,
    validate_observation_extraction_runtime_configuration,
    validate_price_lookup_runtime_configuration,
)
from bike_doc_api.providers.observation_extraction import (
    GeminiDiagnosticObservationExtractor,
)
from bike_doc_api.providers.price import GeminiGroundedPriceProvider
from bike_doc_api.providers.storage import (
    GCSStorageProvider,
    LocalStorageProvider,
    StorageProvider,
)
from bike_doc_api.providers.storage.base import StoredObject
from bike_doc_api.services.cost_estimates import CostEstimateService
from bike_doc_api.services.observation_extraction import (
    ObservationExtractionRequest,
    ObservationExtractionResult,
)

logger = structlog.get_logger(__name__)


class ProcessGemini(Gemini):
    """ADK's existing model adapter over one bounded, process-owned SDK client."""

    _client: genai.Client = PrivateAttr()

    def __init__(self, *, model: str, client: genai.Client) -> None:
        super().__init__(model=model)
        self._client = client

    @property
    def api_client(self) -> genai.Client:
        return self._client


class InactiveObservationExtractor:
    provider = "unavailable"
    model = "unavailable"

    async def extract(
        self, request: ObservationExtractionRequest
    ) -> ObservationExtractionResult:
        raise RuntimeError("observation extraction unavailable")


class BoundedStorage:
    def __init__(self, storage: StorageProvider, timeout: float) -> None:
        self.storage, self.timeout = storage, timeout
        self.provider_name = storage.provider_name

    async def get_object(self, *, path: str, bucket: str | None) -> bytes:
        async with asyncio.timeout(self.timeout):
            return await self.storage.get_object(path=path, bucket=bucket)

    async def put_object(
        self,
        *,
        object_name: str,
        content: bytes,
        content_type: str,
        content_sha256: str,
    ) -> StoredObject:
        async with asyncio.timeout(self.timeout):
            return await self.storage.put_object(
                object_name=object_name,
                content=content,
                content_type=content_type,
                content_sha256=content_sha256,
            )


async def bounded_close(close: Callable[[], Awaitable[None]]) -> None:
    async with asyncio.timeout(5):
        await close()


@asynccontextmanager
async def diagnostic_resources(
    settings: Settings,
) -> AsyncIterator[DiagnosticResources]:
    validate_diagnostic_telemetry_runtime_configuration(settings)
    validate_diagnostic_runtime_configuration(settings)
    if settings.artifact_storage_provider == "gcs" and not settings.artifact_gcs_bucket:
        raise ValueError("diagnostic storage bucket required")
    async with AsyncExitStack() as stack:
        adk = await open_adk_session_service(
            settings,
            pool_size=settings.diagnostic_worker_adk_pool_size,
        )
        stack.push_async_callback(bounded_close, adk.close)
        client = genai.Client(
            vertexai=settings.diagnostic_llm_provider == "vertex_ai",
            http_options=types.HttpOptions(
                timeout=int(settings.diagnostic_agent_timeout_seconds * 1000),
                retry_options=types.HttpRetryOptions(attempts=1),
            ),
        )

        async def close_client() -> None:
            try:
                await client.aio.aclose()
            finally:
                await asyncio.to_thread(client.close)

        stack.push_async_callback(bounded_close, close_client)
        storage = (
            LocalStorageProvider(settings.artifact_local_storage_root)
            if settings.artifact_storage_provider == "local"
            else GCSStorageProvider(bucket_name=settings.artifact_gcs_bucket or "")
        )
        if isinstance(storage, GCSStorageProvider):
            stack.push_async_callback(bounded_close, storage.close)
        observation = InactiveObservationExtractor()
        extractor: GeminiDiagnosticObservationExtractor | None = None
        if settings.image_analysis_mode in {"shadow", "enabled"}:
            try:
                validate_observation_extraction_runtime_configuration(settings)
                factory = (
                    GeminiDiagnosticObservationExtractor.from_vertex_ai
                    if settings.observation_extraction_llm_provider == "vertex_ai"
                    else GeminiDiagnosticObservationExtractor.from_google_ai
                )
                extractor = factory(
                    model=settings.observation_extraction_model,
                    timeout_seconds=settings.observation_extraction_timeout_seconds,
                )
            except Exception:
                logger.warning("diagnostic_observation_provider_unavailable")
            if extractor is not None:
                stack.push_async_callback(bounded_close, extractor.close)
        cost = None
        if settings.price_lookup_provider != "unavailable":
            try:
                validate_price_lookup_runtime_configuration(settings)
                price_factory = (
                    GeminiGroundedPriceProvider.from_vertex_ai
                    if settings.price_lookup_llm_provider == "vertex_ai"
                    else GeminiGroundedPriceProvider.from_google_ai
                )
                price = price_factory(
                    model=settings.price_lookup_model,
                    temperature=settings.price_lookup_temperature,
                    max_output_tokens=settings.price_lookup_max_output_tokens,
                    timeout_seconds=settings.price_lookup_timeout_seconds,
                )
                stack.push_async_callback(bounded_close, price.close)
                cost = CostEstimateService(price)
            except Exception:
                logger.warning("diagnostic_cost_estimate_unavailable")
        yield DiagnosticResources(
            adk,
            BoundedStorage(storage, settings.diagnostic_worker_storage_seconds),
            extractor or observation,
            cost,
            ProcessGemini(
                model=settings.diagnostic_agent_model,
                client=client,
            ),
        )
