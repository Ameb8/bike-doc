"""Gemini adapter for isolated, strict bike-profile image extraction."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable
from typing import Any, Protocol

from google import genai
from google.genai import types

from bike_doc_api.schemas.profile_inference import (
    ProfileInferenceOutput,
    ProfileInferenceRequest,
)
from bike_doc_api.services.profile_registry import get_canonical_field_definition

_SYSTEM_INSTRUCTION = """
You extract only durable installed bicycle configuration evidence from
user-submitted bicycle images. For drivetrain evidence, extract only the
installed topology: drivetrain.architecture, drivetrain.drive_medium, and the
presence of the listed canonical drivetrain component roles. Do not infer
counts, tooth values, manufacturer/model, interfaces, or dimensions.
Return one JSON object matching the provided schema exactly. You may emit
claims only for the allowed field paths. Abstain whenever installedness,
position/scope, or visibility cannot be directly supported.
Loose components, packaging, reference images, another bike, and ambiguous
target relations must not be described as installed on the target bike.
Never return diagnostics, condition assessments, repair advice, personal data,
or hidden reasoning. The caption can clarify installedness but cannot create a
claim without visual image evidence.
""".strip()


class _GeminiGenerateContent(Protocol):
    def __call__(
        self,
        *,
        model: str,
        contents: Any,
        config: types.GenerateContentConfig,
    ) -> Awaitable[Any]:
        """Generate one structured multimodal response."""


class GeminiProfileInferenceExtractor:
    """Perform one provider-isolated structured multimodal extraction."""

    def __init__(
        self,
        *,
        model: str,
        timeout_seconds: float,
        generate_content: _GeminiGenerateContent,
    ) -> None:
        self._model = model
        self._timeout_seconds = timeout_seconds
        self._generate_content = generate_content
        self.provider = "gemini"
        self.last_usage: dict[str, int | float] | None = None

    @classmethod
    def from_google_ai(
        cls,
        *,
        model: str,
        timeout_seconds: float,
    ) -> GeminiProfileInferenceExtractor:
        """Build a Google AI Studio backed extractor."""

        client = genai.Client()
        return cls(
            model=model,
            timeout_seconds=timeout_seconds,
            generate_content=client.aio.models.generate_content,
        )

    @classmethod
    def from_vertex_ai(
        cls,
        *,
        model: str,
        timeout_seconds: float,
    ) -> GeminiProfileInferenceExtractor:
        """Build a Vertex AI backed extractor from standard runtime settings."""

        client = genai.Client(vertexai=True)
        return cls(
            model=model,
            timeout_seconds=timeout_seconds,
            generate_content=client.aio.models.generate_content,
        )

    async def extract(self, request: ProfileInferenceRequest) -> dict[str, Any]:
        """Issue exactly one structured call with no profile or diagnostic history."""

        self.last_usage = None
        response = await asyncio.wait_for(
            self._generate_content(
                model=self._model,
                contents=_contents(request),
                config=types.GenerateContentConfig(
                    system_instruction=_SYSTEM_INSTRUCTION,
                    temperature=0,
                    response_mime_type="application/json",
                    response_schema=ProfileInferenceOutput,
                ),
            ),
            timeout=self._timeout_seconds,
        )
        self.last_usage = _usage_metadata(response)
        return _raw_response_object(response)


def _contents(request: ProfileInferenceRequest) -> list[Any]:
    """Build the minimal provider input allowed by the inference specification."""

    metadata = {
        "bike_id": request.bike_id,
        "repair_session_id": request.repair_session_id,
        "caption": request.caption,
        "schema_version": request.schema_version,
        "field_registry": {
            "allowed_field_paths": request.allowed_field_paths,
            "fields": {
                field_path: _field_contract(field_path)
                for field_path in request.allowed_field_paths
            },
        },
        "images": [
            {"artifact_id": image.artifact_id, "mime_type": image.mime_type}
            for image in request.images
        ],
    }
    return [
        json.dumps(metadata, sort_keys=True),
        *[
            types.Part.from_bytes(data=image.content, mime_type=image.mime_type)
            for image in request.images
        ],
    ]


def _field_contract(field_path: str) -> dict[str, Any]:
    """Expose only registry facts needed to constrain structured extraction."""

    field = get_canonical_field_definition(field_path)
    contract: dict[str, Any] = {
        "value_kind": field.value_kind,
        "scope": field.scope,
        "permitted_evidence_bases": sorted(field.permitted_evidence_bases),
    }
    if field.enum_values:
        contract["allowed_values"] = sorted(field.enum_values)
    return contract


def _raw_response_object(response: Any) -> dict[str, Any]:
    """Accept only a single JSON object; do not normalize model drift."""

    parsed = getattr(response, "parsed", None)
    if isinstance(parsed, ProfileInferenceOutput):
        return parsed.model_dump(mode="json")
    if isinstance(parsed, dict):
        return parsed
    text = getattr(response, "text", None)
    if not isinstance(text, str) or not text.strip():
        raise ValueError("profile inference returned no JSON object")
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError("profile inference returned invalid JSON") from exc
    if not isinstance(data, dict):
        raise ValueError("profile inference must return a JSON object")
    return data


def _usage_metadata(response: Any) -> dict[str, int | float] | None:
    """Extract only numeric provider usage fields for operational metrics."""

    usage = getattr(response, "usage_metadata", None)
    if usage is None:
        return None
    values: dict[str, int | float] = {}
    for source, target in (
        ("prompt_token_count", "input_tokens"),
        ("candidates_token_count", "output_tokens"),
        ("total_token_count", "total_tokens"),
        ("cost_usd", "cost_usd"),
    ):
        value = getattr(usage, source, None)
        if isinstance(value, (int, float)):
            values[target] = value
    return values or None
