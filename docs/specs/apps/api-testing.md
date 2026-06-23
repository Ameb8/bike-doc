# Bike Doc API Testing Conventions Spec

Status: Draft v0.1
Last updated: 2026-06-23

This spec defines the standard test layout, fixture style, and test boundaries
for the Bike Doc FastAPI backend. Feature specs define what behavior must be
tested; this document defines how backend tests should be organized and written
so future slices stay consistent.

## References

- Backend scaffold: `docs/specs/apps/api.md`
- API error handling: `docs/specs/apps/api-errors.md`
- Auth and local development: `docs/specs/apps/api-auth-dev.md`
- Diagnostic API delta: `docs/specs/apps/api-diagnostic.md`
- Diagnostic DB schema: `docs/specs/apps/api-db-diagnostic.md`
- Diagnostic event and SSE semantics:
  `docs/specs/apps/api-events-diagnostic.md`
- Public API contract: `docs/specs/openapi.yaml`
- Backend root: `apps/api`

## Goals

- Keep tests readable and predictable across backend slices.
- Make public API behavior executable before endpoint implementation is filled
  in.
- Keep unit, API, contract, and end-to-end tests separate enough that failures
  identify the affected layer.
- Use deterministic local fakes for auth, storage, providers, and ADK
  boundaries.
- Avoid real network calls, real production credentials, and model calls in
  normal backend tests.

## Non-Goals

- Do not define every diagnostic test case here. Endpoint-specific behavior
  belongs in feature specs such as `api-diagnostic.md`.
- Do not create a separate prose test plan for every feature unless it removes
  real ambiguity.
- Do not test exact LLM wording or agent quality in backend pytest tests. Agent
  behavior belongs in `evals/bike-doc`.
- Do not require a complex custom pytest plugin or fixture framework for V1.

## Test Layers

Backend tests live under `apps/api/tests`.

```text
tests/
  conftest.py
  unit/
    services/
    repositories/
    providers/
    schemas/
  api/
    test_repair_sessions.py
    test_turns.py
    test_events.py
    test_artifacts.py
    test_reports.py
  contract/
    test_openapi_contract.py
  integration/
    test_diagnostic_slice.py
```

Layer responsibilities:

| Layer | Purpose | Allowed dependencies |
|---|---|---|
| `unit/` | Test one service, repository, provider, schema, or mapper rule in isolation. | In-memory objects, fake services, fake providers, test database only for repository tests. |
| `api/` | Test public HTTP behavior and externally visible side effects for one endpoint group. | FastAPI app, dependency overrides, test database, fake providers, fake ADK boundary. |
| `contract/` | Test schema and OpenAPI alignment. | App OpenAPI output, `docs/specs/openapi.yaml`, generated schemas when relevant. |
| `integration/` | Test a full vertical slice flow after its parts exist. | Local app, test database, fake external providers, optional fake agent runner. |

Do not add `integration/` tests until the relevant endpoint groups and
persistence paths exist. For the diagnostic slice, the first full integration
test belongs near the roadmap's end-to-end stage, after session, turn, event,
artifact, report, and safety behavior have working implementations.

## Test File And Function Naming

Use one test file per endpoint group or service area.

Preferred names:

```text
tests/api/test_repair_sessions.py
tests/unit/services/test_safety.py
tests/unit/repositories/test_events.py
tests/contract/test_openapi_contract.py
```

Use behavior-oriented test names:

```python
def test_create_session_with_unknown_bike_returns_404() -> None:
    ...

def test_repeating_client_turn_id_returns_original_turn_acceptance() -> None:
    ...
```

Avoid names that describe implementation details, such as repository method
names, SQLAlchemy internals, or route function names.

## Fixture Conventions

Shared fixtures go in `tests/conftest.py` only when at least two test modules
need them. Keep feature-specific helpers in the test module until reuse is
clear.

Standard fixture names:

| Fixture | Purpose |
|---|---|
| `settings` | `Settings` configured for `environment="test"`. |
| `app` | FastAPI app created through `create_app(settings)`. |
| `api_client` | HTTPX client bound to the test app through ASGI transport. |
| `db_session` | Async database session scoped to one test. |
| `test_user` | Persisted app user owned by the current test. |
| `auth_headers` | Headers or dependency override data for the default test user. |
| `fake_storage` | Storage provider fake with no real network or cloud writes. |
| `fake_adk_runner` | Deterministic ADK boundary fake, used only when orchestration is under test. |

Helper naming:

- `make_*` builds an object without persistence, for example
  `make_diagnostic_report_payload`.
- `create_*` persists test data, for example `create_bike_profile`.
- `assert_*` checks a reusable public contract, for example
  `assert_error_response`.

Factories should return domain-relevant objects or API payloads, not raw tuples.
Prefer explicit defaults over randomized data. Use random or generated values
only when uniqueness matters, such as client idempotency IDs.

## API Test Harness

API tests should exercise the FastAPI app in process.

