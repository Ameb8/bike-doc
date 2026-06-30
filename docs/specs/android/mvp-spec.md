# Bike Doc Android — MVP Frontend Spec

**Scope:** Auth, home, bike management, and diagnostic chat  
**Status:** Draft v0.3  
**Last updated:** 2026-06-28

---

## 1. Overview

This spec covers the MVP Android screens through the end of the diagnostic
phase: authentication, home, bike management, and the diagnostic chat flow.
It does not cover plan review, repair execution, or any post-diagnostic
phase. It also does not assume backend endpoints that are specified
canonically but not implemented yet in `apps/api`.

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
│   ├── chat/
│   │   ├── DiagnosticChatScreen.kt
│   │   ├── DiagnosticChatViewModel.kt
│   │   └── SseEventSource.kt            ← SSE client wrapper
│   └── models/
│       ├── ChatMessage.kt               ← local UI message model
│       └── SseEvent.kt                  ← parsed SSE event types
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
                        ├── BikeListScreen (selection mode)  ← "Start New Repair"
                        │       └── DiagnosticChatScreen
                        └── DiagnosticChatScreen            ← new or resumed session
```

`HomeScreen` is the top-level destination after auth. It is never popped off
the back stack during a session. The back button on Home has no effect or
exits the app.

All destinations live in a single `NavHost` in `MainActivity`. There is no
bottom navigation bar for V1.

`DiagnosticChatScreen` is reachable from new session creation and from
resuming a previously discovered session for the selected bike. The route
accepts `sessionId` as a path parameter.

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
| `GET` | `/v1/repair-sessions?bike_id={bikeId}` | Bike selection / resume flow |
| `POST` | `/v1/repair-sessions` | Create new diagnostic session |
| `GET` | `/v1/repair-sessions/{sessionId}` | Chat screen (load session state) |
| `POST` | `/v1/repair-sessions/{sessionId}/turns` | Submit a user turn |
| `GET` | `/v1/repair-sessions/{sessionId}/events` | SSE stream (OkHttp direct, not Retrofit) |

> **Note:** This spec depends on the canonical `GET /v1/repair-sessions`
> endpoint with `bike_id` filtering. The Android MVP expects that route to
> return only sessions owned by the authenticated user, newest first for the
> requested bike.

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

    @POST("v1/repair-sessions")
    suspend fun createRepairSession(@Body body: RepairSessionCreate): RepairSession

    @GET("v1/repair-sessions/{sessionId}")
    suspend fun getRepairSession(@Path("sessionId") sessionId: String): RepairSession

    @POST("v1/repair-sessions/{sessionId}/turns")
    suspend fun createTurn(
        @Path("sessionId") sessionId: String,
        @Body body: TurnCreate,
    ): TurnAccepted

    @GET("v1/repair-sessions/{sessionId}/reports")
    suspend fun getReports(
        @Path("sessionId") sessionId: String,
        @Query("limit") limit: Int = 50,
        @Query("cursor") cursor: String? = null,
    ): PhaseReportList

    @GET("v1/repair-sessions/{sessionId}/reports/{reportId}")
    suspend fun getReport(
        @Path("sessionId") sessionId: String,
        @Path("reportId") reportId: String,
    ): PhaseReportEnvelope
}
```

The `Authorization` header is attached by an OkHttp interceptor injected into
the shared `OkHttpClient`, so individual service methods do not take a token
parameter.

The SSE endpoint (`/v1/repair-sessions/{sessionId}/events`) is consumed
directly via OkHttp `EventSource` in `SseEventSource.kt` — Retrofit is not
used for that route.

### 4.2.1 Diagnostic Photo Upload Format Policy

For the MVP diagnostic chat flow, backend diagnostic-photo uploads accept:

- `image/jpeg`
- `image/png`
- `image/webp`

The Android client must transcode unsupported selected-image formats before
upload. In particular, `HEIC` and `HEIF` images must be converted on-device
to a backend-accepted format, preferably `JPEG`, before calling
`POST /v1/artifacts`.

This is an explicit MVP contract decision. The backend does not need native
`HEIC` / `HEIF` upload support for this version.

### 4.3 Bike Model (Expected Shape)

