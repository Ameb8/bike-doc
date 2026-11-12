# Bike Doc Android — MVP Frontend Spec

**Scope:** Auth, home, bike management, repair diagnostics, and guided inspections
**Status:** Draft v0.4
**Last updated:** 2026-11-12

---

## 1. Overview

This spec covers authentication, home, bike management, repair-diagnostic
chat, guided inspection chat, inspection reports, and the handoff from an
inspection finding into a new repair session. Plan review, guided repair
execution, and repair completion remain outside this Android MVP.

`docs/specs/inspection-sessions.md` is canonical for inspection product
behavior. `docs/specs/openapi.yaml` is canonical for public wire shapes. This
document makes the Android presentation, navigation, state, and client-side
architecture decisions for those contracts. Android implementation must not
begin against an inspection wire shape until the OpenAPI contract and this
document agree. A disagreement among these documents must be surfaced rather
than resolved silently in client code.

Repair and inspection are workflows of one durable bike-session product
record. Shared Android transport and conversation code must therefore use
workflow-neutral bike-session language. Diagnostic and inspection packages
may retain workflow-specific presentation names where the behavior genuinely
differs.

All screens are user-specific and require an authenticated Firebase user.
Unauthenticated users cannot reach any screen beyond the Auth screen.

---

## 2. Authentication

### 2.1 Approach

Firebase Auth is the sole auth mechanism for V1. The Android app integrates
the Firebase Android SDK directly. The backend validates Firebase ID tokens
on every request via the existing Firebase Auth middleware.

- **Sign-in method:** Email/password only for V1. No social login, no phone
  auth, no magic links.
- **SDK:** `com.google.firebase:firebase-auth-ktx`
- **Token management:** The Firebase SDK handles token refresh automatically.
  The app retrieves a fresh ID token before each API call using
  `FirebaseAuth.getInstance().currentUser?.getIdToken(false)`.
- **No custom token storage.** Do not cache or persist ID tokens manually.
  Always fetch from the Firebase SDK. The SDK caches and refreshes internally.

### 2.2 Auth Flow

On app launch, check `FirebaseAuth.getInstance().currentUser`:

- If non-null → user is signed in → proceed to Home screen.
- If null → show Auth screen.

The Auth screen has two tabs: **Sign In** and **Create Account**. Users
create their Bike Doc account directly in the app via
`FirebaseAuth.createUserWithEmailAndPassword()` — no Firebase console access
is required. On first successful authentication (sign-in or account
creation), the backend auto-creates the BikeDoc `users` row when it receives
the first authenticated request with a valid Firebase identity.

For V1, no email verification gate is applied. Accounts are active
immediately on creation.

On successful sign-in or account creation the app navigates to Home and the
Auth screen is removed from the back stack.

On sign-out (accessible from the Home screen overflow menu) the app calls
`FirebaseAuth.getInstance().signOut()` and navigates back to Auth, clearing
the back stack.

### 2.3 Attaching the Token to API Calls

Every API call must include the Firebase ID token as a Bearer token:

```
Authorization: Bearer <firebase_id_token>
```

Retrieve the token before constructing the request:

```kotlin
suspend fun getAuthToken(): String {
    val user = FirebaseAuth.getInstance().currentUser
        ?: throw AuthException("No signed-in user")
    return user.getIdToken(false).await().token
        ?: throw AuthException("Token retrieval returned null")
}
```

On a 401 response, force-refresh the token (`getIdToken(true)`) and retry
once. If the retry also returns 401, sign the user out and navigate to the
Auth screen.

---

## 3. Architecture

### 3.1 Tech Stack

| Concern | Choice |
|---|---|
| Language | Kotlin |
| UI | Jetpack Compose |
| Navigation | Navigation Compose (`androidx.navigation:navigation-compose`) |
| State | ViewModel + StateFlow |
| HTTP client | Retrofit 2 (`com.squareup.retrofit2:retrofit`) |
| JSON | kotlinx.serialization (via `retrofit2-kotlinx-serialization-converter`) |
| Auth | Firebase Auth Android SDK |
| SSE streaming | OkHttp `EventSource` (via `okhttp-sse`) |
| Local cache | None for V1 (see §3.4) |
| DI | Hilt |

Retrofit is used for all request/response API calls. OkHttp is the
underlying transport and is also used directly for the SSE stream (Retrofit
does not model streaming responses). The same `OkHttpClient` singleton is
shared between Retrofit and `SseEventSource`.

### 3.2 Module / Package Layout

```
com.bikedoc.android
├── auth/
│   ├── AuthScreen.kt
│   ├── AuthViewModel.kt
│   └── FirebaseAuthProvider.kt          ← token fetching utility
├── home/
│   └── HomeScreen.kt                    ← entry point after auth
├── bikes/
│   ├── BikeListScreen.kt
│   ├── BikeListViewModel.kt
│   ├── BikeEditScreen.kt
│   └── BikeEditViewModel.kt
├── sessions/
│   ├── conversation/
│   │   ├── ConversationController.kt    ← load/replay/submit/reconnect reducer
│   │   ├── ConversationState.kt
│   │   ├── ConversationContent.kt       ← shared hoisted chat presentation
│   │   └── SseEventSource.kt            ← generic bike-session SSE wrapper
│   ├── diagnostic/
│   │   ├── DiagnosticChatScreen.kt
│   │   └── DiagnosticChatViewModel.kt
│   ├── inspection/
│   │   ├── InspectionChatScreen.kt
│   │   ├── InspectionChatViewModel.kt
│   │   ├── InspectionReportScreen.kt
│   │   └── InspectionReportViewModel.kt
│   └── models/
│       ├── ChatMessage.kt               ← local UI message model
│       └── SseEvent.kt                  ← parsed generic event types
├── api/
│   ├── BikeDocApiClient.kt              ← Retrofit service interfaces
│   ├── BikeDocApiService.kt             ← Retrofit request declarations
│   ├── models/                          ← response/request data classes
│   └── ApiResult.kt                     ← sealed class: Success / Error / Loading
└── navigation/
    └── AppNavGraph.kt
```

### 3.3 Navigation Graph

```
AuthScreen
    └─(on success)→ HomeScreen
                        ├── BikeListScreen (browse mode)
                        │       └── BikeEditScreen  (new or existing bike)
                        ├── BikeListScreen (repair resume mode)
                        │       └── DiagnosticChatScreen
                        ├── DiagnosticChatScreen            ← new or resumed repair
                        └── BikeListScreen (inspection mode)
                                ├── InspectionChatScreen    ← new or resumed inspection
                                └── InspectionReportScreen  ← completed inspection
                                        └── DiagnosticChatScreen
                                            ← one eligible finding handoff
```

`HomeScreen` is the top-level destination after auth. It is never popped off
the back stack during a session. The back button on Home has no effect or
exits the app.

All destinations live in a single `NavHost` in `MainActivity`. There is no
bottom navigation bar for V1.

`DiagnosticChatScreen` and `InspectionChatScreen` are separate workflow
destinations over one shared conversation module. This keeps workflow copy,
progress, completion actions, and report navigation explicit without copying
session loading, SSE, turn submission, input rendering, or photo preparation.
A `BaseChatViewModel` inheritance hierarchy must not be introduced.

The routes are `sessions/{sessionId}/diagnostic` and
`sessions/{sessionId}/inspection`. Both accept only `sessionId`; after loading,
the destination must verify the returned workflow and phase before enabling
input. `InspectionReportScreen` uses
`sessions/{sessionId}/inspection-reports/{reportId}`. A mismatched workflow or
phase is a controlled state error, never a reason to reinterpret the session.

