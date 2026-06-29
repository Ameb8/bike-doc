# Bike Doc Diagnostic Artifact Storage Spec

Status: Draft v0.1
Last updated: 2026-06-23

This spec defines artifact upload and storage behavior for the diagnostic
vertical slice. It is intentionally small for V1: authenticated users can upload
diagnostic photos through the product API, the backend persists app-owned
artifact metadata, and tests/local development can run without real Google Cloud
Storage.

The public API remains `docs/specs/openapi.yaml`. This spec does not add public
routes or change existing schemas.

## References

- Backend scaffold and artifact boundary: `docs/specs/apps/api.md`
- Diagnostic API delta: `docs/specs/apps/api-diagnostic.md`
- Diagnostic DB schema: `docs/specs/apps/api-db-diagnostic.md`
- API error handling: `docs/specs/apps/api-errors.md`
- Auth behavior: `docs/specs/apps/api-auth-dev.md`
- Diagnostic event and SSE semantics:
  `docs/specs/apps/api-events-diagnostic.md`
- Diagnostic report schema: `docs/specs/apps/diagnostic-report-v1.md`
- ADK diagnostic tools: `docs/specs/apps/adk-diagnostic-tools.md`
- Public API contract: `docs/specs/openapi.yaml`

## Goals

- Implement `POST /v1/artifacts` for diagnostic photos.
- Keep artifact IDs, metadata, ownership, and validation app-owned.
- Keep binary storage behind a narrow provider interface.
- Make local development and API tests deterministic without GCS.
- Preserve a clean path to production GCS storage and later artifact purposes.

## Non-Goals

- Do not implement direct-to-GCS signed upload URLs in V1.
- Do not expose raw storage bucket names, object paths, or signed media URLs in
  public API responses.
- Do not use ADK artifact services as the product artifact source of truth.
- Do not implement async image processing, virus scanning, EXIF analysis, or
  perceptual duplicate detection in V1.
- Do not implement artifact deletion or binary garbage collection in the
  diagnostic slice.
- Do not implement upload behavior for planning, execution, or shop referral
  workflows beyond keeping the schema compatible.

## Public API Surface

The diagnostic slice uses the existing OpenAPI operation:

```text
POST /v1/artifacts
```

For diagnostic V1:

- `purpose` must be `diagnostic_photo`.
- `repair_session_id` is required.
- `bike_id` must be omitted or `null`.
- `file` is required.
- `client_artifact_id` is optional and acts as a user-scoped idempotency key.

The route returns:

```json
{
  "artifact": {
    "id": "art_123",
    "user_id": "usr_123",
    "repair_session_id": "rs_123",
    "bike_id": null,
    "purpose": "diagnostic_photo",
    "media_type": "image",
    "mime_type": "image/jpeg",
    "filename": "rear-derailleur.jpg",
    "byte_size": 345678,
    "width": null,
    "height": null,
    "duration_seconds": null,
    "status": "ready",
    "rejection_reason": null,
    "created_at": "2026-06-21T17:01:30Z"
  }
}
```

`GET /v1/artifacts/{artifactId}` exists in OpenAPI, but it is not required for
the diagnostic vertical slice unless implementation work chooses to add it
while building artifact metadata reads. If implemented, it must return only
owner-scoped `ArtifactRef` metadata.

## Purpose Rules

The OpenAPI `ArtifactPurpose` enum remains broader than diagnostic V1:

- `diagnostic_photo`
- `verification_photo`
- `bike_profile_photo`
- `repair_reference`
- `other`

V1 upload behavior is only required for `diagnostic_photo`. Other purposes may
return `422 validation_error` until their owning feature specs define behavior.
This keeps the API contract stable without accidentally committing to future
workflows.

Parent-resource rules must match `openapi.yaml` and
`api-db-diagnostic.md`:

| Purpose | `repair_session_id` | `bike_id` | Diagnostic V1 behavior |
|---|---:|---:|---|
| `diagnostic_photo` | Required | Must be null | Supported |
| `verification_photo` | Required | Must be null | May return `422` until execution spec exists |
| `bike_profile_photo` | Must be null | Required | May return `422` until bike profile photo spec exists |
| `repair_reference` | Optional | Optional | May return `422` until reference ingestion spec exists |
| `other` | Optional | Optional | May return `422` until a concrete use case exists |

For `diagnostic_photo`, the referenced repair session must:

- exist
- belong to the authenticated user
- be in a state where diagnostic input is accepted or retained as diagnostic
  evidence

Owner-scoped missing or unauthorized resources return `404 not_found`.

## Validation Rules

### MIME Types

Diagnostic V1 accepts only:

- `image/jpeg`
- `image/png`
- `image/webp`

The backend must validate the effective MIME type. It may use the multipart
content type as a first pass, but it must not trust the file extension alone.