```kotlin
@Serializable
data class Bike(
    val id: String,
    val userId: String,
    val displayName: String,
    val hasRepairSessions: Boolean,
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

`hasRepairSessions` indicates whether the authenticated user owns any repair
sessions for this bike. The Android app uses it to decide whether delete
actions should be shown or blocked without needing a second round trip.
This field is part of the canonical `Bike` object for both `GET /v1/bikes`
and `GET /v1/bikes/{bikeId}`.

```kotlin
@Serializable
data class BikeListResponse(
    val items: List<Bike>,
    val nextCursor: String?,
)
```

### 4.4 Repair Session Discovery

The Android MVP depends on `GET /v1/repair-sessions?bike_id={bikeId}` for
bike-scoped session discovery.

- The endpoint must return only sessions owned by the authenticated user.
- Results must be sorted newest first.
- The backend default page size should be 20 when `limit` is omitted.
- The Android client calls this endpoint without a status filter and decides
  resumability client-side.
- The response must include enough `RepairSession` state for the client to
  decide which sessions are resumable.
- The Android chooser reads only the first page for MVP.
- A broad cross-bike repair-history screen remains out of scope for this MVP.

### 4.5 SSE Event Schema

The events stream at `/v1/repair-sessions/{sessionId}/events` is the primary
real-time channel during a diagnostic turn. Events are `text/event-stream`
lines with `id:`, `event:`, and `data:` fields. The `data:` line is the full
JSON-encoded `RepairSessionEvent` envelope, not just the event-specific inner
payload.

The following event types are consumed by the chat screen:

| Event type | Payload | UI effect |
|---|---|---|
| `turn.started` | `{ "turn_id": "...", "phase": "diagnostic" }` | Optional UI hook; keep input disabled |
| `assistant.delta` | `{ "text": "..." }` | Append to streaming assistant bubble |
| `assistant.message.completed` | `{ "message_id": "...", "full_text": "...", "artifact_ids": [...] }` | Finalise and persist the assistant bubble |
| `input.requested` | `{ "input_request": InputRequest }` | Render the appropriate input affordance |
| `artifact.referenced` | `{ "artifact": ArtifactRef }` | Optional UI hook for artifact provenance |
| `phase.report.created` | `{ "report_id": "...", ... }` | Capture latest diagnostic report ID if needed |
| `phase.transitioned` | `{ "from_phase": "...", "to_phase": "...", "status": "..." }` | Freeze chat and show completion banner |
| `safety.escalated` | safety payload | Show safety state prominently |
| `turn.completed` | `{ "turn_id": "...", "session": RepairSession }` | Mark turn done, update session state, re-enable input as appropriate |
| `error` | `{ "code": "...", "message": "...", "retryable": false }` | Show recoverable error and clear loading state |
| `heartbeat` | `{ "ok": true }` | Ignore for UI other than updating last seen cursor |

Unknown event types must be silently ignored; never crash on an unrecognised
event.

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
    data class Error(val code: Int?, val message: String) : ApiResult<Nothing>()
    object Loading : ApiResult<Nothing>()
}

suspend fun <T> safeApiCall(call: suspend () -> T): ApiResult<T> = try {
    ApiResult.Success(call())
} catch (e: HttpException) {
    ApiResult.Error(e.code(), mapHttpError(e))
} catch (e: IOException) {
    ApiResult.Error(null, "Network error. Check your connection.")
}
```

Surface errors to the user as a Snackbar with a retry action. Map common
codes to plain messages:

