"""Application ID generation helpers."""

from __future__ import annotations

import secrets
import time

_CROCKFORD_BASE32 = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


def _encode_ulid(value: int) -> str:
    chars: list[str] = []
    for _ in range(26):
        chars.append(_CROCKFORD_BASE32[value & 0x1F])
        value >>= 5
    return "".join(reversed(chars))


def generate_prefixed_ulid(prefix: str) -> str:
    """Return a prefixed, time-sortable ULID string."""
    timestamp_ms = int(time.time() * 1000) & ((1 << 48) - 1)
    randomness = secrets.randbits(80)
    return f"{prefix}{_encode_ulid((timestamp_ms << 80) | randomness)}"
