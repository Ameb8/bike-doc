# Bike Doc Firebase Auth Implementation Spec

Status: Draft v0.1
Last updated: 2026-06-27

This document defines the simplest production-suitable authentication
implementation for Bike Doc. It assumes the Android app uses Firebase
Authentication and the backend validates Firebase-issued bearer JWTs before
mapping them to app-owned users.

This is an implementation spec, not a file-by-file design. It describes what
must be implemented and what behavior the implementation must confirm to.

## References

- Product design: `docs/specs/bike-doc.md`
- Backend scaffold: `docs/specs/apps/api.md`
- Auth boundary and local/test auth: `docs/specs/apps/api-auth-dev.md`
- Public API contract: `docs/specs/openapi.yaml`
- Error behavior: `docs/specs/apps/api-errors.md`

## 1. Decision

Bike Doc V1 should use:

- Firebase Authentication in the Android app
- Firebase-issued ID tokens as the API bearer token
- Backend-side Firebase token verification
- An app-owned `users` table as the canonical user record

Bike Doc V1 should not:

- Implement app-owned login, signup, logout, refresh, password reset, or
  credential management routes
- Use the external Firebase user identifier as the foreign key in product
  tables
- Expose Firebase-specific claims or identifiers in the public API beyond what
  is needed internally to map the authenticated user

## 2. Goals

- Keep authentication simple enough to implement quickly.
- Use a production-ready external identity provider rather than custom auth.
- Preserve an app-owned user model that is independent of the auth provider.
- Ensure all user-owned records in the database are scoped by internal
  `users.id`, not by the external Firebase identifier.
- Keep local development and automated tests deterministic and provider-free.

## 3. Non-Goals

- Do not add enterprise SSO, SAML, multi-tenancy, or advanced MFA policy
  controls.
- Do not add backend session cookies or server-managed login sessions.
- Do not define Android UI copy or exact client-side screen flows.
- Do not require any migration to a different auth provider in V1.

## 4. Identity Model

The system must distinguish between:

- External identity: the Firebase-authenticated person, identified by a stable
  Firebase subject/UID
- Internal application user: the Bike Doc `users` row that owns credits,
  profiles, repair sessions, artifacts, and all other product data

Required rules:

- The Firebase subject must map to `users.auth_subject`.
- The application must generate and own `users.id`.
- All product-table foreign keys must reference `users.id`.
- Product ownership checks must be performed using the resolved app user, not
  by comparing raw Firebase identifiers across domain tables.

## 5. Required Database Semantics

The `users` table remains the root identity table for the application.

Required fields:

| Field | Meaning |
|---|---|
| `id` | Internal Bike Doc user identifier, app-generated, stable, and used for all foreign keys. |
| `auth_subject` | Stable Firebase subject/UID for the authenticated person. Must be unique. |
| `email` | User email used for profile creation and display. |
| `display_name` | Human-friendly profile name. |
| `skill_level` | App-owned field with default `unknown`. |
| `created_at` | Creation timestamp. |

All user-owned tables must use an internal foreign key pattern like:

```text
<domain_table>.user_id -> users.id
```

Examples include, but are not limited to:

- bike profiles
- credits or balance records
- repair sessions
- repair history
- artifacts when they are user-owned

The implementation must not use Firebase UID as a direct foreign key in those
tables.

## 6. Client Authentication Requirements

The Android app must authenticate the user with Firebase Authentication and
obtain a Firebase ID token after sign-in.

The client must send requests to the backend with:

```http
Authorization: Bearer <firebase-id-token>
```

The client is responsible for:

- initiating sign-in with the chosen Firebase-auth-supported provider(s)
- obtaining the current ID token from Firebase
- refreshing that token through Firebase’s client SDK lifecycle as needed
- attaching the token to authenticated API requests

The backend is not responsible for interactive sign-in.

## 7. Backend Token Validation Requirements

The backend must validate Firebase bearer tokens before any authenticated route
executes product logic.

The validation boundary must confirm:

- the `Authorization` header is present
- the scheme is `Bearer`
- the token is a valid Firebase-issued ID token
- the token signature is valid
- the token is not expired
- the token is valid for the configured Firebase project/application
- a stable subject claim is present

The backend must reject invalid, missing, malformed, or expired tokens with
the public `401 Unauthorized` API error behavior defined by the backend specs.

The backend must normalize a valid token into an internal auth identity with
at least:

```text
subject
email
display_name
```

Normalization rules:

- `subject` comes from the stable Firebase user identifier
- `email` comes from the validated token when available
- `display_name` comes from the validated token when available
- if `display_name` is absent, the backend may derive it from the email local
  part

## 8. User Resolution And Auto-Creation