| HTTP Code | User message |
|---|---|
| 401 | "Session expired. Please sign in again." → sign out |
| 403 | "You don't have permission to do that." |
| 404 | "Not found." |
| 422 | Display `error.message` from response body |
| 5xx | "Something went wrong. Try again." |

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
    val isLoading: Boolean = false,
    val error: String? = null,
)
```

**UI:**
- Top bar: "Bike Doc" title, overflow menu with "Sign Out"
- Greeting: "Hi, [displayName]" or empty while loading
- Card: "My Bikes" → navigates to `BikeListScreen` (browse mode)
- FAB or prominent button: "Start New Repair" → navigates to `BikeListScreen`
  in selection mode

**Behavior:**
- On entry: `GET /v1/me`. On 401, force sign-out.
- "Start New Repair" navigates to bike selection. Session creation happens
  after a bike is chosen (see §5.3).
- Session resume remains bike-centric in this MVP. Home does not expose a
  cross-bike recent-sessions or repair-history entry point.

---

### 5.3 Bike List Screen

**Route:** `bikes?selectionMode={Boolean}` (default `false`)  
**ViewModel:** `BikeListViewModel`

**State:**
```kotlin
data class BikeListUiState(
    val bikes: List<Bike> = emptyList(),
    val isLoading: Boolean = false,
    val error: String? = null,
    val selectionMode: Boolean = false,
    val selectedBikeId: String? = null,
    val bikeSessions: List<RepairSession> = emptyList(),
    val isLoadingBikeSessions: Boolean = false,
    val deletingBikeId: String? = null,
    val isCreatingSession: Boolean = false,     // spinner while POST /repair-sessions is in flight
)
```

**UI:**
- Top bar: "My Bikes"; subtitle "Select a bike to diagnose" when
  `selectionMode`
- List of `BikeCard` composables showing: bike name, make/model/year, brief
  drivetrain/brake summary
- FAB: "Add Bike" → `BikeEditScreen` (hidden in selection mode)
- Empty state: "No bikes yet. Add your first bike." with "Add Bike" button

**BikeCard interactions:**
- Browse mode: tap → `BikeEditScreen`; long-press or swipe-to-reveal → delete
- Selection mode: tap → fetch sessions for that bike via
  `GET /v1/repair-sessions?bike_id={bikeId}` and show a bike-specific session
  chooser sheet; no edit/delete actions shown

Session discovery and resume entry are available only through selection mode
(`Start New Repair`) in this MVP. Browse mode remains focused on bike
management and does not expose repair-session chooser affordances.

**Session chooser sheet (selection mode):**
1. User taps a bike card.
2. Set `selectedBikeId` and `isLoadingBikeSessions = true`.
3. `GET /v1/repair-sessions?bike_id={bikeId}`.
4. On success: show a bottom sheet for that bike containing:
   - Primary action: "Resume Latest Active Session" when an active
     resumable session exists
   - The primary action also shows identifying metadata for the target
     session using the same timestamp and status model as a session row
   - Secondary action: "Start New Diagnostic Session", always available even
     when resumable sessions exist
   - List of older sessions below, newest first, excluding the session
     already represented by the primary resume CTA
5. If the request succeeds with an empty `items` list, skip the chooser and
   immediately start a new diagnostic session for that bike.
6. On error: clear `isLoadingBikeSessions`, dismiss any partial sheet state,
   show Snackbar. Do not offer session creation until session discovery
   succeeds.

Each session row in the chooser shows:
- Primary text: formatted `created_at` timestamp
- Status label: derived from session phase and status
- Secondary text: compact phase/activity description, for example
  "Diagnostic session"
- Tappable older resumable rows also show a `Resumable` badge

The older-session list remains one flat newest-first list for MVP. Sessions
are not visually grouped by phase or status.

Status labels are mapped as follows:
- `diagnostic + created` or `diagnostic + running` → `In progress`
- `diagnostic + awaiting_user` → `Awaiting your reply`
- `diagnostic + awaiting_decision` → `Awaiting your decision`
- `diagnostic + completed` → `Completed`
- non-diagnostic phases → `Moved beyond diagnosis`
- cancelled or stopped states → `Closed`

For MVP, a session is considered **active** and **resumable** when:
- `phase == diagnostic`
- `status` is one of `created`, `running`, `awaiting_user`, or
  `awaiting_decision`

When multiple resumable sessions exist for the same bike, the primary
"Resume Latest Active Session" action must target the newest resumable session
using the same newest-first ordering returned by
`GET /v1/repair-sessions?bike_id={bikeId}`.

Sessions outside the diagnostic phase, or diagnostic sessions with terminal
statuses, must not drive the primary resume CTA. They may appear only in the
older-session list.

Rows in the older-session list are tappable only when that session is
resumable. When multiple resumable sessions exist, the newest one is promoted
to the primary resume CTA and older resumable sessions remain listed as
tappable rows below it. Non-resumable sessions are shown as read-only rows
with status labels and no navigation action. In particular, sessions outside
the diagnostic phase must never navigate into `DiagnosticChatScreen` in this
MVP.

**Start new session from chooser:**
1. If a resumable session already exists for that bike, show a confirmation
   dialog before creating a new session.
2. Confirmation copy: "Start a new diagnostic session? You can still return
   to your earlier session later."
3. On confirm, set `isCreatingSession = true`.
4. `POST /v1/repair-sessions` with the selected `bike_id`.
5. Creating a new session does not close, cancel, or otherwise mutate older
   sessions for that bike. Older resumable sessions remain resumable.
6. On success: navigate to `DiagnosticChatScreen(sessionId = response.id)`,
   clearing the bike selection screen from the back stack so back returns
   to Home.
7. On error: clear `isCreatingSession`, keep the chooser visible, show
   Snackbar.

**Resume session from chooser:**
- Tapping the primary action or a resumable session row navigates to
  `DiagnosticChatScreen(sessionId)`.
- The bike selection screen is cleared from the back stack so back returns to
  Home.
- Resuming a session does not require an additional confirmation dialog.

**Delete flow (browse mode):**
- Bikes with any owned repair session history, including resumable sessions,
  are not deletable in this MVP.
- When deletion is blocked, show a plain explanation such as
  "This bike can't be removed because it has repair session history."
- Only bikes with no owned repair sessions may show the destructive delete
  action.
- `hasRepairSessions` is a UI hint, not a lock. If `DELETE /v1/bikes/{bikeId}`
  still fails because session history now exists, treat that as a normal
  conflict, show the same explanation, and refresh the affected bike row or
  whole list so the delete affordance disappears.
- Confirmation dialog for deletable bikes: "Remove [bike name]?"
- On confirm: `DELETE /v1/bikes/{bikeId}`. Track with `deletingBikeId`.
- On success: remove card from list without a full refresh.
- On error: show Snackbar.

**Data loading:** `GET /v1/bikes` on entry. Ignore `nextCursor` for V1 and
render the first page only.

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
    val displayName: String = "",
    val make: String = "",
    val model: String = "",
    val modelYear: String = "",
    val bikeType: String = "unknown",
    val frameMaterial: String = "unknown",
    val drivetrain: String = "",
    val brakeType: String = "unknown",
    val wheelSize: String = "",
    val tireSize: String = "",
    val notes: String = "",
    val isLoading: Boolean = false,
    val isSaving: Boolean = false,
    val error: String? = null,
    val validationErrors: Map<String, String> = emptyMap(),
)
```

