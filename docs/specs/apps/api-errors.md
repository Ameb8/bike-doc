# Bike Doc API Error Handling Spec

Status: Draft v0.1
Last updated: 2026-06-22

This spec defines cross-cutting public error behavior for the Bike Doc backend.
Endpoint-specific expected cases remain in feature specs such as
`docs/specs/apps/api-diagnostic.md`.

## References

- Public API contract: `docs/specs/openapi.yaml`
- Backend scaffold: `docs/specs/apps/api.md`
- Diagnostic API delta: `docs/specs/apps/api-diagnostic.md`
- Diagnostic event and SSE semantics:
  `docs/specs/apps/api-events-diagnostic.md`
- Auth behavior: `docs/specs/apps/api-auth-dev.md`

## Public HTTP Envelope

All non-2xx JSON API errors must use the OpenAPI `ErrorResponse` shape:

```json
{
  "error": {
    "code": "validation_error",
    "message": "Request validation failed.",
    "details": null
  }
}
```

`code` is stable and machine-readable. `message` is client-facing and must not
include internal exception text, SQL details, tokens, prompts, provider
payloads, or stack traces. `details` is optional and should contain only
client-actionable fields.

## Internal Exception Model

Backend services should raise app-level exceptions for expected failures.
FastAPI `HTTPException` is acceptable at the HTTP adapter boundary, but service,
repository, provider, and ADK code must not depend on FastAPI request or
response objects.

The implementation should keep the mapping centralized in
`bike_doc_api.core.errors`:

| Internal category | HTTP status | Default public code |
|---|---:|---|
| Authentication failure | `401` | `unauthorized` |
| Authenticated identity cannot map to a user | `401` | `user_mapping_required` |
| Owner-scoped resource missing or not owned | `404` | `not_found` |
| Idempotency payload conflict | `409` | `idempotency_conflict` |
| State-machine conflict | `409` | `session_state_conflict` |
| Uploaded payload too large | `413` | `payload_too_large` |
| Request, query, or public payload validation failure | `422` | `validation_error` |
| Safety policy rejects a public decision | `422` | `blocking_safety_flag` or `safety_ack_required` |
| Internal data-integrity or unexpected server failure | `500` | `server_error` |

Feature specs may define additional stable codes for their own domains, but
they must still use this envelope.

## Ownership And Disclosure

Owner-scoped resources that are missing or belong to another user return
`404 not_found`, not `403`, to avoid leaking resource existence.

Authentication failures should not distinguish malformed, expired, unverifiable,
or missing credentials in public messages. Logs may include structured internal
reasons, but must not log raw tokens or secrets.

## Validation And Integrity

Client request validation failures return `422 validation_error`.

Invalid generated model/tool output must fail before persistence. Persisted
invalid data discovered during an API read is a server data-integrity problem,
not a client validation error; log it with enough internal context to debug and
return a generic server error.

Raw database, provider, ADK, and unexpected Python exceptions must be logged and
converted to a generic public server error. They must not bubble to clients.

## SSE Errors

SSE endpoints use the same HTTP error envelope before the stream opens:
authentication, ownership, and cursor validation failures return normal HTTP
errors.

After a `200 OK` SSE stream is open, recoverable processing failures should be
persisted and emitted as public `error` events when possible. Fatal unexpected
stream failures should be logged and the stream closed.