### 3.4 Local Cache

No local persistence (Room, DataStore) for V1. All lists and profile data
are fetched from the backend on each screen entry. Add loading and empty
states to every list screen.

Rationale: the user base is small, the backend is the source of truth, and
caching adds complexity with no meaningful offline use case for this data.

---

## 4. API Contracts

All requests target the backend base URL configured in `BuildConfig.API_BASE_URL`.

### 4.1 Backend Routes Used by These Screens

| Method | Path | Used by |
|---|---|---|
| `GET` | `/v1/me` | Home screen |
| `GET` | `/v1/bikes` | Bike list screen |
| `POST` | `/v1/bikes` | Add bike |
| `GET` | `/v1/bikes/{bikeId}` | Bike edit screen |
| `PATCH` | `/v1/bikes/{bikeId}` | Save bike edits |
| `DELETE` | `/v1/bikes/{bikeId}` | Remove bike |
| `GET` | `/v1/sessions?bike_id={bikeId}&workflow={workflow}` | Workflow-specific discovery |
| `POST` | `/v1/sessions` | Create a repair or inspection session |
| `GET` | `/v1/sessions/{sessionId}` | Load authoritative session state |
| `POST` | `/v1/sessions/{sessionId}/turns` | Submit a user turn |
| `GET` | `/v1/sessions/{sessionId}/events` | SSE replay/live stream |
| `GET` | `/v1/sessions/{sessionId}/reports` | List session reports |
| `GET` | `/v1/sessions/{sessionId}/reports/{reportId}` | Open a report |
| `POST` | `/v1/sessions/{sessionId}/completion` | Finish an inspection early; repair completion remains workflow-specific |
| `POST` | `/v1/sessions/{sessionId}/cancellation` | Cancel an idle inspection |

All session routes and shared schemas use bike-session language. Android must
not call compatibility aliases, accept `repair_session_id` in a generic
response, or infer workflow from an ID prefix. Public session IDs use `ses_`.
Workflow-specific discovery always sends an explicit `workflow` filter; the
client never mixes repair and inspection sessions in one chooser.

### 4.2 Retrofit Setup

```kotlin
// In BikeDocApiClient.kt
@Singleton
class BikeDocApiClient @Inject constructor(
    private val okHttpClient: OkHttpClient,
) {
    val retrofit: Retrofit = Retrofit.Builder()
        .baseUrl(BuildConfig.API_BASE_URL)
        .client(okHttpClient)
        .addConverterFactory(
            Json.asConverterFactory("application/json".toMediaType())
        )
        .build()

    val service: BikeDocApiService = retrofit.create(BikeDocApiService::class.java)
}

// In BikeDocApiService.kt
interface BikeDocApiService {
    @GET("v1/me")
    suspend fun getMe(): UserProfile

    @GET("v1/bikes")
    suspend fun getBikes(
        @Query("limit") limit: Int = 50,
        @Query("cursor") cursor: String? = null,
    ): BikeListResponse

    @POST("v1/bikes")
    suspend fun createBike(@Body bike: BikeCreate): Bike

    @GET("v1/bikes/{bikeId}")
    suspend fun getBike(@Path("bikeId") bikeId: String): Bike

    @PATCH("v1/bikes/{bikeId}")
    suspend fun updateBike(@Path("bikeId") bikeId: String, @Body bike: BikePatch): Bike

    @DELETE("v1/bikes/{bikeId}")
    suspend fun deleteBike(@Path("bikeId") bikeId: String): Unit

    @POST("v1/sessions")
    suspend fun createSession(@Body body: BikeSessionCreate): BikeSession

    @GET("v1/sessions")
    suspend fun getSessions(
        @Query("bike_id") bikeId: String,
        @Query("workflow") workflow: SessionWorkflow,
        @Query("limit") limit: Int = 20,
        @Query("cursor") cursor: String? = null,
    ): BikeSessionListResponse

    @GET("v1/sessions/{sessionId}")
    suspend fun getSession(@Path("sessionId") sessionId: String): BikeSession

    @POST("v1/sessions/{sessionId}/turns")
    suspend fun createTurn(
        @Path("sessionId") sessionId: String,
        @Body body: TurnCreate,
    ): TurnAccepted

    @GET("v1/sessions/{sessionId}/reports")
    suspend fun getReports(
        @Path("sessionId") sessionId: String,
        @Query("limit") limit: Int = 50,
        @Query("cursor") cursor: String? = null,
    ): PhaseReportList

    @GET("v1/sessions/{sessionId}/reports/{reportId}")
    suspend fun getReport(
        @Path("sessionId") sessionId: String,
        @Path("reportId") reportId: String,
    ): PhaseReportEnvelope

    @POST("v1/sessions/{sessionId}/completion")
    suspend fun completeSession(
        @Path("sessionId") sessionId: String,
        @Body body: InspectionCompletion,
    ): InspectionCompletionResponse

    @POST("v1/sessions/{sessionId}/cancellation")
    suspend fun cancelSession(
        @Path("sessionId") sessionId: String,
        @Body body: InspectionCancellation,
    ): BikeSession
}
```

The `Authorization` header is attached by an OkHttp interceptor injected into
the shared `OkHttpClient`, so individual service methods do not take a token
parameter.

The SSE endpoint (`/v1/sessions/{sessionId}/events`) is consumed
directly via OkHttp `EventSource` in `SseEventSource.kt` — Retrofit is not
used for that route.

### 4.2.1 Conversation Photo Upload Policy

Repair-diagnostic and inspection uploads accept:

- `image/jpeg`
- `image/png`
- `image/webp`

The Android client must transcode unsupported selected-image formats before
upload. In particular, `HEIC` and `HEIF` images must be converted on-device
to a backend-accepted format, preferably `JPEG`, before calling
`POST /v1/artifacts`.

Users may attach photos to any turn when permitted by the current request.
The client must limit one pending turn to the shared maximum of three photos.
Repair turns upload `purpose = diagnostic_photo`; inspection turns upload
`purpose = inspection_photo`. Both multipart requests use `session_id`. The
resulting artifact IDs are included in the turn's `message.artifact_ids`.

Inspection photos are always optional in V1. A photo request with
`required = false` must render its stable `continue_without_photo` choice next
to the picker. Selecting it submits an ordinary choice/text turn with no
artifact and continues into the guided manual path; it is not an upload error.

This is an explicit MVP contract decision. The backend does not need native
`HEIC` / `HEIF` upload support for this version.

### 4.3 Bike Model (Expected Shape)

```kotlin
@Serializable
data class Bike(
    val id: String,
    val userId: String,
    val displayName: String,
    val hasSessions: Boolean,
    val make: String?,
    val model: String?,
    val modelYear: Int?,
    val bikeType: String,
    val frameMaterial: String?,
    val drivetrain: String?,
    val brakeType: String?,
    val wheelSize: String?,
    val tireSize: String?,
    val notes: String?,
    val createdAt: String,
    val updatedAt: String,
)
```

`hasSessions` indicates whether the authenticated user owns any bike session
for this bike. The Android app uses it only as a history/deletion hint. It
must not infer repair or inspection history from this value; workflow-specific
discovery always queries `/v1/sessions`.
This field is part of the canonical `Bike` object for both `GET /v1/bikes`
and `GET /v1/bikes/{bikeId}`.

```kotlin
@Serializable
data class BikeListResponse(
    val items: List<Bike>,
    val nextCursor: String?,
)
```

### 4.4 Bike Session Model and Discovery