**UI:**
- Top bar: "Add Bike" or "Edit Bike", back arrow, "Save" action
- Fields (all optional except `displayName`):
  - Display name (required) — free text
  - Make — free text
  - Model — free text
  - Model year — numeric keyboard, 4-digit
  - Bike type — dropdown backed by backend enum values
  - Frame material — dropdown backed by backend enum values
  - Drivetrain — free text
  - Brake type — dropdown backed by backend enum values
  - Wheel size — free text
  - Tire size — free text
  - Notes — multiline free text
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

### 5.5 Deferred: Session History / Previous Sessions

The app may discover prior sessions for a selected bike via
`GET /v1/repair-sessions?bike_id={bikeId}` and resume a selected session by
navigating to `DiagnosticChatScreen(sessionId)`. On resume, the chat must
rebuild the full retained transcript from
`GET /v1/repair-sessions/{sessionId}/events?after=0`.

A broad "Repair History" screen spanning all bikes remains out of scope for
this MVP.

---

### 5.6 Diagnostic Chat Screen

**Route:** `sessions/{sessionId}/chat`  
**ViewModel:** `DiagnosticChatViewModel`

This is the primary interactive screen for the diagnostic phase. The user
exchanges turns with the agent until the diagnostic phase is complete, at
which point the backend transitions the session and the chat is frozen.

#### 5.6.1 State

