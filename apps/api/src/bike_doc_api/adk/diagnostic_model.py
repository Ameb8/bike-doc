"""Bound provider execution and redact failures before they enter ADK telemetry."""

import asyncio
from collections.abc import AsyncGenerator

from google.adk.models.base_llm import BaseLlm
from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse
from pydantic import PrivateAttr


class DiagnosticModelUnavailableError(Exception):
    """Fixed category safe for dependency logs and exception spans."""


class BoundedDiagnosticModel(BaseLlm):
    _delegate: BaseLlm = PrivateAttr()
    _timeout: float = PrivateAttr()

    def __init__(self, delegate: BaseLlm, timeout: float) -> None:
        super().__init__(model=delegate.model)
        self._delegate, self._timeout = delegate, timeout

    async def generate_content_async(
        self,
        llm_request: LlmRequest,
        stream: bool = False,
    ) -> AsyncGenerator[LlmResponse, None]:
        try:
            async with asyncio.timeout(self._timeout):
                async for response in self._delegate.generate_content_async(
                    llm_request, stream
                ):
                    yield response
        except Exception:
            # Dependency telemetry runs before the app runner normalizes errors.
            # Do not allow provider text or paths to enter that boundary.
            raise DiagnosticModelUnavailableError(
                "diagnostic_model_unavailable"
            ) from None