The shared wire model is `BikeSession`, not `RepairSession`. It includes
`workflow`, `phase`, `status`, `safety_state`, `current_input_request`,
`inspection_progress`, `checklist_version`, `execution_progress`, `origin`,
`latest_reports`, `latest_event_id`, and timestamps. Inspection-only fields
are null for repair; repair-only progress is null for inspection. Android
replaces these projections from server responses and never increments
inspection progress locally.

`inspection_progress` contains `completed_checks`,
`total_applicable_checks`, and nullable `current_section`. Counts must be
non-negative and completed must not exceed total. `checklist_version` is
`general_inspection.v1` for the initial release and remains pinned for a
resumed session.

The only valid inspection phase/status combinations and Android presentation
are:

| Phase | Status | Android behavior |
|---|---|---|
| `inspection` | `created` | Show the preflight explanation and Begin action. |
| `inspection` | `running` | Show chat/replay with all conflicting input and terminal actions disabled. |
| `inspection` | `awaiting_user` | Show the current request or freeform retry input. |
| `inspection` | `blocked_safety` | Show blocking safety presentation and only server-permitted input. |
| `inspection` | `failed` | Show terminal failure; no report link. |
| `inspection` | `cancelled` | Show terminal cancellation; no report link. |
| `completed` | `completed` | Open the durable inspection report read-only. |

Inspection never uses `awaiting_decision`. A failed or cancelled inspection
remains in phase `inspection`; the client must not infer completion from a
terminal status alone.

Creation uses a workflow discriminator and a client idempotency key:

```kotlin
@Serializable
data class BikeSessionCreate(
    val bikeId: String,
    val workflow: SessionWorkflow,
    val clientSessionId: String,
    val origin: SessionOrigin? = null,
)

enum class SessionWorkflow { Repair, Inspection }
```

`origin` must be null except for a repair created from exactly one eligible
inspection finding. The client generates `clientSessionId` once per user
action and retains it for retries of that action.

Bike-scoped discovery uses
`GET /v1/sessions?bike_id={bikeId}&workflow={workflow}`.

- The endpoint must return only sessions owned by the authenticated user.
- Results must be sorted newest first.
- The backend default page size should be 20 when `limit` is omitted.
- The Android client calls this endpoint without a status filter and decides
  resumability client-side.
- The response must include enough `BikeSession` state for the client to
  decide which sessions are resumable.
- The Android chooser reads only the first page for MVP.
- A broad cross-bike session-history screen remains out of scope for this MVP.

Repair diagnostic sessions are resumable when `phase == diagnostic` and
status is `created`, `running`, `awaiting_user`, or `awaiting_decision`.
Inspection sessions are resumable when `phase == inspection` and status is
`created`, `running`, `awaiting_user`, or `blocked_safety`. A completed
inspection opens its report. Failed and cancelled inspections are read-only
terminal rows and offer creation of a new inspection.

### 4.5 SSE Event Schema

The events stream at `/v1/sessions/{sessionId}/events` is the primary
real-time channel during a conversation turn. Events are `text/event-stream`
lines with `id:`, `event:`, and `data:` fields. The `data:` line is the full
JSON-encoded `BikeSessionEvent` envelope, not just the event-specific inner
payload.

The following event types are consumed by the chat screen:

| Event type | Payload | UI effect |
|---|---|---|
| `turn.started` | `{ "turn_id": "...", "phase": "..." }` | Keep conflicting input/actions disabled |
| `assistant.delta` | `{ "text": "..." }` | Append to streaming assistant bubble |
| `assistant.message.completed` | `{ "message_id": "...", "full_text": "...", "artifact_ids": [...] }` | Finalise and persist the assistant bubble |
| `input.requested` | `{ "input_request": InputRequest }` | Render the appropriate input affordance |
| `artifact.referenced` | `{ "artifact": ArtifactRef }` | Optional UI hook for artifact provenance |
| `phase.report.created` | `{ "report_id": "...", "report_type": "...", ... }` | Capture the workflow report ID |
| `phase.transitioned` | `{ "from_phase": "...", "to_phase": "...", "status": "..." }` | Freeze chat and show completion banner |
| `safety.escalated` | safety payload | Show safety state prominently |
| `turn.completed` | `{ "turn_id": "...", "session": BikeSession }` | Replace session/input/progress and re-enable permitted actions |
| `error` | `{ "code": "...", "message": "...", "retryable": false }` | Show recoverable error and clear loading state |
| `heartbeat` | `{ "ok": true }` | Ignore for UI other than updating last seen cursor |

Unknown event types must be silently ignored; never crash on an unrecognised
event, but always advance the cursor. Inspection adds no progress event;
progress is reconciled from the authoritative `turn.completed` snapshot.

The `id` field on each SSE event is the public replay cursor. The client sends
this as `Last-Event-ID` on reconnect and may also send it as the `after`
query parameter.

### 4.6 TurnCreate Request

```kotlin
@Serializable
data class TurnCreate(
    val schemaVersion: String = "ai_turn.v1",
    val clientTurnId: String,                   // client-generated UUID
    val message: UserTurnMessage,
    val respondsToInputRequestId: String? = null,
)

@Serializable
data class UserTurnMessage(
    val text: String? = null,
    val artifactIds: List<String> = emptyList(),
)
```

### 4.7 Error Handling

Wrap all API calls in `ApiResult`. Retrofit calls should be wrapped in a
`safeApiCall` helper that catches `HttpException` and `IOException`:

```kotlin
sealed class ApiResult<out T> {
    data class Success<T>(val data: T) : ApiResult<T>()
    data class Error(
        val httpStatus: Int?,
        val code: String?,
        val message: String,
        val details: ErrorDetails? = null,
    ) : ApiResult<Nothing>()
    object Loading : ApiResult<Nothing>()
}

suspend fun <T> safeApiCall(call: suspend () -> T): ApiResult<T> = try {
    ApiResult.Success(call())
} catch (e: HttpException) {
    decodeApiError(e)
} catch (e: IOException) {
    ApiResult.Error(null, null, "Network error. Check your connection.")
}
```

`decodeApiError` must retain the stable backend `error.code` and bounded
details. Reducing a 409 to an HTTP status and message would make required
inspection recovery behavior impossible.

Surface errors to the user as a Snackbar with a retry action. Map common
codes to plain messages:

| HTTP Code | User message |
|---|---|
| 401 | "Session expired. Please sign in again." → sign out |
| 403 | "You don't have permission to do that." |
| 404 | "Not found." |
| 422 | Display `error.message` from response body |
| 5xx | "Something went wrong. Try again." |

Stable product codes override the generic HTTP mapping:

| Error code | Android behavior |
|---|---|
| `active_inspection_exists` | Validate the owner-safe `details.session_id`, then open that inspection. |
| `finding_already_handed_off` | Validate `details.session_id`, then open the existing repair session. |
| `session_busy` | Keep the current screen, reload the session, and explain that the active response must finish first. |
| `session_state_conflict` | Reload authoritative state and route to chat, report, or terminal presentation as appropriate. |
| `artifact_not_ready` | Keep the pending turn editable and offer retry when processing finishes. |
| `idempotency_conflict` | Do not generate a replacement key automatically; show a controlled failure and reload state. |

The backend error envelope is:

```json
{
  "error": {
    "code": "validation_error",
    "message": "Request validation failed.",
    "details": null
  }
}
```

Where details contain a session reference, the only supported shape is
`{"session_id":"ses_..."}`. The client must not navigate from an arbitrary
identifier embedded in a human-readable message.

