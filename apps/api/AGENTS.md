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

## Code Standards
- **Type Hinting:** Enforce strict type hinting for all function/method signatures (arguments and return types). Avoid cluttering the code with type hints on obvious or local variables unless strictly necessary for IDE clarity.
- **Linting & Formatting:** All Python code must be styled and validated using Ruff (`ruff check --fix` and `ruff format`) prior to committing. Never use Black, Flake8, or isort.
- **Asynchronous Architecture:** All API endpoints and database operations must be natively asynchronous using FastAPI's `async def` and SQLAlchemy's `asyncio` extension. Avoid blocking synchronous calls.
- **Pydantic V2 Usage:** Exclusively use Pydantic V2 idioms for schemas and settings. Use `Field` for validations, and avoid mixing database models with request/response schemas—always use distinct Pydantic models for the API layer.
- **SQLAlchemy 2.0 Style:** Use modern SQLAlchemy 2.0 execution syntax (e.g., `select()`, `scalars()`) inside an `async with async_session()` context block. Always explicitely fetch or load relationships using `selectinload` or `joinedload` to avoid lazy-loading errors in async mode.
- **Dependency Injection:** Leverage FastAPI's `Depends()` framework for database sessions, authentication, and shared logic. Never manually instantiate database sessions or handle global application state inside router endpoints.
- **Explicit Error Handling:** Raise native FastAPI `HTTPException` instances with accurate status codes (from `fastapi.status`) and clear detail strings for client-facing errors. Never allow raw database or internal exceptions to bubble up to the client.
- **Google ADK:** When using Google ADK, run `agents-cli lint --fix` to validate your ADK 2.0 graph mappings, state transitions, and tool signatures as they are written.
- **Google ADK Dry Run:** When using Google ADK and the agent is in a usable state, run local dry-runs and schema checks using `agents-cli deploy --dry-run` to catch structural errors, malformed graph nodes, and dependency conflicts statically.