# Android App Architecture

BikeDoc Android is the authenticated mobile client for the BikeDoc repair
workflow. It renders the user-facing experience—authentication, bike profiles,
repair-session selection, live diagnostic chat, photo submission, and report
viewing—while the BikeDoc API remains the durable source of product state. The
app is a single Android application module, written in Kotlin with Jetpack
Compose, Hilt, Retrofit/OkHttp, Kotlin serialization, and Firebase Auth.

Read this document before making a change that crosses Android packages or
changes a workflow boundary. For a focused screen change, start with the
feature source, its ViewModel tests, and the canonical Android MVP spec. The
authoritative product and client behavior documents are
[`docs/specs/android/mvp-spec.md`](../../docs/specs/android/mvp-spec.md) and
[`docs/specs/openapi.yaml`](../../docs/specs/openapi.yaml); implementation
details in this document should not be used to override either contract.

## Quick map

### What the app does

The current client implements the diagnostic portion of the repair journey.
At launch it selects the authentication or home destination from Firebase's
current user. An authenticated user can manage their bikes, select a bike to
resume an eligible diagnostic repair session or start a new one, exchange turns
with the diagnostic service, attach diagnostic photos, follow server-sent
events, and open a resulting report. There is deliberately no Room or
DataStore cache: API reads are the normal source of screen data and the app is
not designed for offline diagnostic work.

### Major modules

| Area | Owns | Main entry points |
| --- | --- | --- |
| Root application | Android and Compose bootstrap, application logging, app theme | `BikeDocApplication`, `MainActivity` |
| `navigation/` | Route definitions, the single Navigation Compose graph, navigation events and back-stack results | `AppRoute`, `AppNavGraph`, `UiEvent` |
| `auth/` | Firebase-facing authentication boundary, auth state and sign-in UI | `AuthProvider`, `FirebaseAuthProvider`, `AuthViewModel`, `AuthScreen` |
| `api/` | HTTP contract, auth interceptor, serialization DTOs, common errors, and API-level repositories | `BikeDocApiService`, `ApiResult`, repository interfaces |
| `home/` | Signed-in landing screen and user-profile loading | `HomeRepository`, `HomeViewModel`, `HomeScreen` |
| `bikes/` | Bike list/edit UI, presentation mapping, repair-session choice, and bike navigation results | `BikeListViewModel`, `BikeEditViewModel` |
| `sessions/chat/` | Diagnostic conversation state, turn/photo submission, SSE lifecycle, and chat UI | `DiagnosticChatViewModel`, `SseEventSource` |
| `sessions/models/` and `sessions/report/` | Local chat/event model and safe report decoding/presentation | `SseEvent`, `ChatMessage`, `ReportRepository` |
| `core/`, `di/`, `ui/` | Shared dispatcher qualifiers, Hilt bindings, and app-wide Compose theme | `IoDispatcher`, feature modules, `BikeDocTheme` |

### Dependency direction

The normal direction must remain one way:

```text
Compose screen -> ViewModel -> feature/API repository -> Retrofit service -> BikeDoc API
                      |                 |
                      |                 -> photo preparer / SSE event source
                      -> UiEvent -> navigation graph

Firebase SDK -> AuthProvider -> AuthInterceptor -> shared OkHttp client
```

`MainActivity` and the Hilt modules are composition roots. They select the app
graph and concrete infrastructure. The navigation graph is a UI composition
root: it obtains destination-scoped ViewModels, collects their one-shot
navigation events where necessary, and translates them into `NavController`
operations. Ordinary composables should not create services, make HTTP calls,
or navigate by constructing an unrelated `NavController`; ordinary ViewModels
should not depend on Retrofit, OkHttp, FirebaseAuth, or Compose types.

There are a few intentionally narrow variations. `SseEventSource` uses the
shared OkHttp client directly because Retrofit is request/response-oriented;
it still exposes a `Flow` rather than OkHttp callbacks. The photo preparer uses
Android's `ContentResolver` because it converts a selected URI into uploadable
bytes. Both are interfaces bound by Hilt and consumed by the chat ViewModel,
not by a composable.

## Application setup and navigation

`BikeDocApplication` is annotated with `@HiltAndroidApp` and plants Timber's
debug tree for debug builds. `MainActivity` is the only activity. It enables
edge-to-edge rendering and installs `BikeDocTheme { AppNavGraph() }`; feature
screens are Compose destinations rather than activities or fragments. The
manifest grants Internet access and provides a non-exported `FileProvider` for
app-owned photo sharing paths. App resources contain strings, colors, theme
resources, launcher assets, and the Google sign-in drawable; reusable Compose
styling belongs in `ui/theme/`, while feature-specific layout belongs beside its
screen.

`AppNavViewModel` determines the initial route from `AuthProvider.isSignedIn()`.
`AppRoute` is the central route catalog for Auth, Home, bikes in ordinary or
selection mode, bike creation/editing, diagnostic chat, and a diagnostic
report. Although routes currently use Navigation Compose string patterns,
construction helpers keep identifiers and optional arguments out of screens.
New routes should be added here and registered in `AppNavGraph`, with explicit
`navArgument` types for every path/query value.