### 4.8 Inspection Completion, Cancellation, and Report Shapes

The client models the two product-owned terminal actions separately:

```kotlin
@Serializable
data class InspectionCompletion(
    val schemaVersion: String = "inspection_completion.v1",
    val reason: String = "finish_early",
    val clientCompletionId: String,
)

@Serializable
data class InspectionCancellation(
    val clientCancellationId: String,
)
```

An early-completion response contains the authoritative completed
`BikeSession` and its `inspection_report.v1` report. Cancellation contains the
authoritative cancelled session and no report. The generic turn acceptance and
report envelopes use `session_id`; no Android DTO retains a
`repair_session_id` compatibility property.

Strictly decode an inspection report payload with these fields:

```text
schema_version
inspection_session_id
checklist_version
scope
outcome
summary
ride_guidance
coverage[]
findings[]
limitations[]
safety_flags[]
key_artifact_ids[]
```

Coverage entries retain `area`, `status`, `evidence_summary`, `check_ids`, and
`artifact_ids`. Findings retain `finding_id`, `area`, `component`,
`observation`, `condition`, `urgency`, `confidence`, `supporting_check_ids`,
`artifact_ids`, and `recommended_next_action`. Limitations retain `code`,
`area`, `check_ids`, `description`, and `artifact_ids`. Unknown enum values or
an unsupported schema version produce an unsupported-report state; they must
not be silently mapped to a reassuring default.

The decoder recognizes exactly these report enums for V1:

| Field | Values |
|---|---|
| `outcome` | `no_actionable_findings`, `monitoring_recommended`, `maintenance_recommended`, `diagnostic_follow_up_recommended`, `shop_assessment_recommended`, `unsafe_to_ride`, `incomplete` |
| `ride_guidance` | `no_known_blocking_issue`, `use_caution`, `do_not_ride`, `professional_assessment_required`, `not_assessed` |
| coverage `status` | `assessed`, `partially_assessed`, `skipped`, `unable_to_assess`, `not_applicable` |
| finding `urgency` | `routine`, `soon`, `before_next_ride`, `immediate` |
| `recommended_next_action` | `monitor`, `routine_maintenance`, `start_diagnostic`, `stop_riding`, `shop_assessment` |
| limitation `code` | `skipped`, `unable_to_assess`, `poor_visibility`, `contradictory_evidence`, `unsafe_to_continue`, `ended_early`, `other` |

`latest_reports` adds nullable `inspection_report_id`. A completed inspection
without that ID is a contract error: reload once, then show a controlled error
rather than reopening chat or inventing a report.

---

## 5. Screens

### 5.1 Auth Screen

**Route:** `auth`  
**ViewModel:** `AuthViewModel`

The Auth screen has two tabs: **Sign In** and **Create Account**. Users
switch between tabs freely before submitting. No Firebase console access is
required at any point; account creation is self-service within the app.

**State:**
```kotlin
enum class AuthMode { SignIn, CreateAccount }

data class AuthUiState(
    val mode: AuthMode = AuthMode.SignIn,
    val email: String = "",
    val password: String = "",
    val confirmPassword: String = "",   // only used in CreateAccount mode
    val isLoading: Boolean = false,
    val error: String? = null,
)
```

**UI:**
- App logo / wordmark at top center
- Tab row: "Sign In" | "Create Account"
- Email text field
- Password text field (obscured, with show/hide toggle)
- Confirm password text field — visible only when `mode == CreateAccount`
- Primary button: "Sign In" or "Create Account" depending on mode — disabled
  while `isLoading`
- Error message beneath button when `error != null`

**Sign In behavior:**
1. User enters credentials and taps "Sign In".
2. `AuthViewModel` calls `FirebaseAuth.getInstance().signInWithEmailAndPassword(email, password)`.
3. On success: navigate to Home, clear Auth from back stack.
4. On failure: set `error` to a plain message derived from the Firebase
   exception type. Do not expose raw Firebase error codes.

**Create Account behavior:**
1. Client-side validation before the Firebase call:
   - Email must not be blank.
   - Password must be at least 6 characters (Firebase minimum).
   - Confirm password must match password.
   - Show inline error and abort if any check fails.
2. `AuthViewModel` calls `FirebaseAuth.getInstance().createUserWithEmailAndPassword(email, password)`.
3. On success: navigate to Home, clear Auth from back stack. The backend
   auto-creates the `users` row on the first authenticated API call.
4. On failure: map Firebase exceptions to plain messages:
   - `FirebaseAuthWeakPasswordException` → "Password must be at least 6 characters."
   - `FirebaseAuthUserCollisionException` → "An account with this email already exists."
   - `FirebaseAuthInvalidCredentialsException` → "Enter a valid email address."
   - Default → "Account creation failed. Please try again."

**Display name:** Not collected at sign-up for V1. The backend derives a
non-empty display name from the email prefix when the auth provider does not
supply one.

---

### 5.2 Home Screen

**Route:** `home`  
**ViewModel:** `HomeViewModel`

**State:**
```kotlin
data class HomeUiState(
    val displayName: String? = null,
    val bikes: List<HomeBike> = emptyList(),
    val selectedBikeId: String? = null,
    val isLoading: Boolean = false,
    val isStartingRepair: Boolean = false,
    val isStartingInspection: Boolean = false,
    val error: String? = null,
)
```

**UI:**
- Top bar: "Bike Doc" title, overflow menu with "Sign Out"
- Greeting: "Hi, [displayName]" or empty while loading
- Card: "My Bikes" → navigates to `BikeListScreen` (browse mode)
- Repair-bike selector: defaults to "New bike" and lists existing bikes
- "Start New Repair" creates a diagnostic session for the selected bike. With
  "New bike", it first creates a minimal profile named "New bike".
- "Resume Repair" → navigates to `BikeListScreen` in resume selection mode
- "Inspect a bike" → navigates to `BikeListScreen` in inspection selection
  mode. Inspection requires an owned bike; the selection screen offers
  "Add bike" rather than creating an implicit "New bike" profile.

**Behavior:**
- On entry: `GET /v1/me`. On 401, force sign-out.
- Changing the selector changes only the pending start target; no session is
  created until "Start New Repair" is pressed.
- "Start New Repair" posts `BikeSessionCreate` with `workflow = repair`, null
  origin, and one `client_session_id` retained across retries.
- Session resume remains bike-centric: the user selects a bike after pressing
  "Resume Repair". Home does not expose a cross-bike session list.
- "Inspect a bike" does not create a session until the user selects a bike
  and confirms the inspection action. This makes active-session discovery and
  the one-active-inspection invariant visible before navigation.

---

### 5.3 Bike List Screen

**Route:** typed `BikeListRoute(mode)` where mode is `browse`,
`repair_resume`, or `inspection`

**ViewModel:** `BikeListViewModel`

**State:**
```kotlin
data class BikeListUiState(
    val bikes: List<Bike> = emptyList(),
    val isLoading: Boolean = false,
    val error: String? = null,
    val mode: BikeListMode = BikeListMode.Browse,
    val selectedBikeId: String? = null,
    val bikeSessions: List<BikeSession> = emptyList(),
    val isLoadingBikeSessions: Boolean = false,
    val deletingBikeId: String? = null,
    val isCreatingSession: Boolean = false,
)
```

**UI:**
- Top bar: "My Bikes" in browse mode, "Resume repair" in repair-resume mode,
  or "Inspect a bike" in inspection mode
- List of `BikeCard` composables showing: bike name, make/model/year, brief
  drivetrain/brake summary
