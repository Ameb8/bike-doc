# Backend API Agent Notes

This directory contains the Bike Doc backend API. Keep backend work scoped to
`apps/api` unless a change explicitly requires shared contracts, docs, infra, or
evals.

## Canonical References

- Backend scaffold and ownership: `docs/specs/apps/api.md`
- Product API contract: `docs/specs/openapi.yaml`
- Product behavior and workflow: `docs/specs/bike-doc.md`

## Backend Shape

The backend is a custom FastAPI service using a Python `src/` layout with
package root `src/bike_doc_api`. Google ADK is imported as an internal library
only; do not expose ADK sessions, prompts, tools, model settings, or internal
schemas through the public API.

Expected boundaries:

- `api/` adapts HTTP requests and responses.
- `schemas/` holds Pydantic API models aligned with the OpenAPI contract.
- `services/` owns product behavior, workflow rules, idempotency, events, and
  safety enforcement.
- `repositories/`, `models/`, and `db/` own persistence.
- `adk/` isolates agent orchestration and ADK-specific code.
- `providers/` isolates external storage, price, and repair-reference services.

## Development Rules

- Keep route handlers thin: validate inputs, resolve dependencies, call
  services, and return schemas.
- Keep safety checks in backend services, not only in prompts.
- Keep ADK tools thin wrappers over services or provider interfaces.
- Do not initialize a standalone ADK app server for this backend.
- Do not add fake production behavior in placeholders.
- Keep backend tests under `apps/api/tests`; agent behavior evaluations belong
  under `evals/bike-doc`.

## Import Direction

Prefer:

```text
api -> services -> repositories -> models/db
api -> schemas
services -> providers
services -> adk orchestration
adk tools -> services/providers
```

Avoid reverse imports across these layers, direct ADK agent imports from route
handlers, direct SQL in ADK tools, and FastAPI request objects in providers.
