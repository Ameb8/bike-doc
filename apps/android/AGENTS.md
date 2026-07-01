# Android App — AGENTS.md

This file governs `apps/android`. It is the enforcement layer for
architecture, layering, and code standards on the Kotlin/Compose client.

## Canonical Spec

`docs/specs/android/mvp-spec.md` is the canonical source of truth for screen
behavior, navigation flow, SSE event handling, and client-side architecture
decisions. Agents must treat it as authoritative over inference from this
file, prior code, or general Android conventions. If a task appears to
conflict with `mvp-spec.md`, stop and flag the conflict rather than silently
resolving it either direction.

## Commands

Agents must run the following commands and fix any reported issues before
considering a task complete. A task is not done if any fail.

- **Format:** `ktlint -F`
- **Lint/Check:** `ktlint check` + `detekt`
- **Unit tests:** `./gradlew :app:testDebugUnitTest`
- **Compile check (fast):** `./gradlew :app:compileDebugKotlin`

## Architectural Layering

```text
ui (composables) → viewmodel → repository → (retrofit service | local datasource)
```

- **Composables:** no business logic, no direct network/DB access, no
  ViewModel internals beyond the exposed `UiState`. Only screen-level
  composables connect to a `hiltViewModel()`; child composables receive plain
  state and lambdas (state hoisting).
- **ViewModels:** orchestrate one screen's state; no Retrofit/OkHttp types in
  their public API. Expose a single `StateFlow<UiState>` (sealed
  class/interface per screen) plus a `Channel`/`SharedFlow` for one-shot
  events (navigation, snackbars, errors). Never expose `MutableStateFlow`
  publicly.
- **Repositories:** single source of truth per domain (auth, bikes,
  diagnostic sessions). Translate network DTOs into domain models — DTOs
  never leak upward past the repository layer.
- **Network/local datasources:** dumb I/O only, no business logic.

## Data/UI Model Separation

Retrofit DTOs and UI-facing domain models are always distinct classes. A
network response type is never used directly as Compose UI state, and a UI
state data class is never serialized directly as a request body. Mapping
between the two happens in the repository layer. This mirrors the backend's
separation of database models from request/response schemas — do not collapse
the boundary "to save a file."

## SSE & Streaming

SSE handling must follow the pattern and event contract defined in
`docs/specs/android/mvp-spec.md`, including the existing `SseEvent` sealed
class reference artifact — copy that pattern rather than inventing a new one.

- Use OkHttp `EventSource`, not Ktor's SSE client.
- `EventSource` connections are owned and scoped by the ViewModel that
  initiates them (e.g. `DiagnosticChatViewModel`), tied to `viewModelScope`,
  and explicitly closed in `onCleared()`.
- Composables must never open or hold an `EventSource` directly.

## Code Standards

- **Compose State Hoisting:** Composables should be stateless where possible
  — accept state and lambdas as parameters rather than reading ViewModels
  directly.
- **Coroutines & Dispatchers:** Inject `CoroutineDispatcher`s via Hilt
  qualifiers rather than hardcoding `Dispatchers.IO`/`Dispatchers.Main` in
  ViewModels/repositories, so tests can substitute a `TestDispatcher`.
- **Retrofit/Network Layer:** All network calls go through repository
  interfaces; ViewModels never reference Retrofit services directly. Wrap
  responses in a sealed `ApiResult`/`Result` type — do not let raw
  `HttpException`/`IOException` leak past the repository layer.
- **Dependency Injection (Hilt):** Use a localized `@Module` per feature area
  (e.g. `AuthModule`, `BikeModule`, `DiagnosticModule`) rather than one
  monolithic `NetworkModule` or `AppModule`. Each module owns the bindings
  relevant to its feature's repository/datasource graph; shared
  cross-cutting bindings (base `Retrofit`/`OkHttpClient`, dispatchers,
  `AuthProvider`) live in a small `CoreModule`.
- **Navigation:** Use Navigation Compose with typed route arguments (sealed
  class or `@Serializable` route objects); no raw string-concatenated routes.
- **Error/Loading State:** UI state sealed classes must model `Loading`,
  `Success`, `Error` (and `Empty` where relevant) explicitly — no nullable
  "loading-by-absence" patterns.
- **Resource Strings:** No hardcoded user-facing strings in composables; use
  `stringResource(R.string.x)`.
- **Immutability:** UI state data classes must be immutable (`val` only,
  immutable collection types) so Compose recomposition skipping works
  correctly.
- **Logging:** Use Timber exclusively (`Timber.d`, `Timber.e`, etc.) for all
  logging. Do not use `android.util.Log`, `println`, or any other logging
  approach — if Timber isn't already initialized/available in a file, that's
  a signal to wire it in, not to fall back to another log style.
- **Auth Boundary:** ViewModels and repositories depend on the `AuthProvider`
  interface, never on `FirebaseAuth` directly.
- **Testing:** ViewModels get unit tests with Turbine for `Flow` assertions
  and fake repository implementations (avoid heavy mocking of simple
  interfaces). Composables containing real logic (not pure layout) get a
  Compose UI test where practical.

## App Code Overview

