"""Storage provider tests."""

from __future__ import annotations

from bike_doc_api.providers.storage import GCSStorageProvider


class _FakeBlob:
    def __init__(self, object_name: str) -> None:
        self.object_name = object_name
        self.upload_calls: list[dict[str, object]] = []

    def upload_from_string(
        self,
        content: bytes,
        *,
        content_type: str,
        if_generation_match: int,
    ) -> None:
        self.upload_calls.append(
            {
                "content": content,
                "content_type": content_type,
                "if_generation_match": if_generation_match,
            }
        )


class _FakeBucket:
    def __init__(self, name: str) -> None:
        self.name = name
        self.created_blobs: list[_FakeBlob] = []

    def blob(self, object_name: str) -> _FakeBlob:
        blob = _FakeBlob(object_name)
        self.created_blobs.append(blob)
        return blob


class _FakeClient:
    def __init__(self) -> None:
        self.bucket_calls: list[str] = []
        self.bucket_instance: _FakeBucket | None = None

    def bucket(self, bucket_name: str) -> _FakeBucket:
        self.bucket_calls.append(bucket_name)
        self.bucket_instance = _FakeBucket(bucket_name)
        return self.bucket_instance


async def test_gcs_storage_provider_uploads_bytes_without_overwrite() -> None:
    client = _FakeClient()
    provider = GCSStorageProvider(
        bucket_name="bike-doc-artifacts",
        client=client,
    )

    stored = await provider.put_object(
        object_name="users/usr_123/repair-sessions/rs_123/artifacts/art_123/hash.jpg",
        content=b"jpeg-bytes",
        content_type="image/jpeg",
        content_sha256="a" * 64,
    )

    assert client.bucket_calls == ["bike-doc-artifacts"]
    assert client.bucket_instance is not None
    assert len(client.bucket_instance.created_blobs) == 1
    blob = client.bucket_instance.created_blobs[0]
    assert blob.object_name == (
        "users/usr_123/repair-sessions/rs_123/artifacts/art_123/hash.jpg"
    )
    assert blob.upload_calls == [
        {
            "content": b"jpeg-bytes",
            "content_type": "image/jpeg",
            "if_generation_match": 0,
        }
    ]
    assert stored.provider == "gcs"
    assert stored.bucket == "bike-doc-artifacts"
    assert stored.path == blob.object_name
    assert stored.byte_size == len(b"jpeg-bytes")
    assert stored.content_sha256 == "a" * 64