- FAB: "Add Bike" → `BikeEditScreen`; visible in browse and inspection modes
- Empty state: "No bikes yet. Add your first bike." with "Add Bike" button

**BikeCard interactions:**
- Browse mode: tap → `BikeEditScreen`; long-press or swipe-to-reveal → delete.
- Repair-resume mode: bikes with `has_sessions` are candidates. On tap, query
  `GET /v1/sessions?bike_id={bikeId}&workflow=repair`; show the chooser only
  when repair sessions exist. Never infer repair history from `has_sessions`.
- Inspection mode: show every owned bike. On tap, query
  `GET /v1/sessions?bike_id={bikeId}&workflow=inspection`, then show its
  active/completed inspection state and a "Start inspection" action when no
  active inspection exists. No edit/delete action is shown in either
  workflow-selection mode.

Browse mode remains focused on bike management and does not expose session
chooser affordances.

**Session chooser sheet (resume selection mode):**
1. User taps a bike card.
2. Set `selectedBikeId` and `isLoadingBikeSessions = true`.
3. Call `/v1/sessions` with that bike ID and the mode's explicit workflow.
4. On success: show a bottom sheet for that bike containing:
   - Primary action: "Resume latest repair" or "Resume inspection" when a
     resumable workflow session exists
   - The primary action also shows identifying metadata for the target
     session using the same timestamp and status model as a session row
   - List of older sessions below, newest first, excluding the session
     already represented by the primary resume CTA
5. On error: clear `isLoadingBikeSessions`, dismiss any partial sheet state,
   show Snackbar. Do not offer session creation until session discovery
   succeeds.

Each session row in the chooser shows:
- Primary text: formatted `created_at` timestamp
- Status label: derived from session phase and status
- Secondary text: compact phase/activity description, for example
  "Diagnostic session" or "Guided inspection"
- Tappable older resumable rows also show a `Resumable` badge

The older-session list remains one flat newest-first list for MVP. Sessions
are not visually grouped by phase or status.

Status labels are mapped in workflow order as follows:
- `diagnostic + created` or `diagnostic + running` → `In progress`
- `diagnostic + awaiting_user` → `Awaiting your reply`
- `diagnostic + awaiting_decision` → `Awaiting your decision`
- `diagnostic + completed` → `Completed`
- later repair phases → `Moved beyond diagnosis`
- terminal repair states → `Closed`
- `inspection + created` or `inspection + running` → `In progress`
- `inspection + awaiting_user` → `Awaiting your reply`
- `inspection + blocked_safety` → `Safety concern — action needed`
- `completed + completed` for an inspection → `Report ready`
- `inspection + failed` → `Failed`
- `inspection + cancelled` → `Cancelled`

Resumability is workflow-specific:

- Repair diagnostic: `phase == diagnostic` and status is `created`, `running`,
  `awaiting_user`, or `awaiting_decision`.
- Inspection: `phase == inspection` and status is `created`, `running`,
  `awaiting_user`, or `blocked_safety`.

When multiple resumable sessions exist for the same bike, the primary
"Resume Latest Active Session" action must target the newest resumable session
using the ordering returned by the newest-first `/v1/sessions` response.

Sessions outside the selected workflow's resumable state must not drive the
primary resume CTA. Completed inspections are tappable report rows. Failed and
cancelled inspections are read-only rows with a "Start a new inspection"
action. Other non-resumable rows remain read-only.

Rows in the older-session list are tappable only when that session is
resumable. When multiple resumable sessions exist, the newest one is promoted
to the primary resume CTA and older resumable sessions remain listed as
tappable rows below it. Non-resumable sessions are shown as read-only rows
with status labels and no navigation action. In particular, sessions outside
the diagnostic phase must never navigate into `DiagnosticChatScreen` in this
MVP.

**Resume session from chooser:**
- Tapping the primary action or a resumable session row navigates to
  `DiagnosticChatScreen(sessionId)` for repair or
  `InspectionChatScreen(sessionId)` for inspection.
- The bike selection screen is cleared from the back stack so back returns to
  Home.
- Resuming a session does not require an additional confirmation dialog.

**Delete flow (browse mode):**
- Bikes with any owned bike-session history, including inspection-only history,
  are not deletable in this MVP.
- When deletion is blocked, show a plain explanation such as
  "This bike can't be removed because it has session history."
- Only bikes with no owned sessions may show the destructive delete
  action.
- `hasSessions` is a UI hint, not a lock. If `DELETE /v1/bikes/{bikeId}`
  still fails because session history now exists, treat that as a normal
  conflict, show the same explanation, and refresh the affected bike row or
  whole list so the delete affordance disappears.
- Confirmation dialog for deletable bikes: "Remove [bike name]?"
- On confirm: `DELETE /v1/bikes/{bikeId}`. Track with `deletingBikeId`.
- On success: remove card from list without a full refresh.
- On error: show Snackbar.

**Inspection creation:** "Start inspection" posts `BikeSessionCreate` with
`workflow = inspection` and a retained client UUID. On success, navigate to
`InspectionChatScreen`. If the server returns `active_inspection_exists`, use
the bounded `details.session_id` to open that owned session instead of showing
a generic failure. This server response also closes the race between discovery
and creation.

**Data loading:** `GET /v1/bikes` on entry. Selection modes query only after a
bike is selected. Ignore `nextCursor` for V1 and render the first session page.

---

### 5.4 Bike Edit Screen

**Route:** `bikes/new` or `bikes/{bikeId}/edit`  
**ViewModel:** `BikeEditViewModel`

Handles both create and edit. When `bikeId` is present, pre-fill from
`GET /v1/bikes/{bikeId}` on entry.

**State:**
```kotlin
data class BikeEditUiState(
    val isNew: Boolean = true,
    val profile: BikeProfileEdit = BikeProfileEdit(displayName = ""),
    val isLoading: Boolean = false,
    val isSaving: Boolean = false,
    val error: String? = null,
    val validationErrors: Map<String, String> = emptyMap(),
)
```

**UI:**
- Top bar: "Add Bike" or "Edit Bike", back arrow, "Save" action
- Fields (all optional except `displayName`) use the `bike_profile.v2`
  projection. The editor groups the structured values into Identity and frame,
  independently positioned front/rear brakes, drivetrain roles, independently
  positioned front/rear wheels and tires, suspension, cockpit and seating, and
  electric assist. `brake_type`, free-text `drivetrain`, `wheel_size`, and
  `tire_size` are deprecated compatibility summaries and are never editable.
- Component presence is entered independently as `unknown`, `present`, or
  `absent`; an absent component must not retain technical detail fields.
- If editing: "Remove this bike" destructive button at bottom

**Validation (client-side):**
- `displayName` must not be blank.
- `modelYear` if provided must be an integer between 1880 and 2100.
- Inline field errors via `validationErrors`.

**Save flow:**
- Create: `POST /v1/bikes` → navigate back to BikeList.
- Edit: `PATCH /v1/bikes/{bikeId}` → navigate back.
- Show `isSaving` spinner on Save button and disable the form during the call.

---

### 5.5 Bike-Scoped Session Discovery

The app may discover prior sessions for a selected bike via
`GET /v1/sessions?bike_id={bikeId}&workflow={workflow}`. Resuming a repair
navigates to `DiagnosticChatScreen`; resuming an inspection navigates to
`InspectionChatScreen`; opening a completed inspection navigates directly to
its latest inspection report. Both chat destinations rebuild the retained
conversation from `/v1/sessions/{sessionId}/events?after=0`.

