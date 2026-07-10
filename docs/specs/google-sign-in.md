# Bike Doc Google Sign-In Spec

Status: Draft v0.2
Last updated: 2026-07-07

This spec defines the desired Google sign-in behavior for Bike Doc. It is a
delta on top of the existing Firebase auth implementation: Firebase remains
the sole identity system, the Android app performs interactive sign-in, and
the backend continues to accept Firebase-issued ID tokens as bearer tokens.

Where this spec conflicts with the Android MVP auth section that says
email/password only, this spec supersedes that subsection for the Google
sign-in feature.

## References

- Product design: `docs/specs/bike-doc.md`
- Android MVP spec: `docs/specs/android/mvp-spec.md`
- Firebase auth implementation: `docs/specs/apps/api-auth-firebase-implementation.md`
- Backend local/test auth: `docs/specs/apps/api-auth-dev.md`
- Public API contract: `docs/specs/openapi.yaml`

## 1. Decision

Bike Doc must support Google sign-in in addition to email/password sign-in.
Both methods must go through Firebase Authentication.

Required decisions:

- Firebase Authentication remains the only production auth provider boundary.
- The backend must continue to validate Firebase ID tokens; it must not perform
  interactive Google OAuth flows.
- The Android app owns the Google sign-in user interaction and exchanges the
  resulting Google credential for a Firebase-authenticated user.
- A person with an existing email/password Firebase account who signs in with
  Google using the same email must be guided into linking Google to the
  existing Firebase account, not silently creating a separate Bike Doc account.
- The app should present Google sign-in as `Continue with Google` on both the
  Sign In and Create Account tabs.

## 2. Goals

- Allow users to authenticate with Google while preserving the existing
  email/password flow.
- Keep Firebase as the canonical identity system for Android and backend API
  access.
- Avoid duplicate Bike Doc users for the same person when they move from
  email/password to Google sign-in.
- Keep backend product services independent of provider-specific auth details.
- Preserve deterministic local development and API tests.

## 3. Non-Goals

- Do not add app-owned OAuth routes, callback routes, refresh-token routes, or
  backend-managed sessions.
- Do not replace email/password sign-in.
- Do not add Google Drive, Gmail, Calendar, or other Google account data
  authorization scopes.
- Do not add phone auth, passkeys, magic links, Apple sign-in, SAML, or
  enterprise SSO as part of this feature.
- Do not automatically overwrite existing Bike Doc profile fields from Google
  claims after initial user creation.

## 4. Identity Model

The application still distinguishes between:

- External identity: the Firebase user, identified by Firebase subject/UID.
- Internal application user: the Bike Doc `users` row, identified by internal
  `users.id`.

Required rules:

- Backend authenticated ownership checks must continue to use internal
  `users.id`.
- Firebase UID must continue to map to `users.auth_subject`.
- Product tables must not use Google account IDs, Google OAuth subject IDs, or
  raw Firebase UIDs as foreign keys.
- Google sign-in must produce a Firebase-authenticated user before any backend
  API request is considered authenticated.

## 5. Firebase Project Configuration

The Firebase project must enable both sign-in providers:

- Email/password
- Google

Firebase Authentication must be configured to use one account per email
address. The account-linking policy in this spec depends on Firebase returning
an account-exists or credential-collision condition when Google sign-in uses an
email that already belongs to an email/password account.

Android Firebase app configuration must include every application ID that will
be used to run the app.

Required Android app IDs:

| Build | Application ID |
|---|---|
| Debug | `com.bikedoc.android.debug` |
| Release | `com.bikedoc.android` |

Each configured Android app must include the appropriate SHA certificate
fingerprints:

- Debug SHA-1, and preferably SHA-256
- Release SHA-1, and preferably SHA-256, before production distribution

After enabling the Google provider or changing Android app fingerprints, the
updated `google-services.json` must be downloaded and committed or otherwise
provided through the project's accepted secrets/configuration process.