```kotlin
data class DiagnosticChatUiState(
    val session: RepairSession? = null,
    val messages: List<ChatMessage> = emptyList(),
    val inputRequest: InputRequest? = null,       // mirrors session.currentInputRequest
    val draftText: String = "",
    val selectedArtifactIds: List<String> = emptyList(),
    val isLoadingSession: Boolean = true,
    val isTurnInFlight: Boolean = false,          // true from POST turns → turn.completed
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

1. `GET /v1/repair-sessions/{sessionId}` to load initial `RepairSession` state.
2. Open SSE connection to `/v1/repair-sessions/{sessionId}/events` with
   `after = 0` to replay all retained events for the session and reconstruct
   chat history.
3. Render existing `messages` from replayed events.
4. If `session.status == awaiting_user`, render the `currentInputRequest`
   affordance immediately without waiting for the SSE stream to deliver it.
   The transcript may continue replaying in the background while the current
   input affordance is already visible.
5. If replayed events later reveal a newer persisted `input.requested`, the
   replayed event stream wins and the visible input affordance must reconcile
   to that newer request.
6. If replayed events show `phase.transitioned`, the app must immediately
   reconcile to the transitioned state: freeze chat input, append the system
   annotation, and show the completion banner even if the initial
   `GET /v1/repair-sessions/{sessionId}` response appeared resumable.

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

When `inputRequest` is null and `session.status == awaiting_user`, fall back
to a plain text field — the agent is waiting for freeform input.

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
5. `POST /v1/repair-sessions/{sessionId}/turns` with `TurnCreate`.
6. On 202 response: open (or ensure open) the SSE stream using
   `start_event_id` from `TurnAccepted` as the `after` parameter if the
   stream was not already connected.
7. On non-202: set `error`, set `isTurnInFlight = false`, keep the optimistic
   message visible with an error indicator and a "Retry" tap target.

#### 5.6.6 SSE Stream Lifecycle

The SSE connection is managed by `DiagnosticChatViewModel`, not by the
composable. It is opened on screen entry and kept alive until the phase
transitions or the user navigates away.

**Implementation:** Use OkHttp `EventSource` with a custom `EventSourceListener`.
Wrap it in a `callbackFlow` and collect in the ViewModel's `viewModelScope`.

**Reconnect strategy:**
- On disconnect, wait with exponential backoff (start 1 s, max 30 s, jitter).
- Reconnect with `Last-Event-ID` header and `after` query param set to the
  last received event ID.
- If no event has been received yet in the current screen instance, use
  `after = 0` to rebuild the transcript again.
- Stop reconnecting after the phase has transitioned
  (`phaseTransitioned == true`).

**Event handling:**

```
assistant.delta   →  append delta to streamingBubbleText; set isStreaming = true
assistant.message.completed
                  →  append finalised ChatMessage(role = Assistant) to messages;
                     clear streamingBubbleText; set isStreaming = false
input.requested   →  set inputRequest; update session.currentInputRequest
phase.report.created
                  →  capture report_id as latestReportId
turn.completed    →  set isTurnInFlight = false; replace local session from
                     payload.session; sync inputRequest from payload.session
error             →  set isTurnInFlight = false; set isStreaming = false;
                     set error from payload.message
phase.transitioned →  set phaseTransitioned = true; append system annotation
                      message; close SSE connection; freeze input area
heartbeat         →  ignore except for cursor bookkeeping
```

Unknown event types are silently ignored.

#### 5.6.7 Photo Upload in Chat

When `inputRequest.type == "photo"`:

1. User selects one or more images from camera or gallery.
2. If a selected image uses a backend-unsupported format such as `HEIC` or
   `HEIF`, transcode it on-device to `JPEG` before upload.
3. For each image: `POST /v1/artifacts` with `purpose = diagnostic_photo`,
   `repair_session_id = sessionId`. Show per-image upload progress.
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
4. Show a prominent banner or bottom sheet with:
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

// In DiagnosticChatViewModel
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
└── SessionRepository         (injects BikeDocApiService)

@HiltViewModel
├── AuthViewModel             (injects FirebaseAuthProvider)
├── HomeViewModel             (injects BikeDocApiService, AuthProvider)
├── BikeListViewModel         (injects BikeRepository, AuthProvider)
├── BikeEditViewModel         (injects BikeRepository, AuthProvider)
└── DiagnosticChatViewModel   (injects SessionRepository, SseEventSource, AuthProvider)
```

---

## 8. Open Items / Deferred Decisions

| Item | Decision needed |
|---|---|
| Session-discovery endpoint | Backend must add an owned route that returns previous repair-session IDs before a real history screen can exist |
| Sign-up flow | Self-service account creation is now in-app (§5.1). Defer email verification to a future iteration. |
| Offline behavior | Not in scope for V1 |

---

## 9. Out of Scope for This Spec

- Plan review and decision gate
- Diagnostic report screen / read-only report viewer
- Resuming an in-progress diagnostic session after leaving chat
- Guided repair execution
- Previous-session browsing UI
- Repair completion / summary screen
- Push notifications
- Bike profile photo upload
- Email verification after sign-up
- Any admin or settings screens