A broad cross-bike history screen remains out of scope. Bike-scoped discovery,
active inspection resumption, and completed inspection report opening are in
scope and must not be described as deferred.

---

### 5.6 Shared Conversation and Diagnostic Chat

**Route:** typed `DiagnosticChatRoute(sessionId)`

**ViewModel:** `DiagnosticChatViewModel`

`DiagnosticChatScreen` is the repair workflow's presentation over the shared
conversation module. The module's interface accepts a session ID and exposes
authoritative session state, reduced messages, input state, attachment state,
and actions to submit/retry a turn. It hides session loading, cursor replay,
SSE reconnect/backoff, event parsing/reduction, optimistic delivery state,
and photo preparation/upload. The same interface is used by inspection.

`DiagnosticChatViewModel` owns diagnostic presentation and navigation policy
and composes a `ConversationController`; it must not reimplement the shared
transport state machine. `InspectionChatViewModel` is the second adapter at
this seam. The shared module must have no inspection checklist rules or repair
diagnosis rules.

Keep the shared interface small:

```kotlin
interface ConversationController {
    val state: StateFlow<ConversationUiState>
    fun dispatch(action: ConversationAction)
}

interface ConversationControllerFactory {
    fun create(sessionId: String, expectedWorkflow: SessionWorkflow): ConversationController
}
```

`ConversationAction` covers start/close, draft changes, photo selection and
retry, stable choice/confirmation selection, turn submission, and failed-turn
retry. The controller owns ordering constraints and rejects actions that are
invalid for its current authoritative state. Workflow ViewModels translate
screen intents into these actions and own only workflow-specific terminal
operations and navigation. Tests use the same interface through a fake
controller or fake repositories/event source beneath the real controller.

#### 5.6.1 State

```kotlin
data class ConversationUiState(
    val session: BikeSession? = null,
    val messages: List<ChatMessage> = emptyList(),
    val inputRequest: InputRequest? = null,       // mirrors session.currentInputRequest
    val draftText: String = "",
    val selectedArtifactIds: List<String> = emptyList(),
    val isLoadingSession: Boolean = true,
    val isTurnInFlight: Boolean = false,          // session running or accepted local turn
    val isStreaming: Boolean = false,             // true while SSE is delivering deltas
    val streamingBubbleText: String = "",         // accumulates assistant.delta text
    val phaseTransitioned: Boolean = false,       // true when phase.transitioned received
    val latestReportId: String? = null,
    val error: String? = null,
)

data class ChatMessage(
    val id: String,
    val role: Role,                               // User | Assistant
    val text: String,
    val artifactIds: List<String> = emptyList(),
    val isStreaming: Boolean = false,
    val createdAt: Instant,
)

enum class Role { User, Assistant }
```

#### 5.6.2 Entry Sequence

On screen entry:

1. `GET /v1/sessions/{sessionId}` to load initial `BikeSession` state.
2. Verify the destination's expected workflow and phase.
   Initialize `isTurnInFlight` from `session.status == running`; it is not only
   a memory of turns submitted in this process.
3. Open SSE connection to `/v1/sessions/{sessionId}/events` with
   `after = 0` to replay all retained events for the session and reconstruct
   chat history.
4. Render existing `messages` from replayed events.
5. If the session accepts user input, render the `currentInputRequest`
   affordance immediately without waiting for the SSE stream to deliver it.
   The transcript may continue replaying in the background while the current
   input affordance is already visible.
6. If replayed events later reveal a newer persisted `input.requested`, the
   replayed event stream wins and the visible input affordance must reconcile
   to that newer request.
7. If replayed events show `phase.transitioned`, the app must immediately
   reconcile to the transitioned state: freeze chat input, append the system
   annotation, and show the completion banner even if the initial
   `GET /v1/sessions/{sessionId}` response appeared resumable.

For MVP, replayed or resumed artifact references do not require thumbnail
rendering. The app may show thumbnails only for artifacts selected and
uploaded within the current client session. When reconstructing chat history
from replayed events, prior `artifactIds` may be displayed as non-thumbnail
attachments or ignored visually.

#### 5.6.3 Message List

The message list scrolls chronologically, oldest at top, newest at bottom.
Auto-scroll to bottom when a new message is appended or a streaming delta
arrives, unless the user has manually scrolled up — in that case do not
force-scroll.

Message bubble types:

- **User bubble** (right-aligned): shows `text` and thumbnail rows for any
  `artifactIds` created in the current client session. Replayed historical
  artifact references do not require thumbnails for MVP.
- **Assistant bubble** (left-aligned): shows `text`. While `isStreaming` is
  true, show a blinking cursor or pulsing indicator appended to the text.
- **System annotation** (centered, subdued): used for non-message events
  such as "Diagnostic complete — reviewing findings…" shown when
  `phase.transitioned` is received.

#### 5.6.4 Input Area

The input area at the bottom of the screen adapts to `inputRequest.type`:

| `InputRequest.type` | Input area renders |
|---|---|
| `text` or `null` | Text field + send button |
| `photo` | Camera/gallery picker + optional caption field + send |
| `multiple_choice` | Tappable choice chips above the text field; tapping a chip submits immediately |
| `decision` | Rendered as choice chips; used for yes/no confirmations |
| `confirmation` | Single "Confirm" button |
| `none` | Input area hidden; no user input expected |

The input area is disabled (greyed out, non-interactive) while
`isTurnInFlight` or `isStreaming` is true.

When `inputRequest` is null and the workflow accepts freeform input in the
current state, fall back to a plain text field. This includes inspection
`awaiting_user` or `blocked_safety` after a recoverable turn error; the backend
still rejects input prohibited by active safety policy.

**Send button behaviour:**
- Enabled when: `draftText` is non-blank OR `selectedArtifactIds` is
  non-empty, AND `!isTurnInFlight`, AND `!isStreaming`.
- Tap: see §5.6.5.

#### 5.6.5 Turn Submission Flow

1. Generate a `clientTurnId` (UUID v4).
2. Optimistically append a `ChatMessage(role = User, text = draftText,
   artifactIds = selectedArtifactIds)` to `messages`.
3. Clear `draftText` and `selectedArtifactIds`.
4. Set `isTurnInFlight = true`.
5. `POST /v1/sessions/{sessionId}/turns` with `TurnCreate`.
6. On 202 response: open (or ensure open) the SSE stream using
   `start_event_id` from `TurnAccepted` as the `after` parameter if the
   stream was not already connected.
7. On non-202: set `error`, set `isTurnInFlight = false`, keep the optimistic
   message visible with an error indicator and a "Retry" tap target.

#### 5.6.6 SSE Stream Lifecycle

The SSE connection is managed by `ConversationController`, not by a composable
or duplicated workflow ViewModel. It is scoped to the owning ViewModel and is
kept alive until the workflow conversation ends or the user navigates away.

**Implementation:** Use OkHttp `EventSource` with a custom `EventSourceListener`.
Wrap it in a `callbackFlow` and collect in the ViewModel's `viewModelScope`.

**Reconnect strategy:**
- On disconnect, wait with exponential backoff (start 1 s, max 30 s, jitter).
- Reconnect with `Last-Event-ID` header and `after` query param set to the
  last received event ID.
- If no event has been received yet in the current screen instance, use
  `after = 0` to rebuild the transcript again.
- A `phase.transitioned` event freezes input but does not by itself close the
  stream because the durable `turn.completed` snapshot may follow it. Stop
  reconnecting after that terminal turn event, or after an authoritative
  reload confirms the workflow conversation is no longer active.

**Event handling:**