The app must use the web client ID generated for the Firebase/Google auth
configuration when requesting a Google ID token through Android Credential
Manager. The implementation should read this from Android resources, preferring
the Google Services plugin's generated `default_web_client_id` value. If that
resource is not generated reliably for every build variant, the app should
define an explicit variant resource such as `google_web_client_id`, and that
resource must match the Firebase/Google auth configuration for the variant.

## 6. Android Client Behavior

### 6.1 Auth Methods

The Auth screen must support:

- Email/password sign-in
- Email/password account creation
- Google sign-in

Google sign-in is also a sign-up path. A first-time Google user who
successfully authenticates through Firebase should land in the same
post-authenticated app flow as a first-time email/password user.

### 6.2 UI Requirements

The Auth screen must show a persistent `Continue with Google` action on both:

- Sign In tab
- Create Account tab

The Google action must be visually separate from the email/password submit
button. The screen should not imply that Google sign-in requires entering the
email/password fields.

Required UI behavior:

- The Google action is independent of email/password form validation. It remains
  available when the email/password fields are empty or invalid, unless an auth
  operation is already in progress.
- Disable both the Google action and the email/password submit action while any
  auth operation is already in progress.
- Model the active operation in UI state, for example as an `AuthOperation?`
  value such as `EmailPassword` or `Google`, rather than adding independent
  loading booleans.
- Treat ordinary user cancellation of the Google picker as a silent no-op:
  stop loading, remain on Auth, do not navigate, and do not show an error.
- Show a user-readable error for real Google sign-in failures, including no
  available Google credential, network/provider failure, missing or invalid ID
  token, or Firebase sign-in failure.
- On success, navigate to Home using the same navigation behavior as
  email/password success.
- Do not require a separate "create account with Google" form.
- User-facing auth errors, validation messages, and link-state messages must be
  represented in app state as typed reasons or message models, then mapped to
  Android string resources in Compose. The implementation should move existing
  email/password validation and error copy out of `AuthViewModel` as part of
  this work.

### 6.3 Credential Manager

The Android implementation must use AndroidX Credential Manager with Google
Identity `GetGoogleIdOption` for Google sign-in. It must not use the legacy
`GoogleSignInClient` path unless Credential Manager proves incompatible with a
supported device and a separate spec update accepts that fallback.

Initial implementation should use an explicit `Continue with Google` button.
Automatic sign-in or an automatic bottom-sheet prompt may be added later, but
is not required for this feature.

The client flow is:

1. User selects `Continue with Google`.
2. Android launches the Credential Manager Google sign-in flow.
3. The app receives a Google ID token.
4. The app creates a Firebase credential from that token.
5. The app calls Firebase Authentication `signInWithCredential`.
6. The Firebase SDK sets the current Firebase user.
7. The app navigates to Home.
8. Existing API infrastructure retrieves the Firebase ID token and sends it as
   a bearer token on backend requests.

The app must not persist Google ID tokens or Firebase ID tokens manually.

The explicit `Continue with Google` request must allow any Google account
available through Credential Manager so the same action works for first-time
Google sign-up and returning Google sign-in. Do not filter the request to only
previously authorized accounts for this feature.

The v1 implementation should not include a nonce in the Google ID token request.
The backend never accepts raw Google ID tokens, and Firebase handles the
client-to-Firebase provider exchange.

### 6.4 Auth Provider Boundary

Android ViewModels and repositories must continue to depend on the
`AuthProvider` interface rather than directly depending on `FirebaseAuth`,
Credential Manager, or Google sign-in classes.

`AuthProvider` should expose Google sign-in and provider-linking operations in
addition to the existing email/password operations. Because Credential Manager
launches interactive UI, the Google sign-in operation may accept the current
Android `Activity` or equivalent UI `Context` supplied by the screen-level
Composable. The ViewModel must not directly depend on `FirebaseAuth`,
Credential Manager, or Google sign-in classes.

The concrete Firebase implementation owns:

- launching or delegating the Credential Manager request
- converting the Google ID token into a Firebase credential
- calling Firebase sign-in
- linking a pending Google credential to the currently signed-in Firebase user
- mapping Firebase and Credential Manager failures into app-level auth results