Once a token is validated, the backend must resolve the current app-owned user.

Resolution flow:

1. Read the normalized external subject from the validated identity.
2. Look up an existing `users` row by `auth_subject`.
3. If found, use that row as the current user.
4. If not found, create a new `users` row if the required fields can be
   derived.

Required creation behavior:

- `auth_subject` is set from the Firebase subject
- `email` must be present and non-blank for first-time auto-create
- `display_name` must be non-blank, using a derived fallback when necessary
- `skill_level` defaults to `unknown`
- the operation must be safe under concurrent first requests for the same
  Firebase subject

If token validation succeeds but the required fields for a new app user cannot
be derived, the backend must return `401 Unauthorized` with the
`user_mapping_required` behavior defined in the auth spec.

## 9. Ownership And Authorization Rules

Authentication and ownership are separate concerns.

Authentication answers:

- who is this person?

Authorization and ownership answers:

- which app data belongs to them?
- which resources can they read or mutate?
- how many credits do they have?

Required rule:

- all ownership checks must use the resolved internal `users.id`

This means the backend must:

- resolve the authenticated app user first
- pass the app user or app user ID into services
- query user-owned rows using internal `user_id`
- reject access to rows that do not belong to the resolved app user

Credit accounting must also be keyed by internal `users.id`, not Firebase UID.

## 10. Local Development Requirements

Local development must remain simple and deterministic without contacting
Firebase.

The implementation must preserve the existing local development auth strategy
described in `docs/specs/apps/api-auth-dev.md`.

At minimum:

- local fixed-token auth must remain available
- test dependency overrides must remain available
- local/test auth must exercise the same user-mapping path used by production
  once an identity is established

Production auth and local dev auth must remain clearly separated by
configuration. Local-only auth modes must never be permitted in production.

## 11. Testing Requirements

The implementation must include tests that confirm the auth boundary works as
specified.

Required test coverage areas:

- missing `Authorization` header returns `401`
- malformed bearer header returns `401`
- invalid token returns `401`
- expired token returns `401`
- valid Firebase identity resolves to an existing app user
- valid Firebase identity auto-creates an app user when none exists
- first-time user creation requires a usable email
- fallback display-name derivation works when the provider display name is
  absent
- concurrent first-request creation does not produce duplicate users
- user-owned resource access is scoped by internal `users.id`
- local dev fixed-token mode still works
- API tests can continue to override current-user resolution without contacting
  Firebase

Tests do not need live Firebase network calls. Production verification may be
tested via mocking, fixtures, or a verification abstraction.

## 12. API Contract Requirements

The implementation must confirm to the current API contract assumptions:

- authenticated routes require bearer auth
- `/v1/me` returns the resolved app-owned user profile
- authenticated domain routes operate on resources owned by the current app
  user
- the API does not expose Firebase login or account-management routes

If needed, the human-readable API descriptions may be updated to make Firebase
Auth explicit, but the public contract should continue to describe the backend
as consuming external bearer tokens rather than implementing login itself.

## 13. Configuration Requirements

Production configuration must support Firebase-backed token verification.

The implementation must define and document the configuration needed to:

- enable production auth mode
- identify the Firebase project/application the backend trusts
- provide any backend credentials or runtime configuration required for token
  verification

Configuration requirements must follow the backend configuration conventions:

- typed settings
- environment-variable driven configuration
- documented variables in the repository environment example
- explicit validation preventing insecure production configuration

## 14. Operational Requirements

The production implementation should be operationally simple.

Required properties:

- no app-owned password storage
- no backend-managed login flow
- no backend refresh-token implementation
- no dependency on storing Firebase tokens in the database for normal request
  authentication
- ability to disable or reject local-only auth modes in production

The backend should treat Firebase as the identity provider and Bike Doc as the
owner of product data and permissions.

## 15. Acceptance Criteria

The implementation is complete when all of the following are true:

- A signed-in Firebase user can call authenticated Bike Doc API routes with a
  bearer token.
- The backend validates that token before product logic executes.
- The backend maps the Firebase subject to an app-owned `users` row.
- New authenticated users are auto-created when required profile fields are
  available.
- All user-owned domain data is keyed by internal `users.id`.
- No domain table relies on Firebase UID as its foreign key.
- The API continues to expose only app-owned user records and product
  resources.
- Local development and test auth remain deterministic and provider-free.
- Production configuration cannot accidentally enable local dev auth.

## 16. Deferred Work

The following are explicitly deferred unless requirements change:

- enterprise SSO
- SAML or OIDC federation beyond standard Firebase-supported consumer auth
- multi-tenant identity separation
- provider migration support
- advanced MFA policy enforcement
- custom backend session management
- app-owned credentials and password reset flows
