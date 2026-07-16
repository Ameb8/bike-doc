"""Deterministic privacy boundary for untrusted extractor output."""

from __future__ import annotations

import re
from typing import Any


class ProfileInferencePrivacyError(ValueError):
    """Extractor output contains data the profile-inference feature cannot retain."""


_PROHIBITED_LABEL = re.compile(
    r"\b(?:"
    r"serial(?:\s*(?:number|no\.?))?|vin|vehicle\s+identification|"
    r"owner|person|face|home|address|location|latitude|longitude|"
    r"gps|license\s*plate|email|phone(?:\s*number)?"
    r")\b",
    re.IGNORECASE,
)
_SERIAL_OR_VIN_VALUE = re.compile(
    r"\b(?=[A-Z0-9]*\d)(?:[A-HJ-NPR-Z0-9]{11,17}|[A-Z0-9]{10,})\b",
    re.IGNORECASE,
)
_EMAIL = re.compile(r"\b[^\s@]+@[^\s@]+\.[^\s@]+\b")
_PHONE = re.compile(r"(?:\+?\d[\d .()\-]{7,}\d)")
_STREET_ADDRESS = re.compile(
    r"\b\d{1,6}\s+[A-Za-z][A-Za-z .'-]{1,40}\s"
    r"(?:street|st\.?|avenue|ave\.?|road|rd\.?|lane|ln\.?|drive|dr\.?)\b",
    re.IGNORECASE,
)
_COORDINATES = re.compile(r"\b[+-]?\d{1,2}\.\d{3,}\s*,\s*[+-]?\d{1,3}\.\d{3,}\b")


def validate_privacy_safe_extractor_output(output: Any) -> None:
    """Reject an entire raw output before validation can persist any claim.

    This intentionally does not redact. A rejected run has no usable partial
    result, which prevents a sensitive string from reaching claims, run data,
    or telemetry through a later error path.
    """

    claims = getattr(output, "claims", ())
    for claim in claims:
        _validate_text(getattr(claim, "value", None))
        _validate_text(getattr(claim, "observed_text", None))
        for cue in getattr(claim, "evidence_cues", ()):
            _validate_text(cue)


def _validate_text(value: object) -> None:
    if not isinstance(value, str):
        return
    if (
        _PROHIBITED_LABEL.search(value)
        or _SERIAL_OR_VIN_VALUE.search(value)
        or _EMAIL.search(value)
        or _PHONE.search(value)
        or _STREET_ADDRESS.search(value)
        or _COORDINATES.search(value)
    ):
        raise ProfileInferencePrivacyError("extractor output contains prohibited data")