The graph enforces the important authentication back-stack transitions:
successful authentication replaces Auth with Home, while a sign-out navigation
replaces Home with Auth. Bike edit returns a boolean refresh request through
the previous back-stack entry's `SavedStateHandle`, allowing the list to reload
without a global event bus. Chat and report destinations read their IDs from
their `SavedStateHandle`, which is the appropriate destination-local argument
source. `UiEvent` represents transient commands—navigation, back navigation
with a result, and snackbars—and is carried through buffered channels rather
than stored in durable screen state.

## State, feature, and presentation structure

Each feature owns its screen, ViewModel, and feature-specific presentation
types. A ViewModel exposes an immutable `StateFlow` backed by a private
`MutableStateFlow`; it changes state in `viewModelScope` and supplies UI
actions. Screens collect the state and pass values and lambdas into child
composables. This keeps rendering testable, supports Compose state hoisting,
and prevents leaf UI from gaining infrastructure dependencies. A channel/flow
is used for one-off effects that must not replay after recomposition.

`home/` is a small example: its repository retrieves `/v1/me`, its ViewModel
models loading, loaded data, and errors, and `HomeScreen` offers the entry
actions for bikes and sign-out. `bikes/` contains two closely related flows.
`BikeEditViewModel` loads, validates, creates, updates, and deletes an
individual profile through `BikeRepository`. Bike writes are encoded at that
repository seam: creates omit unspecified optional fields, while updates diff
the loaded profile and send explicit JSON null only for a user-requested clear.
`BikeListRepository` deliberately
maps API `Bike` DTOs to the list-specific `BikeListItem` display model, and
normalizes a delete conflict into `BikeDeleteResult.RepairHistoryConflict`.
The list ViewModel owns selection mode and the session chooser: it queries
sessions for the selected bike, resumes a session that is diagnostic and in a
resumable status, or creates a new repair session after the appropriate
confirmation. That product rule belongs in the ViewModel/repository layer, not
in the list composable.

`sessions/` is split by user experience rather than by transport concern.
`sessions/chat/` owns the ongoing diagnostic conversation; `sessions/models/`
contains local `ChatMessage`, delivery state, roles, and parsed stream event
types; `sessions/report/` owns the resulting report screen and ViewModel. This
separation matters because a repair session is a long-lived server product
record, whereas a chat bubble and streaming cursor are client presentation
state. Do not make a chat screen's transient message list the system of record
for repair-session state.

Loading, empty, error, mutation-in-progress, and disabled-input cases are
explicit state. Some feature states are data classes with fields rather than a
sealed class, but they must still not express loading by a missing object. Keep
all UI-state fields immutable (`val`) and prefer immutable or replaced lists
when changing state. User-facing text belongs in string resources; logging
uses Timber, not platform logs or `println`.

## Authentication and HTTP boundary

`AuthProvider` is the application boundary for identity. The rest of the app
depends on this interface and its app-owned `AuthResult`, failure reasons, and
pending-link credential abstractions—never directly on `FirebaseAuth`.
`FirebaseAuthProvider` implements email/password account creation and sign-in,
retrieves Firebase ID tokens, and performs Google sign-in through Credential
Manager. When a Google identity collides with an existing account, the
ViewModel coordinates the explicit link-after-password flow rather than losing
the pending credential. Firebase itself retains and refreshes credentials; the
app does not persist raw bearer tokens.

`CoreModule` binds this provider, a lenient shared Kotlin `Json` instance, the
qualified IO dispatcher, `ContentResolver`, one `OkHttpClient`, and the
Retrofit service. `AuthInterceptor` obtains a Firebase token for every request
and adds it as a bearer token. On a 401 it closes the first response,
force-refreshes once, and retries; a second 401 signs the provider out. The
same authenticated `OkHttpClient` is shared by Retrofit and the SSE client,
so a stream receives the same authentication behavior as ordinary API calls.

`BikeDocApiClient` constructs Retrofit from `BuildConfig.API_BASE_URL` and the
serialization converter. The URL is a Gradle build-config field, defaulting to
the Android emulator's `10.0.2.2:8000` host and overridable with
`BIKEDOC_API_BASE_URL`; feature code must not read build properties or hardcode
hosts. `BikeDocApiService` is the one declaration point for `/v1` endpoints:
profile, bikes, repair sessions and turns, artifact upload, and reports.

The `api/models/` package describes wire request/response shapes with Kotlin
serialization. `safeApiCall` converts `HttpException`, network I/O failure,
and response-decoding failure into `ApiResult.Success` or the app-safe
`ApiResult.Error`; repositories prevent those transport exceptions from
reaching ViewModels. `ApiResult.Loading` is available as a common algebraic
case, but screen loading is owned by feature UI state. Network DTOs should not
be invented as Compose state or serialized directly from a screen. Map them at
the repository boundary when the feature needs a smaller, safer, or more
readable model, as bike-list and report code do.