- Create the app with `create_app(Settings(...))`.
- Use FastAPI dependency overrides for current-user auth in most route tests.
- Use `httpx.AsyncClient` with `httpx.ASGITransport` for request execution.
- Avoid mutating global app state. Apply overrides in a fixture and clear them
  after the test.
- Do not start a real Uvicorn server for normal API tests.

Auth-specific tests may exercise local dev-token behavior from
`api-auth-dev.md`. Other route tests should use dependency overrides so they
stay focused on endpoint behavior.

## Database Strategy

Repository and API tests may use a real test database because persistence,
constraints, transactions, and idempotency are part of the backend contract.

Rules:

- Use SQLAlchemy async sessions.
- Run migrations or create metadata through one central test setup path.
- Isolate tests with one of these strategies:
  - transaction rollback per test, preferred when compatible with async
    database behavior
  - schema/table truncation per test module when rollback is not practical
- Do not rely on test execution order.
- Do not share persisted IDs across test modules.
- Do not use production database URLs or credentials.

SQLite may be used only for tests that do not depend on PostgreSQL-specific
behavior. Diagnostic persistence tests that cover JSONB semantics, constraints,
locking, or event sequence allocation should run against test Postgres.

## Provider And ADK Fakes

Normal backend tests must not call external providers.

Use fakes for:

- storage uploads
- auth provider verification
- price lookup
- repair-reference lookup
- ADK runner and agent orchestration

Fakes should be small and behavior-oriented. They should simulate success,
not-found, unauthorized, validation, and provider-failure cases that backend
services must handle. They should not contain product logic that duplicates the
service under test.

## API Assertions

Endpoint tests should assert the public contract and observable side effects:

- HTTP status code
- response `Content-Type` when relevant
- required response fields and stable enum values
- public `ErrorResponse` envelope for errors
- persisted rows needed by later API calls
- idempotent retry behavior
- emitted or replayed event order and cursor values
- absence of ADK internals in public responses

Do not assert private implementation details such as:

- internal repository method calls
- SQLAlchemy object identity
- exact generated primary key randomness
- ADK session object shape
- prompt text
- exact LLM assistant wording

For generated IDs, assert prefix and later retrievability unless the exact value
is deliberately controlled by the test fixture.

## Error Assertions

All public API error tests must assert the error envelope defined in
`docs/specs/apps/api-errors.md`.

At minimum, error assertions should check:

- status code
- `error.code`
- `error.message` is present and non-empty
- request correlation fields when the error spec requires them

Do not assert brittle full error-message strings unless a feature spec requires
the exact wording.

## Contract Tests

Contract tests keep hand-written routes and schemas aligned with
`docs/specs/openapi.yaml`.

The contract suite should cover:

- the app can produce an OpenAPI document
- diagnostic-slice paths exist in the app OpenAPI output
- request and response schemas used by implemented endpoints match the public
  contract closely enough to catch drift
- generated model code, if used, still regenerates from the canonical OpenAPI
  file without manual edits

If manual Pydantic schemas are used, add an app OpenAPI snapshot or structured
diff test for the diagnostic paths. The test should compare meaningful path,
method, status-code, and schema references instead of byte-for-byte formatting.

## SSE Tests

SSE behavior is part of the public API contract for repair-session events.

API tests should cover replay behavior without requiring a live ADK model call:

- `after=0` replays retained events from the beginning.
- a known cursor returns only newer events.
- omitted `after` starts after the current latest event.
- invalid cursors return the expected public validation error.
- unknown or unauthorized sessions return the expected public error.

Tests may parse SSE frames with a small local helper. The helper should verify
`id`, `event`, and JSON `data` fields; it should not depend on private event
model classes.

## Diagnostic Slice Minimum

Before declaring the diagnostic API slice complete, backend tests should cover:

- authenticated current-user dependency behavior
- `POST /v1/repair-sessions`
- `GET /v1/repair-sessions/{sessionId}`
- `POST /v1/repair-sessions/{sessionId}/turns`
- repeated `client_turn_id` idempotency
- `GET /v1/repair-sessions/{sessionId}/events?after=0`
- diagnostic-photo upload validation
- report list and lookup
- expected `401`, `403`, `404`, `409`, and `422` cases
- safety flag validation and session safety-state mapping
- OpenAPI contract drift for implemented diagnostic paths

## Commands

Run backend tests from `apps/api`:

```bash
uv run pytest
```

Useful focused commands:

```bash
uv run pytest tests/unit
uv run pytest tests/api
uv run pytest tests/contract
```

Run lint and format checks before considering a backend change complete:

```bash
uv run ruff check .
uv run ruff format --check .
```

## Definition Of Done

- New backend tests follow the layer, naming, and fixture conventions in this
  spec.
- API tests use deterministic auth and provider fakes.
- Unit tests do not perform real network, storage, provider, or ADK calls.
- Contract tests protect the implemented public API from OpenAPI drift.
- Full vertical-slice tests are kept separate from focused endpoint tests.
