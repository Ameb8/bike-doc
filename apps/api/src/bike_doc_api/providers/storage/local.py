"""Local filesystem artifact storage provider."""

from __future__ import annotations

import asyncio
from pathlib import Path, PurePosixPath

from bike_doc_api.providers.storage.base import StoredObject


class LocalStorageProvider:
    """Store artifact objects under a configured local root."""

    provider_name = "local"

    def __init__(self, root: Path) -> None:
        self._root = root

    async def put_object(
        self,
        *,
        object_name: str,
        content: bytes,
        content_type: str,
        content_sha256: str,
    ) -> StoredObject:
        """Write bytes to the local root and return relative object metadata."""

        del content_type
        relative_path = _safe_relative_path(object_name)
        destination = self._root.joinpath(*PurePosixPath(relative_path).parts)
        await asyncio.to_thread(_write_bytes, destination, content)
        return StoredObject(
            provider=self.provider_name,
            bucket=None,
            path=relative_path,
            byte_size=len(content),
            content_sha256=content_sha256,
        )


def _safe_relative_path(object_name: str) -> str:
    """Return a normalized relative provider path."""

    path = PurePosixPath(object_name)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError("object_name must be a safe relative path")
    return path.as_posix()


def _write_bytes(destination: Path, content: bytes) -> None:
    """Write object bytes, creating parent directories first."""

    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(content)
