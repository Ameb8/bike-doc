# Bike Doc API Auth And Local Development Spec

Status: Draft v0.1
Last updated: 2026-06-21

This spec defines the authentication boundary and deterministic local/test auth
strategy for the Bike Doc API. It is a delta on top of
`docs/specs/apps/api.md` and `docs/specs/openapi.yaml`; it does not add login,
signup, refresh, password, or identity-provider management routes.

## References

- Backend scaffold: `docs/specs/apps/api.md`
- Diagnostic DB schema: `docs/specs/apps/api-db-diagnostic.md`
- Diagnostic API delta: `docs/specs/apps/api-diagnostic.md`
- Public API contract: `docs/specs/openapi.yaml`
- Product design: `docs/specs/bike-doc.md`

## Goals

- Keep production authentication behind a bearer-JWT validation boundary.
- Let API tests authenticate deterministically without an external provider.
- Define exactly how a validated token identity maps to the app-owned `users`
  table.
- Define required user defaults, especially `skill_level`.
- Make missing, invalid, and unmapped-auth behavior predictable for all
  secured routes.

## Non-Goals

- Do not implement app-owned login, signup, logout, token refresh, or password
  reset routes.
- Do not choose the final production identity provider in this spec.
- Do not expose provider-specific claims, provider user IDs, or auth internals
  in public API responses.
- Do not make ADK sessions, tools, or prompts aware of bearer tokens.

## Production Auth Boundary

All public API routes in `docs/specs/openapi.yaml` require:

```http
Authorization: Bearer <jwt>
```

The bearer token is issued by an external identity provider. The backend is
responsible for validating the token and resolving an app-owned user before
route handlers call services.

Production validation must verify:

- The token is present in the `Authorization` header using the `Bearer` scheme.
- The token is a JWT accepted by the configured provider verifier.
- Signature, issuer, audience, expiry, and not-before claims are valid.
- A stable provider subject claim is present.

The backend should keep the provider implementation behind `core/security.py`.
Route handlers should depend on `api/deps.py` current-user resolution. Product
services should receive the resolved app user or user ID; they must not parse
or validate bearer tokens directly.

## Auth Identity

Validated token claims are normalized into an internal auth identity before
touching application tables:

```text
AuthIdentity
  subject: string
  email: string | null
  display_name: string | null
```

Rules:

- `subject` is the stable provider user identifier and maps to
  `users.auth_subject`.
- `subject` must be non-empty after trimming.
- `email` may come from a provider email claim. Production code must reject an
  auto-create attempt when `email` is missing or blank.
- `display_name` may come from provider name/display-name claims. When absent,
  the backend derives a non-empty default from the email local part.
- Provider-specific claim names must be normalized inside the security/auth
  boundary, not leaked into repositories or route modules.

## User Mapping

The `users` table is the app-owned profile for an authenticated person.

Required stored fields:

| Field | Source/default |
|---|---|
| `id` | App-generated prefixed ULID, `usr_...`. |
| `auth_subject` | Normalized `AuthIdentity.subject`; unique. |
| `email` | Normalized `AuthIdentity.email`; required and non-blank. |
| `display_name` | Normalized `AuthIdentity.display_name`; required and non-blank. |
| `skill_level` | Defaults to `unknown`. |
| `created_at` | Database default timestamp. |

Allowed `skill_level` values are the OpenAPI `UserSkillLevel` enum:

```text
unknown, beginner, intermediate, advanced
```

The initial user record must use `skill_level: unknown` unless a future
explicit onboarding/update flow sets a different value. Auth token claims must
not set `skill_level`.

On each authenticated request, the auth service resolves the current app user
by `users.auth_subject = AuthIdentity.subject`.

If the user exists:

- Return that app user as the current user.
- Do not overwrite `skill_level`.
- Do not overwrite `display_name` on every request.
- Email synchronization is allowed only if implemented deliberately and tested;
  the diagnostic slice does not require it.

## Auto-Creation Policy

Bike Doc V1 should auto-create a `users` row on the first successfully
authenticated request when all required user fields can be derived.

Auto-create is enabled for:

- Production bearer tokens that validate successfully and include a usable
  subject and email.
- Local dev fixed-token identities.
- Local unsigned-token fixture identities.
- Test dependency overrides that explicitly request a current user fixture.

Auto-create must not run when token validation fails.

If validation succeeds but required profile fields cannot be derived, return
`401 Unauthorized` with `error.code: user_mapping_required`. This is an auth
resolution failure, not a request validation error.

Auto-creation must be idempotent under concurrent requests. The repository
should enforce a unique constraint on `auth_subject`; the auth service should
handle the create race by re-reading the existing user.

## Local Development Strategy

Local development must support deterministic auth without contacting an
external identity provider. The implementation may support both local modes
below. If only one mode is implemented first, prefer the fixed dev token
because it is simplest for manual API calls.

### Fixed Dev Token