Files with unsupported MIME types return `422 validation_error`.

### Media Type

Accepted diagnostic photos are serialized as:

```json
"media_type": "image"
```

`duration_seconds` is always `null` for diagnostic photos.

### Upload Size

Diagnostic V1 max upload size is:

```text
10 MiB per file
```

The implementation should expose this as configuration, with a default of
`10485760` bytes.

Files larger than the configured limit return:

```text
413 payload_too_large
```

### Empty Files

Zero-byte files are invalid and return `422 validation_error`.

### Filename Handling

The public `filename` field is a display value, not a storage key.

The service must:

- discard any client-supplied path components
- normalize blank filenames to a generated value such as `upload.jpg`
- trim surrounding whitespace
- cap stored filenames at 255 characters
- avoid using the filename to authorize access or choose storage ownership

The storage object name must be generated by the backend and must not depend on
the client filename for uniqueness.

### Image Dimensions

Dimension extraction is optional in V1.

If the implementation can cheaply and safely parse dimensions for JPEG and PNG,
it should store positive `width` and `height`. If dimensions are not extracted,
both fields must be `null`.

The service must not reject an otherwise valid image only because dimensions
could not be extracted, unless the parser determines that the file is malformed
or unsafe to process.

## Idempotency

`client_artifact_id` is optional. When present, it is unique per authenticated
user.

The service must follow the idempotency behavior from
`api-db-diagnostic.md`:

- same `user_id`, same `client_artifact_id`, same semantic request: return the
  original artifact response with `201 Created`
- same `user_id`, same `client_artifact_id`, different semantic request:
  return `409 idempotency_conflict`

For artifacts, semantic equality includes:

- `purpose`
- `repair_session_id`
- `bike_id`
- normalized public `filename`
- `content_sha256`

`content_sha256` is the lowercase hex SHA-256 hash of the uploaded bytes.
`request_hash` is the lowercase hex SHA-256 hash of the canonicalized semantic
request fields above.

On idempotent retry, the service should not write a second storage object.

## Persistence Model

The app-owned source of truth is the `artifact_refs` table in
`api-db-diagnostic.md`.

Required diagnostic upload persistence:

- `id`: generated app ID with `art_` prefix
- `user_id`: authenticated user
- `repair_session_id`: target repair session
- `bike_id`: `null`
- `client_artifact_id`: supplied key or `null`
- `request_hash`: supplied-key hash or `null`
- `purpose`: `diagnostic_photo`
- `media_type`: `image`
- `mime_type`: validated effective MIME type
- `filename`: normalized display filename
- `byte_size`: uploaded byte count
- `width` / `height`: extracted dimensions or `null`
- `duration_seconds`: `null`
- `status`: `ready` for successful diagnostic V1 uploads
- `rejection_reason`: `null` for successful uploads
- `content_sha256`: lowercase hex SHA-256 of uploaded bytes
- `storage_provider`: provider identifier such as `local` or `gcs`
- `storage_bucket`: provider bucket/container when applicable, otherwise
  `null`
- `storage_path`: provider object/path key, not exposed publicly

Validation failures that return `4xx` do not need an artifact row.

## Storage Provider Interface

The provider interface belongs under:

```text
apps/api/src/bike_doc_api/providers/storage/base.py
```

The product service calls the provider through `services/artifacts.py`.
Routes must not call provider implementations directly.

The V1 interface should be narrow:

```python
class StorageProvider(Protocol):
    provider_name: str

    async def put_object(
        self,
        *,
        object_name: str,
        content: bytes,
        content_type: str,
        content_sha256: str,
    ) -> StoredObject:
        ...
```

`StoredObject` should contain only app-needed storage metadata:

```python
class StoredObject(BaseModel):
    provider: str
    bucket: str | None
    path: str
    byte_size: int
    content_sha256: str
```

Provider implementations must not return vendor SDK objects to services,
repositories, routes, or ADK tools.

## Local Storage Behavior

Local development and tests use a local provider. It should:

- write files under a configured directory
- create parent directories when needed
- return `provider = "local"`
- return `bucket = null`
- return a relative provider path as `path`
- avoid depending on GCS credentials or network access

Suggested local root:

```text
apps/api/.local/artifacts/
```

Tests may use a temporary directory instead.

Local files should use the same object naming strategy as GCS where practical,
so migration between providers does not change metadata semantics.

## GCS Object Naming Strategy

Production GCS can be implemented later behind the same provider interface.
This spec only defines the object key convention.

Suggested object name:

```text
users/{user_id}/repair-sessions/{repair_session_id}/artifacts/{artifact_id}/{content_sha256}.{ext}
```

Where:

- `user_id`, `repair_session_id`, and `artifact_id` are app-owned IDs
- `content_sha256` is the uploaded content hash
- `ext` is `jpg` for `image/jpeg`
- `ext` is `png` for `image/png`
- `ext` is `webp` for `image/webp`