ViewModels should only coordinate UI state, validation, success navigation, and
typed displayable errors.

The Google sign-in result model must explicitly represent the link-required
case with an opaque pending credential object. For example, the provider may
return `LinkRequired(email, pendingCredential)`. The ViewModel may retain the
opaque pending credential in private transient state while exposing only the
linking mode, email, and typed messages through UI state. The pending credential
must not be stored on disk or hidden as provider-global mutable state.

## 7. Account Linking Behavior

### 7.1 Required Policy

If a user already has an email/password Firebase account and then attempts
Google sign-in with the same email, Bike Doc must link Google to the existing
Firebase account instead of creating a duplicate Bike Doc account.

This is required because the backend maps app users by Firebase UID. If Google
sign-in created a distinct Firebase UID for the same person, the backend would
auto-create a separate `users` row, splitting that person's bikes, sessions,
credits, and profile data.

### 7.2 Existing Email/Password Account

When Google sign-in fails with an account-exists or credential-collision
condition for the Google email:

1. The app must tell the user that an account already exists for that email.
2. The app must switch to the Sign In tab, prefill the Google email when
   available, and ask the user to sign in with email/password first.
3. After successful email/password sign-in, the app must link the pending
   Google credential to the currently signed-in Firebase user.
4. After successful linking, future Google sign-ins for that email should sign
   into the same Firebase user.
5. The app must force-refresh the Firebase ID token after linking succeeds.
6. The app must navigate to Home after linking and token refresh succeed.

The pending Google credential must be kept only as transient in-memory state
for the current auth flow. It must not be written to disk.

The linking step uses the existing Sign In tab, not a separate screen. During
the link-required state, the screen should show copy equivalent to:

`An account already exists for {email}. Sign in with your password once to link
Google to this Bike Doc account.`

The primary email/password action remains `Sign In`. The screen must also offer
a lightweight cancel action, such as `Cancel Google linking`, that clears the
pending credential and restores the normal Sign In tab. The regular
`Continue with Google` action remains available after cancellation.

If Google sign-in is initiated from the Create Account tab and linking is
required, the app must switch to the Sign In tab for the linking step. Provider
linking is not supported through email/password account creation in this
feature.

The pending Google credential must be cleared when:

- linking succeeds
- the user cancels Google linking
- the user switches to Create Account
- the user starts Google sign-in again
- the signed-in Firebase user's email does not match the pending Google email
- the user leaves the Auth screen

The pending credential should not be cleared only because the user edits the
password field. If the user edits the prefilled email field, keep the pending
credential until submit so the app can deliberately validate and show the
mismatch error if needed.

### 7.3 Already Linked Account

If Google is already linked to the Firebase user, `Continue with Google` should
complete a normal Firebase sign-in and navigate to Home.

### 7.4 Different Email

If a user is signed in with one email/password account and attempts to link a
Google credential for a different email, the app must not silently link it.
The email field remains editable during the linking step, but after successful
email/password sign-in the app must compare the signed-in Firebase user's email
with the pending Google email before linking. If they differ, the app must not
link, must clear the pending credential, and must show an error explaining that
the Google account email does not match the signed-in account.

Future support for changing account emails or merging accounts must be handled
by a separate spec.

This feature does not add a signed-in account-settings entry point for linking
Google. The different-email rule applies to the Auth-screen linking flow and may
be reused by a future account-settings linking feature.

### 7.5 Merge Policy

This feature does not merge two existing Bike Doc users. The supported path is
provider linking before a second app user is created.

If duplicate Firebase users and duplicate Bike Doc users already exist before
this feature is released, cleanup requires an administrative migration or
support workflow outside this spec.

## 8. Backend Behavior

The backend should not need a new public auth API for Google sign-in.

Required backend behavior:

- Continue accepting `Authorization: Bearer <firebase-id-token>`.
- Continue validating Firebase tokens for the configured Firebase project.
- Continue resolving app users by Firebase subject/UID.
- Continue auto-creating the app-owned `users` row when a first-time valid
  Firebase identity reaches the API and required profile fields are available.