When `BIKE_DOC_API_ENVIRONMENT=local`, the API may accept one configured fixed
token value.

Suggested settings:

```text
BIKE_DOC_API_AUTH_MODE=dev
BIKE_DOC_API_DEV_AUTH_TOKEN=dev-token
BIKE_DOC_API_DEV_AUTH_SUBJECT=dev-user
BIKE_DOC_API_DEV_AUTH_EMAIL=dev@example.com
BIKE_DOC_API_DEV_AUTH_DISPLAY_NAME="Dev User"
```

Request:

```http
Authorization: Bearer dev-token
```

The fixed token resolves to:

```text
subject: dev-user
email: dev@example.com
display_name: Dev User
```

The auth service then maps or auto-creates the corresponding `users` row using
the same code path as production identity mapping.

Fixed dev-token mode must not be enabled when
`BIKE_DOC_API_ENVIRONMENT=production`.

### Local Unsigned Token Fixture

Local development may also support unsigned JWT fixtures for testing multiple
local users without a provider.

This mode is allowed only when explicitly configured, for example:

```text
BIKE_DOC_API_AUTH_MODE=local_unsigned_jwt
```

Rules:

- Accept JWTs with `alg: none` only in local/test environments.
- Require `sub` and `email` claims.
- Use `name` or `display_name` if present; otherwise derive `display_name`
  from the email local part.
- Reject expired tokens when an `exp` claim is present, even in local mode.
- Never allow unsigned JWTs in production.

Example fixture payload:

```json
{
  "sub": "local-user-1",
  "email": "local1@example.com",
  "name": "Local User 1"
}
```

Unsigned fixture mode is useful for exercising owner scoping with multiple
users in integration tests or manual local API exploration.

## Test Strategy

API tests should not depend on production JWT verification or network access.

Preferred test approach:

- Override the FastAPI current-user dependency in tests.
- Seed or create a deterministic `users` row with a known `id`.
- Exercise route behavior with that resolved user.

Tests that specifically cover auth behavior should use the auth service
directly or configure a local auth mode:

- Missing token returns `401 unauthorized`.
- Malformed token returns `401 unauthorized`.
- Fixed dev token resolves to the configured test user.
- Local unsigned fixture resolves to the expected subject when that mode is
  enabled.
- A known validated subject with no existing user auto-creates a user when
  required fields are available.
- A known validated subject with no existing user and no usable email returns
  `401 user_mapping_required`.

Dependency overrides are the default for most route tests because they keep
test setup focused on endpoint behavior rather than token mechanics.

## Error Behavior

All auth failures return an `ErrorResponse` and `401 Unauthorized`.

Missing token:

```json
{
  "error": {
    "code": "unauthorized",
    "message": "Authentication is required."
  }
}
```

Invalid token cases include malformed header, unsupported scheme, malformed
JWT, failed signature, wrong issuer/audience, expired token, not-yet-valid
token, disabled local auth mode, and unsigned token outside local/test mode.
They return:

```json
{
  "error": {
    "code": "unauthorized",
    "message": "Authentication is required."
  }
}
```

Known validated identity with no mapped user:

- If auto-create succeeds, continue the request as that new user.
- If auto-create is disabled in a future environment, return `401
  user_not_registered`.
- If auto-create is enabled but required user fields cannot be derived, return
  `401 user_mapping_required`.

Example unmapped-user response when auto-create is disabled:

```json
{
  "error": {
    "code": "user_not_registered",
    "message": "Authenticated user is not registered."
  }
}
```

Example mapping-failure response:

```json
{
  "error": {
    "code": "user_mapping_required",
    "message": "Authenticated identity is missing required profile fields."
  }
}
```

Route handlers should not distinguish malformed, expired, and unverifiable
tokens in user-facing messages. Logs may record internal failure reasons
without logging raw tokens.

## Implementation Boundaries

Expected ownership:

| Module | Responsibility |
|---|---|
| `core/security.py` | Parse `Authorization`, validate production/dev/local tokens, return `AuthIdentity`. |
| `services/auth.py` | Map `AuthIdentity` to `users`, auto-create users, enforce required fields. |
| `repositories/users.py` | Query/create users by `auth_subject`. |
| `api/deps.py` | Expose current-user FastAPI dependency for route modules and test overrides. |
| `api/v1/me.py` | Return the resolved current user as the OpenAPI `User` response. |

ADK code and provider tools receive app user context only after auth
resolution. They should not receive raw bearer tokens.

## Definition Of Done

- API tests can authenticate deterministically without an external provider.
- Normal route tests can override the current-user dependency.
- Auth-specific tests cover missing token, invalid token, fixed dev token, and
  first-request auto-create behavior.
- `GET /v1/me` can return a schema-valid `User` with `skill_level: unknown`
  for a first-time local/test user.
- Production mode cannot accept fixed dev tokens or unsigned local JWTs.