```
assistant.delta   →  append delta to streamingBubbleText; set isStreaming = true
assistant.message.completed
                  →  append finalised ChatMessage(role = Assistant) to messages;
                     clear streamingBubbleText; set isStreaming = false
input.requested   →  set inputRequest; update session.currentInputRequest
phase.report.created
                  →  capture report_id as latestReportId
safety.escalated  →  update prominent workflow safety presentation
turn.completed    →  set isTurnInFlight = false; replace local session from
                     payload.session; sync inputRequest, safety, and progress
error             →  set isTurnInFlight = false; set isStreaming = false;
                     set error from payload.message
phase.transitioned →  set phaseTransitioned = true; append system annotation
                      message; let workflow policy close/freeze/route
heartbeat         →  ignore except for cursor bookkeeping
```

Unknown event types are silently ignored.

#### 5.6.7 Photo Upload in Chat

When `inputRequest.type == "photo"`:

1. User selects one or more images from camera or gallery.
2. If a selected image uses a backend-unsupported format such as `HEIC` or
   `HEIF`, transcode it on-device to `JPEG` before upload.
3. For each image: `POST /v1/artifacts` with `session_id = sessionId` and the
   purpose selected from loaded workflow (`diagnostic_photo` for repair,
   `inspection_photo` for inspection). Show per-image upload progress.
4. On each 201 response: add the returned `artifact.id` to
   `selectedArtifactIds` and show a thumbnail.
5. After all uploads complete, the send button becomes active.
6. Turn submission includes the collected `artifactIds` in the same order the
   user selected the images.

Upload errors show inline beneath the affected thumbnail with a retry option.
A failed upload does not block submission if at least one artifact uploaded
successfully and `inputRequest.min_artifacts` is satisfied.

Supported backend upload formats for this MVP flow are `JPEG`, `PNG`, and
`WebP`. The Android client is responsible for converting unsupported formats
before upload.

#### 5.6.8 Phase Transition Banner

When `phase.transitioned` is received:

1. Append a system annotation to the message list: "Diagnosis complete —
   your results are ready."
2. Set `phaseTransitioned = true`.
3. The input area disappears (replaced by the banner).
4. Continue consuming through `turn.completed`, then close the stream.
5. Show a prominent banner or bottom sheet with:
   - Primary action: "Back to Home"

Read-only diagnostic report rendering is not part of the Android MVP. The
frontend stops at the completion banner for this release. A richer report or
plan-review flow remains out of scope.

The chat message history remains visible and scrollable behind/above the
banner.

#### 5.6.9 Top Bar and Navigation

- Top bar: bike name (from `session.bikeId` resolved to name) as title;
  phase label "Diagnosing" as subtitle; back arrow.
- Back arrow: if `isTurnInFlight` or `isStreaming`, show a confirmation
  dialog — "Leave this conversation while Bike Doc is still responding?" On
  confirm, navigate back.
- If the session is idle and still resumable, back navigates directly without
  a warning because the user can return later through the bike-scoped session
  chooser.
- If the session has already transitioned (`phaseTransitioned == true`), back
  navigates directly without a dialog.

---

### 5.7 Inspection Chat Screen

**Route:** typed `InspectionChatRoute(sessionId)`

**ViewModel:** `InspectionChatViewModel`

The inspection screen composes the shared conversation module and adds
inspection progress, safety presentation, completion actions, and inspection
report navigation. It must not copy the diagnostic reducer, SSE loop, turn
submission, input renderer, or photo pipeline.

#### 5.7.1 Entry and Start

After loading, require `workflow == inspection` and either
`phase == inspection` or the completed inspection state. Route by durable
state:

- `inspection + created`: show the stationary-inspection explanation and a
  primary "Begin inspection" action.
- resumable `inspection` state: replay events and resume the conversation.
- `completed + completed`: open the latest inspection report.
- `inspection + failed` or `inspection + cancelled`: show a terminal state
  with an action to return to the bike and start a new inspection.

"Begin inspection" submits an ordinary `ai_turn.v1` turn with a fresh retained
`client_turn_id`, no artifacts, no input-request response ID, and a non-empty
intent such as "Begin inspection." The wording is not a protocol selector;
the persisted workflow selects inspection behavior. Disable the action after
submission and use the ordinary optimistic/retry path.

Before the first turn, explain that the inspection is guided, visual, and
stationary; is not a professional safety certification; may be skipped when a
check is uncomfortable or unsafe; and never requires riding the bike.

#### 5.7.2 Progress and Input

The top of the chat shows `completed_checks / total_applicable_checks` and the
current section. Progress is replaced only from the loaded session or a
`turn.completed` session snapshot. The denominator may decrease when
conditional applicability resolves, so the client must not animate or label
that as lost progress.

For `general_inspection.v1`, an `InspectionSectionCatalog` maps stable section
IDs to localized display resources. An unknown checklist version or section
must render the generic localized label "Inspection progress," never a raw
identifier or guessed checklist wording. The conversation prompt remains the
source for the detailed requested action.

Inspection supports `text`, `photo`, `multiple_choice`, `confirmation`, and
`none`. It does not use the repair-only `decision` input type. A photo request
renders camera/gallery controls and, when supplied, the
`continue_without_photo` choice beside them. Choice and confirmation turns
must include `responds_to_input_request_id` and submit the stable choice value,
not its localized label.

If status is `running`, disable input, attachments, Finish, and Cancel even
after process recreation. If status is `blocked_safety`, show the latest
safety message above the input and allow only the current server-requested or
freeform safety-permitted response. The client never decides that a prohibited
check has become safe.

#### 5.7.3 Safety and Leaving

Render `safety.escalated` prominently and reconcile it with the next
authoritative session snapshot. Blocking copy must tell the user not to ride
or perform the unsafe check without claiming a root cause. The inspection may
continue with server-requested safe visual or stationary checks.

Use the same leave-while-streaming confirmation as diagnostic chat. Leaving an
idle inspection needs no confirmation because it is discoverable and
resumable. Leaving does not cancel or finish the inspection.

#### 5.7.4 Finish Early and Cancel

The overflow menu exposes two distinct actions whenever phase is `inspection`
and status is `created`, `awaiting_user`, or `blocked_safety`:

- **Finish inspection now** — confirmation explains that remaining checks are
  recorded as skipped and a partial report is created.
- **Cancel inspection** — confirmation explains that the inspection ends
  permanently and no report is created.

Never infer either action from conversational text such as "stop." While
status is `running`, show both actions disabled with an explanation that the
current response must finish.

Finish sends `inspection_completion.v1`, `reason = finish_early`, and one
`client_completion_id` retained for all retries of that confirmation. On
success, use the returned report and completed session to replace local state
and open `InspectionReportScreen` without waiting for SSE.

Cancel sends one retained `client_cancellation_id`. On success, replace local
state with the cancelled session and show the terminal presentation. A
`session_busy` or `session_state_conflict` response triggers a session reload;
the client does not silently retry with a new idempotency key.

#### 5.7.5 Normal Completion

On `phase.report.created`, retain the inspection report ID. On
`phase.transitioned`, make chat read-only but continue consuming the stream.
On the following `turn.completed`, replace the session and navigate to the
retained report when the snapshot is `completed + completed`. If the report
event was missed but `latest_reports.inspection_report_id` is present, use that
ID. If neither source contains an ID, apply the completed-session contract
error behavior from §4.8.

---

### 5.8 Inspection Report Screen

**Route:** typed `InspectionReportRoute(sessionId, reportId)`

**ViewModel:** `InspectionReportViewModel`

