"""GCS storage provider boundary."""

from __future__ import annotations

import asyncio

from google.cloud import storage  # type: ignore[import-untyped]

from bike_doc_api.providers.storage.base import StoredObject


class GCSStorageProvider:
    """Store artifact objects in a private GCS bucket."""

    provider_name = "gcs"

    def __init__(
        self,
        *,
        bucket_name: str,
        client: storage.Client | None = None,
    ) -> None:
        self._client = client or storage.Client()
        self._bucket = self._client.bucket(bucket_name)

    async def put_object(
        self,
        *,
        object_name: str,
        content: bytes,
        content_type: str,
        content_sha256: str,
    ) -> StoredObject:
        """Upload bytes to GCS and return provider-neutral metadata."""

        blob = self._bucket.blob(object_name)
        await asyncio.to_thread(
            blob.upload_from_string,
            content,
            content_type=content_type,
            if_generation_match=0,
        )
        return StoredObject(
            provider=self.provider_name,
            bucket=self._bucket.name,
            path=object_name,
            byte_size=len(content),
            content_sha256=content_sha256,
        )