- Continue rejecting valid tokens that cannot be mapped to a usable app user
  with the existing `user_mapping_required` behavior.
- Do not add a new `email_verified` gate for this feature. The backend should
  continue requiring a non-blank email from the validated Firebase identity.

The backend must not:

- accept Google ID tokens directly as API bearer tokens
- perform Google OAuth code exchange
- expose provider-linking routes
- store Google provider IDs or Google OAuth claims in product tables

The backend may store the initial email and display name derived from Firebase
token claims when creating a user. It must not automatically overwrite an
existing user's display name on later requests simply because Google claims
contain a different profile name.

## 9. Display Name And Email Rules

On first app-user creation:

- `email` must come from the validated Firebase identity and must be non-blank.
- `display_name` may come from Firebase token name/display-name claims.
- If no display name is present, the backend may derive one from the email
  local part.

After app-user creation:

- Existing `display_name` must not be overwritten automatically by Google
  profile claims.
- Email synchronization may only be added deliberately with tests and a clear
  product rule.

## 10. Error Handling

The Android app must distinguish at least these cases:

| Case | Desired user behavior |
|---|---|
| User cancels Google picker | Return to Auth screen without navigating or showing an error. |
| No Google credential available | Keep user on Auth screen and show a retryable message. The message should explain that no Google account is available on the device and that the user can add one in Android settings or use email/password. |
| Network/provider failure | Show a retryable sign-in error. |
| Existing email/password account needs linking | Switch to Sign In, prefill the email when available, ask user to sign in with email/password, then link Google. |
| User cancels the linking step | Clear the pending credential and restore the normal Sign In tab. |
| Google email differs from signed-in email during linking | Do not link; clear the pending credential; show a mismatch error. |
| Post-link Firebase ID token force-refresh fails | Sign out and show a retryable auth error on the Auth screen. |
| Firebase token accepted by client but backend returns 401 | Preserve existing forced-refresh retry; sign out if retry also fails. |

All user-facing copy should live in Android string resources.

## 11. Testing Requirements

Android unit tests should cover:

- `AuthViewModel` starts Google sign-in and navigates on success.
- Google picker cancellation is silent.
- Google sign-in failure maps to a typed displayable error.
- Existing-account/link-required result moves the UI into the expected
  email/password linking flow.
- Successful email/password sign-in with a pending Google credential links the
  provider and navigates to Home.
- Link attempt is rejected when the Google email does not match the signed-in
  Firebase account email, and clears the pending credential.
- Cancelling the linking step clears the pending credential and restores normal
  Sign In behavior.

Provider integration should be isolated behind fakes in unit tests. Tests must
not call real Firebase, Google, or Credential Manager services.

Backend tests do not need Google-specific cases unless backend token parsing or
user mapping behavior changes. Existing Firebase ID token validation and user
mapping tests remain the relevant backend coverage.

Compose UI tests are not required for v1 unless the UI implementation moves
meaningful logic out of the ViewModel. The minimum automated coverage is focused
`AuthViewModel` unit tests plus the standard Android compile, formatting, lint,
and unit-test checks.

Manual QA should verify:

- New Google-only user can authenticate and reach Home.
- Existing email/password user can link Google and later sign in with Google.
- Existing email/password user is not duplicated when using Google with the
  same email.
- Debug build uses the correct Firebase app configuration and SHA fingerprint.
- Release build configuration is prepared before production distribution.

## 12. Implementation Notes

The expected implementation surface is primarily Android:

- Add Credential Manager and Google ID dependencies.
- Extend `AuthProvider` with Google sign-in and provider-linking operations,
  including an explicit link-required result with an opaque pending credential.
- Implement Google sign-in and credential linking in the Firebase auth
  provider.
- Update `AuthViewModel` state and events for Google sign-in and linking.
- Update `AuthScreen` with the `Continue with Google` action and errors.
- Add string resources for new copy and migrate existing auth validation/error
  copy out of the ViewModel.
- Add or update Android unit tests.

Backend changes and backend tests are not part of the expected v1
implementation unless existing Firebase token validation or user mapping does
not already satisfy this spec.
