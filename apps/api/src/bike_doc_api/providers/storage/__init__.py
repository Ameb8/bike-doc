"""Artifact storage provider package."""

from bike_doc_api.providers.storage.base import StorageProvider, StoredObject
from bike_doc_api.providers.storage.gcs import GCSStorageProvider
from bike_doc_api.providers.storage.local import LocalStorageProvider

__all__ = [
    "GCSStorageProvider",
    "LocalStorageProvider",
    "StorageProvider",
    "StoredObject",
]
