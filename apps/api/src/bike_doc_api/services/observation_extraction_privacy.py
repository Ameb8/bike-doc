"""Reject unsafe diagnostic-observation extractor output before persistence."""

from __future__ import annotations

import re
from typing import Any


class ObservationExtractionPrivacyError(ValueError):
    """The extractor proposed unrelated personal or scene information."""


_PROHIBITED = re.compile(
    r"\b(?:face|person|owner|home|address|location|latitude|longitude|gps|"
    r"license\s*plate|serial(?:\s*(?:number|no\.?))?|vin|email|phone)\b",
    re.IGNORECASE,
)
_SERIAL = re.compile(
    r"\b(?=[A-Z0-9]*\d)(?:[A-HJ-NPR-Z0-9]{11,17}|[A-Z0-9]{10,})\b", re.I
)
_EMAIL = re.compile(r"\b[^\s@]+@[^\s@]+\.[^\s@]+\b")
_PHONE = re.compile(r"(?:\+?\d[\d .()\-]{7,}\d)")
_COORDINATES = re.compile(r"\b[+-]?\d{1,2}\.\d{3,}\s*,\s*[+-]?\d{1,3}\.\d{3,}\b")


def validate_privacy_safe_observation_output(output: Any) -> None:
    """Reject the whole output instead of attempting unsafe partial redaction."""

    _walk_text(output)


def _walk_text(value: Any) -> None:
    if isinstance(value, str):
        if (
            _PROHIBITED.search(value)
            or _SERIAL.search(value)
            or _EMAIL.search(value)
            or _PHONE.search(value)
            or _COORDINATES.search(value)
        ):
            raise ObservationExtractionPrivacyError(
                "extractor output contains prohibited data"
            )
    elif isinstance(value, dict):
        for item in value.values():
            _walk_text(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            _walk_text(item)