`BikeRepository`, `SessionRepository`, `ArtifactRepository`, and
`ReportRepository` are the general API seams. Each has a default implementation
wrapping `BikeDocApiService`; the corresponding Hilt bindings are grouped in
the feature-oriented `BikeModule` and `DiagnosticModule`. This supports fake
repositories in ViewModel tests and avoids service construction inside a
feature. `ReportRepository` is intentionally more than a pass-through: it
decodes the API report envelope, validates schema version/type, maps supported
diagnostic and plan payloads to presentation-ready `RepairReport` variants,
and returns a controlled error for unsupported or malformed reports.

## Diagnostic session and streaming path

Opening a chat follows a deliberate load-before-stream sequence:

```text
chat route -> DiagnosticChatViewModel
  -> SessionRepository.getRepairSession
  -> apply durable session/current input state
  -> SseEventSource.connect(sessionId, cursor)
  -> parse SseEvent -> reduce DiagnosticChatUiState -> Compose rendering
```

After loading the repair session, the ViewModel begins event replay from the
start cursor, then retains the last received event ID for reconnection. Its
single event job is scoped to `viewModelScope`, cancelled before a replacement
connection and in `onCleared`. The loop treats a closed/failed connection as a
recoverable transport problem, reconnecting with bounded exponential backoff
and jitter until the phase has moved beyond the chat flow. This behavior makes
the server's durable SSE event log—not an in-memory Android conversation—the
reconnection source of truth.

`OkHttpSseEventSource` builds the events URL from the configured base URL,
sends both the `after` query parameter and `Last-Event-ID` when a cursor is
available, adapts OkHttp `EventSource` callbacks with `callbackFlow`, and
cancels the source when collection ends. `SseEvent` parses the server envelope
and maps known event names such as `turn.started`, assistant deltas/completion,
input requests, report creation, transitions, terminal completion, errors,
and heartbeats into sealed app types. Unknown or non-rendered events remain
safe no-ops; raw JSON or OkHttp events must never leak to UI.

The chat ViewModel is the reducer for these events. It adds assistant deltas to
a temporary streaming bubble, commits a completed assistant message, updates
the server-provided current input request, tracks the latest report, and turns
terminal/error events into input and delivery state. A user turn is first
shown optimistically with a stable client message ID, then posted through
`SessionRepository.createTurn`; rejection marks it failed so the user can
retry. Submission is gated by session status, active request constraints,
minimum required photos, and absence of an in-flight/streaming turn. Maintain
these guards when adding new input types—the backend remains authoritative,
but the app should not knowingly submit an invalid turn.

Photo attachment is a separate subflow. A selected URI becomes a
`DiagnosticPhotoAttachment` with upload status. The content-resolver preparer
runs on the injected IO dispatcher, preserves accepted JPEG/PNG/WebP content
or transcodes another decodable image to JPEG, assigns a client artifact ID,
and hands `PreparedDiagnosticPhoto` to `ArtifactRepository`. The repository
creates the multipart artifact request and returns only the app artifact
reference. On success the chat state selects that artifact for the eventual
turn; on failure it records a retryable per-attachment error. Do not read URI
bytes, perform bitmap conversion, or construct multipart bodies in Compose.

## Hilt, tests, and change guidance

Hilt bindings are intentionally localized. `CoreModule` contains shared
infrastructure and `AuthModule` the identity implementation; `HomeModule`,
`BikeModule`, `BikeListModule`, and `DiagnosticModule` each own their feature
interfaces and concrete bindings. Add a new feature dependency in its feature
module unless it genuinely crosses feature boundaries. New dispatcher use
should receive an injected qualifier such as `@IoDispatcher`, rather than a
hardcoded `Dispatchers.IO`, so tests can control execution.

Unit tests under `app/src/test/` cover the auth and navigation boundaries,
auth retry interceptor, turn serialization, SSE parsing, and the principal
ViewModels. They use `MainDispatcherRule`, coroutine test dispatchers, fake
repository/event-source implementations, and Turbine where flow assertions
need it. Add behavior tests beside the closest feature before or alongside a
workflow change; fake interfaces are preferred to heavy mocking. Compose UI
tests are appropriate when a composable contains behavior beyond straightforward
layout.

Before treating an Android change as complete, format and validate it from
`apps/android`: run `ktlint -F`, `ktlint check`, `detekt`, and
`./gradlew :app:testDebugUnitTest`; `./gradlew :app:compileDebugKotlin` is the
fast compile check. When changing a client-visible API payload, event, route,
or repair-session behavior, review the Android MVP spec and OpenAPI contract
with the backend change. Preserve the key seams: Firebase behind
`AuthProvider`, wire data behind repositories, streaming behind
`SseEventSource`, transient navigation outside durable UI state, and the API
as the source of truth for users, bikes, repair sessions, turns, artifacts,
and reports.
