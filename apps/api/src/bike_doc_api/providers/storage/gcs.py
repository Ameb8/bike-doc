"""GCS storage provider boundary."""

from bike_doc_api.providers.storage.base import StoredObject


class GCSStorageProvider:
    """Placeholder preserving the production provider boundary."""

    provider_name = "gcs"

    async def put_object(
        self,
        *,
        object_name: str,
        content: bytes,
        content_type: str,
        content_sha256: str,
    ) -> StoredObject:
        """Reject cloud writes until production GCS behavior is implemented."""

        del object_name, content, content_type, content_sha256
        raise NotImplementedError("GCS artifact storage is not implemented")