The object name intentionally does not include the original filename.

The public API must not serialize this GCS object name. It is persisted only in
`artifact_refs.storage_path`.

## Transaction And Failure Behavior

The service should perform upload handling in this order:

1. Authenticate and resolve the current user.
2. Read the upload bytes up to the configured size limit.
3. Validate non-empty content, MIME type, purpose, and parent-resource rules.
4. Normalize filename and compute `content_sha256`.
5. Check idempotency if `client_artifact_id` is supplied.
6. Generate `artifact_id` and storage object name.
7. Store bytes through the storage provider.
8. Insert `artifact_refs` metadata in the database.
9. Return the public `ArtifactRef`.

If the storage provider fails before metadata is committed, return a generic
`500 server_error` and do not insert an artifact row.

If database commit fails after provider storage succeeds, the implementation
should log the orphaned provider object path for cleanup. V1 does not require
automatic binary cleanup, but provider paths in logs must avoid exposing
credentials or signed URLs.

## Events And Turn Integration

Uploading an artifact does not by itself have to emit a repair-session event in
diagnostic V1.

When a later turn references uploaded artifact IDs, the turn service must:

- verify each artifact belongs to the authenticated user
- verify each artifact is attached to the target repair session
- persist any required `artifact.referenced` event according to
  `api-events-diagnostic.md`

This keeps upload behavior simple and makes diagnostic context explicit at the
turn boundary.

## ADK Tool Integration

ADK diagnostic tools consume artifact metadata through
`list_diagnostic_artifacts` in `adk-diagnostic-tools.md`.

For V1, tools receive metadata only:

- artifact ID
- purpose
- media type and MIME type
- filename
- byte size
- status
- dimensions when available
- creation time

Tools must not receive raw storage paths, GCS objects, signed URLs, or ADK
artifact service objects unless a later provider contract explicitly adds that
capability.

## Report Integration

Diagnostic reports may reference artifacts through:

- `phase_reports.source_artifact_ids`
- `DiagnosticReportV1.key_artifact_ids`

Before report persistence, the report service must verify referenced artifact
IDs are:

- owned by the authenticated user
- attached to the same repair session
- valid diagnostic evidence for the current diagnostic phase

## Error Mapping

Known failures map to the public error envelope from `api-errors.md`.

| Case | HTTP status | Error code |
|---|---:|---|
| Missing or invalid bearer token | `401` | `unauthorized` |
| Session or bike missing/not owned | `404` | `not_found` |
| Reused `client_artifact_id` with different semantic request | `409` | `idempotency_conflict` |
| File exceeds configured max size | `413` | `payload_too_large` |
| Missing file | `422` | `validation_error` |
| Unsupported MIME type | `422` | `validation_error` |
| Invalid purpose/parent-resource combination | `422` | `validation_error` |
| Empty file | `422` | `validation_error` |
| Storage provider failure | `500` | `server_error` |
| Unexpected database or integrity failure | `500` | `server_error` |

Public error messages must not include raw provider errors, storage paths,
tokens, stack traces, or SQL details.

## Tests

Stage 9 implementation should include focused tests for:

- successful JPEG diagnostic upload
- successful PNG diagnostic upload
- `repair_session_id` required for `diagnostic_photo`
- `bike_id` rejected for `diagnostic_photo`
- missing or not-owned session returns `404`
- unsupported MIME type returns `422`
- empty file returns `422`
- oversize file returns `413`
- same `client_artifact_id` and same content returns original artifact
- same `client_artifact_id` and different content returns `409`
- returned `ArtifactRef` does not expose provider path or bucket
- local provider writes bytes and returns provider metadata

Provider tests should run without network access.

## Future Extensions

The following can be added without changing the V1 public shape:

- signed upload URLs for large media
- signed read URLs for internal agent vision or user preview workflows
- HEIC/HEIF acceptance for mobile uploads
- async image processing and `processing` status
- malware scanning or moderation status
- artifact deletion and provider garbage collection
- richer storage providers
- direct ADK artifact mirroring inside the internal ADK layer

Any future direct-upload design must keep `artifact_refs` as the app-owned
metadata source of truth and continue returning `ArtifactRef` from the product
API.

## Consistency Notes

This spec intentionally follows the existing specs without modifying them.

No required inconsistency was found. Two existing choices are worth preserving
explicitly:

- `openapi.yaml` says V1 stores binaries in GCS, while the implementation plan
  allows local/stub storage until production GCS is needed. This spec treats
  GCS as the production provider and local storage as a development/test
  provider behind the same interface.
- `api-diagnostic.md` examples show populated image dimensions, while
  `openapi.yaml` and `api-db-diagnostic.md` allow `width` and `height` to be
  nullable. This spec keeps dimensions optional for V1 so implementation can
  start simple.
