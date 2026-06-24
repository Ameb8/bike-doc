"""Storage provider interface boundary."""

from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field


class StoredObject(BaseModel):
    """App-owned metadata returned after storing an object."""

    model_config = ConfigDict(extra="forbid")

    provider: str = Field(min_length=1)
    bucket: str | None = None
    path: str = Field(min_length=1)
    byte_size: int = Field(ge=0)
    content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class StorageProvider(Protocol):
    """Narrow storage interface used by product artifact services."""

    provider_name: str

    async def put_object(
        self,
        *,
        object_name: str,
        content: bytes,
        content_type: str,
        content_sha256: str,
    ) -> StoredObject:
        """Store object bytes and return provider-neutral metadata."""