Load the report through the generic report endpoint and require envelope
`type == inspection`, `phase == inspection`, and
`schema_version == inspection_report.v1`. Unsupported or malformed reports
produce a controlled error rather than partial decoding.

Render:

- deterministic summary and outcome
- ride guidance with non-certifying copy
- coverage for every area, including partial, skipped, unable-to-assess, and
  not-applicable areas
- findings grouped by urgency, then bike area
- limitations
- safety flags
- shop-assessment guidance where recommended
- a "Start diagnosis" action only on findings whose
  `recommended_next_action == start_diagnostic`

`no_issue_observed`, `no_actionable_findings`, and
`no_known_blocking_issue` must never be rendered as "safe," "certified," or
"passed." Use wording such as "No blocking issue was identified in the checks
that were completed" and keep material coverage limitations adjacent.

#### 5.8.1 Finding Handoff

Tapping "Start diagnosis" creates a repair session with a retained
`client_session_id` and exactly this origin:

```text
type: inspection_finding
inspection_session_id: <source session>
finding_id: <selected finding>
```

Disable only that finding's action while creation is in flight. On success,
navigate to `DiagnosticChatScreen` for the returned repair session. When that
created session with an inspection-finding origin first opens, diagnostic chat
automatically submits one ordinary initial turn if no turn has been accepted;
the turn uses a `SavedStateHandle`-retained `client_turn_id` and a non-empty
intent such as "Begin diagnosis from the inspection finding." If the session
is already `running` or later, it must not submit another start turn.

If creation returns `finding_already_handed_off`, validate the bounded
`details.session_id` and open that existing repair session. The client never
copies the inspection transcript, image bytes, or finding text into the turn;
the server-provided structured origin and approved evidence are authoritative.

---

## 6. State Management Patterns

### 6.1 ViewModel Conventions

- All ViewModels expose a single `uiState: StateFlow<XxxUiState>`.
- Side effects (navigation, one-shot Snackbars) are emitted on a separate
  `SharedFlow<UiEvent>`.
- ViewModels do not hold references to `Context`, `NavController`, or other
  Android framework types (except `Application` via `AndroidViewModel` only
  when truly needed).

```kotlin
sealed class UiEvent {
    data class ShowSnackbar(val message: String) : UiEvent()
    data class NavigateTo(val route: String) : UiEvent()
    object NavigateBack : UiEvent()
}
```

### 6.2 API Call Pattern

```kotlin
private fun loadBikes() {
    viewModelScope.launch {
        _uiState.update { it.copy(isLoading = true, error = null) }
        when (val result = safeApiCall { apiService.getBikes() }) {
            is ApiResult.Success -> _uiState.update {
                it.copy(isLoading = false, bikes = result.data.items)
            }
            is ApiResult.Error -> _uiState.update {
                it.copy(isLoading = false, error = result.message)
            }
            else -> {}
        }
    }
}
```

### 6.3 Token Retrieval in ViewModels

ViewModels must not call Firebase directly. Inject an `AuthProvider`
interface:

```kotlin
interface AuthProvider {
    suspend fun getToken(): String
    fun currentUserId(): String?
    fun signOut()
}
```

`FirebaseAuthProvider` implements this. The `Authorization` header is
attached by an OkHttp `Interceptor` injected into the shared `OkHttpClient`,
so ViewModels and service interfaces never handle the token directly.
`FirebaseAuthProvider` is injected via Hilt to keep ViewModels testable
without a Firebase dependency.

### 6.4 SSE Flow Pattern

```kotlin
// In SseEventSource.kt
fun connect(sessionId: String, after: String?): Flow<SseEvent> = callbackFlow {
    val request = buildRequest(sessionId, after)
    val source = okHttpClient.newEventSource(request, object : EventSourceListener() {
        override fun onEvent(source: EventSource, id: String?, type: String?, data: String) {
            trySend(SseEvent.parse(type, id, data))
        }
        override fun onClosed(source: EventSource) { channel.close() }
        override fun onFailure(source: EventSource, t: Throwable?, response: Response?) {
            close(t ?: IOException("SSE failure"))
        }
    })
    awaitClose { source.cancel() }
}

// Inside ConversationController, owned by the workflow ViewModel
private fun observeStream() {
    streamJob = viewModelScope.launch {
        sseEventSource.connect(sessionId, lastEventId)
            .retryWithExponentialBackoff()
            .collect { event -> handleSseEvent(event) }
    }
}
```

---

## 7. Hilt Dependency Graph

```
@Singleton
├── FirebaseAuthProvider      (implements AuthProvider)
├── OkHttpClient              (configured with auth interceptor; shared by Retrofit and SseEventSource)
├── BikeDocApiClient          (Retrofit instance; injects OkHttpClient)
├── BikeDocApiService         (created from BikeDocApiClient.retrofit.create())
├── SseEventSource            (injects OkHttpClient)
├── BikeRepository            (injects BikeDocApiService)
├── SessionRepository         (generic BikeSession reads/writes)
├── ArtifactRepository        (workflow-neutral upload transport)
└── ReportRepository          (strict report-envelope decoding)

@HiltViewModel
├── AuthViewModel             (injects FirebaseAuthProvider)
├── HomeViewModel             (injects HomeRepository, AuthProvider)
├── BikeListViewModel         (injects BikeRepository, AuthProvider)
├── BikeEditViewModel         (injects BikeRepository, AuthProvider)
├── DiagnosticChatViewModel   (composes ConversationController)
├── InspectionChatViewModel   (composes ConversationController plus inspection actions)
└── InspectionReportViewModel (injects ReportRepository, SessionRepository)
```

`ConversationController` is created per destination/ViewModel, not as an
application singleton. Its dependencies are repository and event-source
interfaces, so diagnostic and inspection tests exercise the same conversation
seam with fakes.

---

## 8. Contract Synchronization and Verification

The Android implementation begins only after `docs/specs/openapi.yaml` records
the generic session routes and schemas used here. The contract change and
Android migration are one coordinated cutover: no dual route support, repair
aliases, or legacy `rs_` assumptions are required.

Deterministic Android tests must cover:

- generic session and event decoding for both workflows
- explicit repair/inspection discovery filters and workflow route validation
- unchanged repair diagnostic replay, reconnect, input, photo, and retry
  behavior through `/v1/sessions`
- inspection resumability for `created`, `running`, `awaiting_user`, and
  `blocked_safety`
- authoritative progress replacement, including a decreasing conditional
  denominator and an unknown section fallback
- optional-photo `continue_without_photo` submission
- safety escalation and disabled input/actions while running
- finish-early and cancellation confirmations, retained idempotency IDs,
  `session_busy`, and terminal navigation
- strict `inspection_report.v1` decoding and non-certifying copy
- finding handoff, exact retry, and `finding_already_handed_off` navigation
- `active_inspection_exists` navigation
- inspection-only history blocking bike deletion
- unknown SSE types advancing the cursor without changing UI state

The shared conversation module is tested through its interface. Diagnostic and
inspection ViewModel tests focus on workflow policy rather than repeating the
SSE state-machine suite. Compose UI tests cover the optional-photo choice,
Finish/Cancel distinction, safety presentation, and finding action gating.

---

## 9. Out of Scope for This Spec

- Plan review and decision gate
- Broad cross-bike session history
- Guided repair execution
- Repair completion / summary screen
- Push notifications
- Bike profile photo upload
- Email verification after sign-up
- Any admin or settings screens
- Offline-authoritative inspection state
- Grouping multiple inspection findings into one repair handoff
- Cancelling an inspection while a turn is running
