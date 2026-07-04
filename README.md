# BikeDoc

Bike Doc exists because most people who own a bike don't actually know how to diagnose what's wrong with it. Something clicks, shifts badly, or feels loose, and the average rider has no idea whether that's a five-minute fix or a reason to stop riding immediately. The gap isn't a lack of information out there. It's that generic troubleshooting advice, whether from a forum post or a general-purpose chatbot, doesn't know your bike, doesn't remember your last repair, and can't see the photo of your derailleur well enough to tell you anything useful and specific. Bike Doc's job is to close that gap: take a rider from "something feels wrong" to a confident decision about whether to fix it themselves or take it to a shop, and then actually walk them through the fix if it's safe to do.

The reason this needs to be a dedicated system rather than just a well-prompted general AI comes down to structure and accountability. Diagnosing a bike, planning a repair, and executing that repair are different tasks with different failure modes, and treating them as one long conversation invites context rot and sloppy reasoning. Bike Doc splits these into distinct phases, each with a fresh context and a clean structured handoff to the next.

Safety is the other piece that a generic assistant can't be trusted with. A chatbot can say "this seems risky, maybe see a mechanic" and then, three messages later, cheerfully explain how to do it anyway because the conversation drifted. Bike Doc treats safety severity as something the backend enforces, not just a suggestion baked into a prompt. If a diagnosis flags a blocking safety concern, like a suspected frame crack or brake failure, the system won't let a DIY repair proceed no matter how the model's output is worded that day. That's a guarantee a general-purpose assistant simply isn't built to make.

## Setup And Running

Bike Doc currently runs as two applications:

- `apps/api`: a FastAPI backend with PostgreSQL persistence, artifact storage,
  Firebase/dev authentication, and an internal Google ADK diagnostic agent.
- `apps/android`: an Android Kotlin/Compose app that uses Firebase
  Authentication and calls the backend `/v1` API.

The repository root `.env.example` is the canonical environment template for
the backend. Do not use `.env.prod` as a template; local files may contain
machine-specific paths or real secrets.

### Prerequisites

Install these tools before running the full app:

- Docker and Docker Compose, if using the compose-based API/Postgres setup.
- Python 3.12 and `uv`, if running the API directly with the root `Taskfile.yml`.
- `go-task` (`task`) for the API helper commands.
- PostgreSQL 16, if running the API directly without Compose.
- Android Studio with JDK 17 and the Android SDK, for the Android app.
- A Firebase project with Authentication enabled, for the Android app and
  production-like backend auth.
- A Google Cloud project, if using GCS artifact storage or Vertex AI.
- `gcloud`, if using Application Default Credentials locally for GCS or
  Vertex AI.
- `agents-cli`, when validating or inspecting the ADK agent graph.

### Backend Environment

Create the backend environment file from the root template:

```bash
cp .env.example .env
```

Important backend settings:

- `BIKE_DOC_API_ENVIRONMENT`: use `local` for development and `production` for
  deployed production configuration.
- `BIKE_DOC_API_DATABASE_URL`: SQLAlchemy async PostgreSQL URL.
- `BIKE_DOC_API_AUTH_MODE`: `dev`, `local_unsigned_jwt`, or `firebase`.
  Production rejects anything except `firebase`.
- `BIKE_DOC_API_FIREBASE_PROJECT_ID`: required when auth mode is `firebase`.
- `BIKE_DOC_API_ARTIFACT_STORAGE_PROVIDER`: `local` or `gcs`.
- `BIKE_DOC_API_ARTIFACT_LOCAL_STORAGE_ROOT`: local upload directory when using
  local artifact storage.
- `BIKE_DOC_API_ARTIFACT_GCS_BUCKET`: required when artifact storage is `gcs`.
- `BIKE_DOC_API_DIAGNOSTIC_LLM_PROVIDER`: `google_ai` or `vertex_ai`.
- `BIKE_DOC_API_DIAGNOSTIC_AGENT_MODEL`: Gemini model used by the diagnostic
  ADK agent.
- `GEMINI_API_KEY` or `GOOGLE_API_KEY`: required for the `google_ai` provider.
- `GOOGLE_GENAI_USE_VERTEXAI=true`, `GOOGLE_CLOUD_PROJECT`, and
  `GOOGLE_CLOUD_LOCATION`: required for the `vertex_ai` provider.
- `GOOGLE_APPLICATION_CREDENTIALS`: optional local path to a service account
  JSON file for Google ADC. Prefer attached runtime identity in production.
- `BIKE_DOC_API_CORS_ORIGINS`: JSON list of allowed browser origins, if a web
  client is calling the API.
- `BIKE_DOC_API_LOG_LEVEL` and `BIKE_DOC_API_LOG_FORMAT`: optional logging
  controls. Use `BIKE_DOC_API_LOG_FORMAT=json` for production-style logs.

The default `.env.example` is compose-oriented: its database URL points at
`db`, the Compose service name. If you run the API directly on the host with
`task run`, change `BIKE_DOC_API_DATABASE_URL` to use `localhost`, for example:

```text
BIKE_DOC_API_DATABASE_URL=postgresql+asyncpg://bikedoc:bikedoc@localhost:5432/bikedoc
```

### Run The API With Compose

Compose starts the API container and a PostgreSQL container:

```bash
docker compose up --build
```

The API listens on `http://localhost:${BIKE_DOC_API_PORT}`, usually
`http://localhost:8000`.

Run database migrations after the database is healthy:

```bash
docker compose exec api uv run alembic upgrade head
```

The current `compose.yaml` passes core API settings into the container and is
best suited for local API plus Postgres development. If you want compose to run
the live diagnostic agent or GCS storage path, extend the API service
environment to pass the relevant provider variables as well, such as
`BIKE_DOC_API_ARTIFACT_STORAGE_PROVIDER`, `BIKE_DOC_API_ARTIFACT_GCS_BUCKET`,
`GOOGLE_APPLICATION_CREDENTIALS`, `GOOGLE_CLOUD_PROJECT`,
`BIKE_DOC_API_DIAGNOSTIC_LLM_PROVIDER`, `GEMINI_API_KEY`, `GOOGLE_API_KEY`,
`GOOGLE_GENAI_USE_VERTEXAI`, and `GOOGLE_CLOUD_LOCATION`. If credentials are
file-based, mount the credential file into the container and point
`GOOGLE_APPLICATION_CREDENTIALS` at the in-container path.

### Run The API Locally With Task

Use this option when you want the API process to read your host environment,
use host ADC from `gcloud`, or avoid mounting credential files into Compose.

Start PostgreSQL first. You can use only the compose database:

```bash
docker compose up -d db
```

Set `BIKE_DOC_API_DATABASE_URL` in `.env` to use `localhost`, then install
dependencies and run migrations:

```bash
task sync
cd apps/api
set -a && . ../../.env && set +a
uv run alembic upgrade head
cd ../..
```

Run the API:

```bash
task run
```

Useful API commands:

```bash
task test
task format
task check
task build
```

For quick local API calls with fixed-token auth, keep:

```text
BIKE_DOC_API_ENVIRONMENT=local
BIKE_DOC_API_AUTH_MODE=dev
BIKE_DOC_API_DEV_AUTH_TOKEN=dev-token
```

Then send:

```http
Authorization: Bearer dev-token
```

### Firebase Auth Setup

Firebase is used by the Android app and by the backend in production-like auth
mode.

For local backend-only development, Firebase is optional because
`BIKE_DOC_API_AUTH_MODE=dev` and `local_unsigned_jwt` are supported in local
and test environments.

For Android development:

1. Create or select a Firebase project.
2. Add an Android app for package `com.bikedoc.android`. The debug build uses
   application id `com.bikedoc.android.debug`, so register that app too if you
   want debug builds to use a separate Firebase application.
3. Enable Firebase Authentication and the email/password sign-in provider.
4. Download `google-services.json` for the Android app and place it at
   `apps/android/app/google-services.json`.
5. Build or run the Android app from `apps/android`.

For production backend auth:

1. Set `BIKE_DOC_API_ENVIRONMENT=production`.
2. Set `BIKE_DOC_API_AUTH_MODE=firebase`.
3. Set `BIKE_DOC_API_FIREBASE_PROJECT_ID` to the Firebase project id.
4. Ensure the Android app sends Firebase ID tokens as
   `Authorization: Bearer <firebase-id-token>`. The existing Android
   `AuthInterceptor` obtains the Firebase ID token and attaches that header.

The backend verifies Firebase ID tokens for the configured project and maps the
Firebase subject to an internal `users.auth_subject` row. Product data is owned
by the internal `users.id`, not by the Firebase UID.

### Artifact Storage Setup

Local artifact storage is simplest for development:

```text
BIKE_DOC_API_ARTIFACT_STORAGE_PROVIDER=local
BIKE_DOC_API_ARTIFACT_LOCAL_STORAGE_ROOT=apps/api/.local/artifacts
```

For GCS artifact storage:

1. Create a Google Cloud Storage bucket.
2. Set `BIKE_DOC_API_ARTIFACT_STORAGE_PROVIDER=gcs`.
3. Set `BIKE_DOC_API_ARTIFACT_GCS_BUCKET` to the bucket name.
4. Provide Google Application Default Credentials with permission to read and
   write objects in that bucket.

For local GCS testing, either run:

```bash
gcloud auth application-default login
```

or set:

```text
GOOGLE_APPLICATION_CREDENTIALS=/absolute/path/to/service-account.json
GOOGLE_CLOUD_PROJECT=<google-cloud-project-id>
```

For production, attach a service account to the runtime, such as Cloud Run,
GKE Workload Identity, or a VM service account. Grant that service account the
minimum bucket permissions required for artifact object access. Avoid shipping
long-lived service account JSON keys with production deployments.

### Diagnostic Agent And Model Provider Setup

The diagnostic phase uses Google ADK internally. The public API stays the
FastAPI `/v1` API; do not run the app as the default ADK server.

For Google AI Studio / Gemini API key mode:

```text
BIKE_DOC_API_DIAGNOSTIC_LLM_PROVIDER=google_ai
GEMINI_API_KEY=<ai-studio-api-key>
# or GOOGLE_API_KEY=<ai-studio-api-key>
```

For Vertex AI mode:

```text
BIKE_DOC_API_DIAGNOSTIC_LLM_PROVIDER=vertex_ai
GOOGLE_GENAI_USE_VERTEXAI=true
GOOGLE_CLOUD_PROJECT=<google-cloud-project-id>
GOOGLE_CLOUD_LOCATION=<vertex-location>
```

Local Vertex AI runs need ADC through `gcloud auth application-default login`
or `GOOGLE_APPLICATION_CREDENTIALS`. Production Vertex AI runs should use an
attached service account with the required Vertex AI permissions.

The model and generation settings are controlled by:

```text
BIKE_DOC_API_DIAGNOSTIC_AGENT_MODEL=gemini-2.5-flash
BIKE_DOC_API_DIAGNOSTIC_AGENT_TEMPERATURE=0.2
BIKE_DOC_API_DIAGNOSTIC_AGENT_MAX_OUTPUT_TOKENS=2048
BIKE_DOC_API_DIAGNOSTIC_AGENT_TIMEOUT_SECONDS=30
```

When changing ADK agent structure or tools, validate the agent entrypoint:

```bash
cd apps/api
agents-cli lint --fix
```

Use `agents-cli deploy --dry-run` when the graph is ready for a static
deployment-style validation.

### Android App Setup

The Android app reads the backend URL from the Gradle property
`BIKEDOC_API_BASE_URL`. If the property is unset, debug builds default to the
Android emulator host URL:

```text
http://10.0.2.2:8000/
```

Use that default when the API is running on your host machine and the app runs
in the Android emulator. For a physical device, set the property to a URL the
device can reach, such as your machine's LAN IP or a deployed API URL.

You can set the property in `apps/android/gradle.properties`, in your user
Gradle properties, or on the command line:

```bash
cd apps/android
./gradlew :app:installDebug -PBIKEDOC_API_BASE_URL=http://10.0.2.2:8000/
```

Common Android commands:

```bash
cd apps/android
./gradlew :app:compileDebugKotlin
./gradlew :app:testDebugUnitTest
./gradlew :app:ktlintCheck
./gradlew :app:detekt
```

For end-to-end Android auth against the backend, the backend must use
`BIKE_DOC_API_AUTH_MODE=firebase` with the same Firebase project that issued
the Android ID token. If the backend is in `dev` auth mode, the current Android
app will still send Firebase tokens, so authenticated product calls will not
match the fixed dev token.

### Production Configuration Checklist

A production API deployment needs:

- `BIKE_DOC_API_ENVIRONMENT=production`.
- `BIKE_DOC_API_DEBUG=false`.
- `BIKE_DOC_API_AUTH_MODE=firebase`.
- `BIKE_DOC_API_FIREBASE_PROJECT_ID` set to the production Firebase project.
- A production PostgreSQL database and
  `BIKE_DOC_API_DATABASE_URL` set to its async SQLAlchemy URL.
- Alembic migrations applied with `uv run alembic upgrade head`.
- `BIKE_DOC_API_ARTIFACT_STORAGE_PROVIDER=gcs`.
- `BIKE_DOC_API_ARTIFACT_GCS_BUCKET` set to the production artifact bucket.
- Runtime identity with GCS permissions, preferably an attached service
  account rather than a JSON key.
- Either Google AI API key configuration or Vertex AI configuration for the
  diagnostic ADK agent.
- `BIKE_DOC_API_LOG_FORMAT=json` and an appropriate `BIKE_DOC_API_LOG_LEVEL`.
- `BIKE_DOC_API_CORS_ORIGINS` set only to trusted frontend origins, if browser
  clients are used.
- Android release configuration with a production Firebase app
  `google-services.json` and `BIKEDOC_API_BASE_URL` pointing at the production
  API.

Keep local/dev and production Firebase projects separate when possible. The
current code supports separate dev and production Firebase setup by changing
the Android `google-services.json`, backend `BIKE_DOC_API_FIREBASE_PROJECT_ID`,
and deployed backend URL per environment.

## Architecture

### Backend API

The backend lives in `apps/api` and is a custom FastAPI service using a Python
`src/` package layout. The public application package is `bike_doc_api`; the
ADK compatibility entrypoint package is `bike_doc_api_adk_agent`. The API is
implemented directly rather than generated from the OpenAPI file, but its
Pydantic request and response models are kept in `bike_doc_api/schemas` and are
intended to stay aligned with `docs/specs/openapi.yaml`.

The FastAPI app is created in `bike_doc_api.main:create_app`. Startup resolves
typed settings from `bike_doc_api.core.config.Settings`, validates the runtime
configuration for artifact storage, configures logging, installs the shared
error handlers, applies CORS when configured, and mounts the versioned router at
`/v1`. The route tree is assembled in `bike_doc_api.api.router` from small
route modules under `bike_doc_api/api/v1`.

The current implemented HTTP surface is:

- `GET /v1/me`: resolves the authenticated user.
- `GET /v1/bikes`, `POST /v1/bikes`, `GET /v1/bikes/{bikeId}`,
  `PATCH /v1/bikes/{bikeId}`, and `DELETE /v1/bikes/{bikeId}`: manage owned
  bike profiles. Deletes are soft deletes and are blocked if the bike has repair
  session history.
- `GET /v1/repair-sessions`, `POST /v1/repair-sessions`, and
  `GET /v1/repair-sessions/{sessionId}`: create and inspect diagnostic repair
  sessions for owned bikes. Listing is owner-scoped, bike-scoped, status
  filterable, and cursor paginated.
- `POST /v1/repair-sessions/{sessionId}/turns`: accepts a user diagnostic turn,
  persists it, emits the first turn event, and schedules diagnostic processing
  as a FastAPI background task. The route returns `202 Accepted` with the turn
  id, event stream URL, and updated session snapshot.
- `GET /v1/repair-sessions/{sessionId}/events`: streams server-sent events for a
  repair session. The stream first replays persisted events after the requested
  cursor or `Last-Event-ID`, then subscribes to process-local live events and
  emits heartbeats until timeout.
- `POST /v1/artifacts`: accepts multipart uploads, stores the object through the
  configured storage provider, and persists provider-neutral artifact metadata.
- `GET /v1/repair-sessions/{sessionId}/reports` and
  `GET /v1/repair-sessions/{sessionId}/reports/{reportId}`: read persisted phase
  report envelopes for an owned repair session.

The backend is deliberately layered. Route handlers validate HTTP input, resolve
dependencies, call services, and return Pydantic schemas. Product behavior is in
`bike_doc_api/services`: bike ownership rules, session creation, turn
acceptance, idempotency, artifact validation, report persistence, event
streaming, and safety state transitions live there rather than in route
handlers. Persistence is isolated in `bike_doc_api/repositories`, SQLAlchemy ORM
models are in `bike_doc_api/models`, and database/session wiring is in
`bike_doc_api/db`. External boundaries are under `bike_doc_api/providers`,
currently with local filesystem and GCS storage providers; price and repair
reference provider packages exist only as stub boundaries.

Persistence is PostgreSQL through SQLAlchemy 2.0 async sessions and `asyncpg`.
`bike_doc_api.db.session` creates cached async engines/sessionmakers per database
URL, and request-scoped sessions commit on success or roll back on error. The
current Alembic migration creates the diagnostic persistence schema:

- `users`: app user records mapped from authenticated identities.
- `bike_profiles`: owner-scoped bike metadata with soft-delete support.
- `repair_sessions`: the durable product session state, including current phase,
  public status, safety state, latest event sequence, active safety flags,
  current input request, execution progress placeholder, and pointers to latest
  phase reports.
- `repair_phase_sessions`: one app-owned phase/session row per repair-session
  phase. For the diagnostic phase this stores the opaque ADK session id without
  exposing it through the public API.
- `repair_turns`: accepted user turns with `client_turn_id`, request hash,
  phase, message payload, and the sequence of the starting event.
- `repair_session_events`: per-session ordered public events used for SSE replay
  and live streaming.
- `artifact_refs`: metadata for uploaded artifacts, including ownership,
  parent session or bike, purpose, MIME/media details, content hash, status, and
  storage location.
- `phase_reports`: persisted diagnostic report envelopes and future-compatible
  slots for plan, execution, and shop-referral reports.

The database enforces many of the product invariants directly: ID prefixes,
allowed enum values, nonblank text fields, JSON object/array shape checks,
client idempotency hash pairing, unique per-user client ids, unique per-session
turn ids, unique per-session event sequences, and purpose-specific artifact
parent rules. Services still perform owner checks and user-facing validation
before hitting those constraints so API errors stay predictable.

Authentication is resolved at the dependency boundary in `bike_doc_api.api.deps`.
Every product route depends on `get_current_user`, which validates a bearer token
according to `BIKE_DOC_API_AUTH_MODE`. The implemented modes are a fixed
development token, local unsigned JWT fixtures, and Firebase ID tokens. A
validated identity is normalized to an app user through `AuthService`; new users
are created on first successful resolution when needed. Production settings
reject non-Firebase auth.

Errors use a single public envelope from `bike_doc_api.core.errors`:
`{"error": {"code": "...", "message": "...", "details": ...}}`. Domain services
raise typed `AppError` subclasses for authentication failures, owner-scoped
missing resources, validation failures, idempotency conflicts, invalid session
state, safety policy violations, stale internal phase sessions, oversized
uploads, and generic server errors. FastAPI request validation errors are also
mapped into the same envelope.

Diagnostic safety is a backend invariant, not only an agent prompt guideline.
`SafetyService` validates diagnostic safety flags, rejects contradictory
duplicates, requires blocking flags to block repair instructions, restricts
diagnostic flags to the diagnostic phase, deduplicates active flags by
`(code, phase)`, derives the session safety state, and emits safety escalation
event data when warning or blocking risk increases. `ReportService` applies the
same safety rules when saving a diagnostic report, updates the repair session,
stores the report, and emits public report/phase-transition events in the same
transaction. If safety is blocking, the session lands in `blocked_safety` rather
than being allowed to move toward repair instructions.

### Agent

The agent implementation is inside `bike_doc_api/adk`. Google ADK is an
internal library boundary for the backend; raw ADK sessions, tool context,
provider events, prompts, and model settings are not exposed through the public
API. The Android app and any other clients talk only to Bike Doc's `/v1`
product API and consume product-level events, reports, artifacts, and session
state.

Only the diagnostic phase is implemented as a real agent today. The repository
has `adk/agents/planning.py` and `adk/agents/execution.py` placeholder modules,
plus plan and execution report schema placeholders, but there is no current
planning or execution agent flow wired into the API. The live agent path is:

1. A user creates a repair session for an owned bike. New sessions start in
   `phase=diagnostic`, `status=created`, and `safety_state=ok`.
2. The client uploads any diagnostic photos or other supported artifacts through
   `/v1/artifacts`.
3. The client posts a diagnostic turn to
   `/v1/repair-sessions/{sessionId}/turns`.
4. `TurnService` verifies ownership, verifies the session is in an accepting
   diagnostic state, validates referenced artifacts, enforces
   `client_turn_id` idempotency, creates or resumes the diagnostic phase session,
   persists the turn, marks the repair session `running`, and emits
   `turn.started`.
5. The route schedules `execute_diagnostic_turn_background`, which rebuilds the
   service/repository graph around a fresh async database session and runs the
   diagnostic orchestrator outside the request scope.
6. The client watches `/v1/repair-sessions/{sessionId}/events` to receive
   assistant deltas, artifact references, input requests, safety escalations,
   report creation, phase transitions, errors, and turn completion.

ADK session ownership is split between durable product state and process-local
ADK state. `DiagnosticPhaseSessionManager` ensures one
`repair_phase_sessions` row exists for the diagnostic phase and stores the
opaque ADK session id there. The actual ADK session service is
`google.adk.sessions.InMemorySessionService`, cached for the FastAPI process.
That means the product database remembers which ADK session id belongs to a
repair session, but the conversational ADK state itself is currently
process-local. If a background run cannot find the in-memory ADK session,
`DiagnosticRunner` emits a recoverable `diagnostic_session_unavailable` error
instead of pretending the stale context is valid.

The diagnostic agent is constructed by
`bike_doc_api.adk.agents.diagnostic.create_diagnostic_agent`. It loads the
versioned prompt from `adk/prompts/diagnostic.md`, uses the configured model from
`BIKE_DOC_API_DIAGNOSTIC_AGENT_MODEL` (default `gemini-2.5-flash`), and installs
only the V1 diagnostic tool catalog. Runtime configuration supports Google AI
API keys or Vertex AI environment variables; non-test environments validate
those credentials before building diagnostic dependencies.

The V1 diagnostic tool catalog is defined in `adk/tools/tool_catalog.py` and
contains exactly these ADK `FunctionTool`s:

- `get_bike_profile`: returns the owned bike profile and user skill level for
  the active repair session.
- `lookup_repair_history`: checks relevant prior repair records for the active
  bike. The current service implementation performs ownership and diagnostic
  context checks but returns no entries because repair-history persistence is not
  implemented yet.
- `list_diagnostic_artifacts`: returns metadata for ready diagnostic artifacts
  associated with the active repair session.
- `request_diagnostic_input`: persists a structured input request on the repair
  session, marks the session `awaiting_user`, and emits an `input.requested`
  event.
- `raise_safety_flag`: persists a backend-validated diagnostic safety flag,
  updates active safety flags and session safety state, and emits
  `safety.escalated` when severity increases enough to matter publicly.
- `save_diagnostic_report`: validates and persists a `diagnostic_report.v1`
  payload, injects the server-owned diagnostic session id, updates the repair
  session's latest diagnostic report pointer and status, and emits report and
  phase-transition events.

Each tool accepts an app-owned `DiagnosticToolContext` extracted from ADK
`ToolContext.state["app_context"]`. That context contains only product IDs and
safe product data: user id, user skill level, repair session id, active phase,
diagnostic phase-session id, turn id, artifact ids, bike profile, repair history
entries, and diagnostic artifact metadata. Tool wrappers normalize validation,
not-found, stale-session, safety-policy, and unexpected failures into structured
tool responses so the runner can handle tool failures without leaking internal
exceptions.

`DiagnosticTurnOrchestrator` connects an accepted turn to the ADK runner. Before
calling the model, it seeds the run with durable product context by invoking the
same service-backed tools used by the agent: bike profile, repair history, and
diagnostic artifact metadata. It also emits `artifact.referenced` events for
artifacts included directly in the user's turn. It then builds a
`DiagnosticRunnerRequest` containing the ADK session id, user text, artifact
ids, seed context, repair session id, turn id, and diagnostic phase-session id.

`DiagnosticRunner` is the adapter between Google ADK events and Bike Doc public
events. It calls ADK `Runner.run_async` with a GenAI user message and a
`state_delta` containing `app_context`. As ADK emits events, the runner
coalesces streamed text into bounded assistant deltas, maps final text into an
`assistant.message.completed` event, and translates state-mutating tool
responses into app-owned runner events for input requests, safety escalations,
and report completion. Raw ADK event objects never leave this adapter.

The orchestrator persists public side effects through `EventService` and the
domain services rather than writing directly to client-visible state. Text
deltas become `assistant.delta`; final assistant text becomes
`assistant.message.completed`; tool-created input requests, safety flags, and
reports are persisted by their owning services; and the orchestrator finishes
each turn with `turn.completed` using the current repair-session snapshot. If
diagnostic processing throws an unexpected exception, the orchestrator appends a
retryable public error and returns the session to `awaiting_user`. If a
background setup failure happens before orchestration starts, the background
worker emits a safe error event when it can and avoids leaving the session
permanently `running`.

## Project Evidence for Course Tools

This project demonstrates several of the course tool categories through the
implemented backend design, not only through planned future work. The strongest
evidence is the ADK diagnostic agent, the service-backed ADK tool catalog, the
agent-specific safety controls, the Agents CLI compatibility entrypoint, and
the deployment-oriented decision to wrap ADK inside a product FastAPI service.

### Agent / Multi-Agent System: Google ADK Diagnostic Agent

Bike Doc currently implements one real Google ADK agent: the diagnostic phase
agent. The planning and execution phase modules exist as placeholders, but the
diagnostic phase is the implemented agent path used by the API.

The diagnostic agent is constructed in
`apps/api/src/bike_doc_api/adk/agents/diagnostic.py` by
`create_diagnostic_agent`. That function creates a real
`google.adk.agents.Agent` with:

- a named agent identity, `diagnostic_agent`;
- a versioned diagnostic prompt loaded from `adk/prompts/diagnostic.md`;
- a configurable Gemini model, defaulting to `gemini-2.5-flash`;
- a bounded V1 tool catalog built with ADK `FunctionTool`s.

The important learning demonstrated here is that the agent is not just a plain
chat endpoint. It is connected to product state through explicit tools and a
phase-based workflow. A user turn flows through the product API, is accepted and
persisted, then is processed by ADK in a background task. The Android client
does not call ADK directly. It posts product turns and receives product events.

The implemented diagnostic turn flow is:

1. The client posts a diagnostic turn to
   `POST /v1/repair-sessions/{sessionId}/turns`.
2. `TurnService` validates ownership, phase state, artifact references, and
   idempotency, then persists the turn and emits `turn.started`.
3. The route schedules `execute_diagnostic_turn_background` as a FastAPI
   background task.
4. The background worker rebuilds the service and repository graph around a
   fresh async database session.
5. The worker constructs the diagnostic ADK agent and `DiagnosticRunner`.
6. `DiagnosticTurnOrchestrator` seeds the run with durable product context,
   including bike profile, repair history, and diagnostic artifact metadata.
7. `DiagnosticRunner` calls ADK `Runner.run_async`, converts raw ADK events into
   app-owned runner events, and streams assistant deltas back through the
   product event log.

This demonstrates agentic behavior beyond simple chat because the model has
bounded actions it can take through tools: it can inspect product context,
request more input, list uploaded diagnostic photos, raise a safety flag, and
save a structured diagnostic report. The agent participates in a product
workflow with persistent state, typed reports, SSE streaming, and backend-owned
phase transitions.

### ADK Tools as MCP-Like Capability Boundaries

Bike Doc does not currently implement an MCP server, so this project should not
claim the MCP Server category as completed. However, the ADK tool layer
demonstrates a closely related design skill: exposing controlled backend
capabilities to an agent through narrow, typed tool interfaces.

The V1 diagnostic tool catalog lives in
`apps/api/src/bike_doc_api/adk/tools/tool_catalog.py` and exposes exactly six
ADK `FunctionTool`s:

- `get_bike_profile`
- `lookup_repair_history`
- `list_diagnostic_artifacts`
- `request_diagnostic_input`
- `raise_safety_flag`
- `save_diagnostic_report`

These tools are not thin prompt tricks. Each one is backed by product services
and repositories. For example, `raise_safety_flag` calls the backend safety
service, `save_diagnostic_report` calls the report service, and
`request_diagnostic_input` persists state through the turn/session service.

The important architectural point is that the model is not allowed to invent
server-owned context. Tool wrappers extract an app-owned
`DiagnosticToolContext` from ADK `ToolContext.state["app_context"]`. That
context contains server-controlled identifiers such as user id, repair session
id, phase-session id, active phase, and turn id. The model can provide
diagnostic inputs, but it cannot override ownership, user identity, the active
session, or raw ADK session ids.

If this project later needed to satisfy the MCP Server category directly, the
cleanest path would be to wrap a subset of these same service-backed
capabilities as MCP tools. The current ADK tool catalog already defines the
right capability boundaries; it simply exposes them to ADK rather than through
the MCP protocol.

### Agent-Specific Security Features

The main security feature demonstrated by Bike Doc is agent safety control:
the backend enforces what the agent may cause the product to do. This is
different from normal API authentication or generic backend security. The
security concern here is that an LLM should not be able to produce unsafe repair
instructions or silently bypass a safety-critical workflow rule.

Bike Doc handles that by making safety a backend invariant. The diagnostic
agent can propose or raise a safety flag through the `raise_safety_flag` tool,
and it can include safety flags in a diagnostic report, but those flags are not
authoritative until backend services validate them.

`apps/api/src/bike_doc_api/services/safety.py` owns the safety policy. It:

- accepts only the fixed V1 diagnostic safety flag codes;
- rejects unknown or malformed safety flags;
- rejects non-diagnostic phase values for diagnostic V1 safety inputs;
- requires a nonblank user-facing safety message;
- requires every `blocking` flag to set `blocks_repair_instructions: true`;
- deduplicates active flags by `(code, phase)`;
- keeps the highest active severity;
- derives the session safety state from validated active flags;
- emits `safety.escalated` event data when public risk increases.

This matters because the model cannot simply say a dangerous repair is safe in
later text. If a blocking issue is detected, such as suspected frame or fork
damage, suspected brake failure, carbon damage, or another safety-critical
condition, the server moves the repair session to a blocked safety state. The
agent's words are no longer the final control point; product state is.

The report path applies the same rule. When the agent saves a diagnostic report,
`ReportService` validates the report's safety flags and reconciles them with
any safety flags already raised during the turn. If the final state is blocking,
the session cannot proceed toward DIY repair instructions. This makes safety
resistant to prompt drift, contradictory model output, and later conversation
turns that might otherwise weaken the warning.

Bike Doc also limits what agent tools can do by design:

- ADK tools receive server-owned context from `ToolContext.state`, not from
  model-supplied parameters.
- Tools normalize errors into structured tool responses instead of exposing raw
  backend exceptions.
- Raw ADK events, prompts, provider metadata, and raw ADK session ids are not
  exposed through the public API.
- State-mutating tools write through backend services, so validation,
  persistence, event emission, and safety transitions stay centralized.

This is the project's strongest security argument: it demonstrates how to make
an AI agent safer by constraining its actions through backend-owned policy and
state transitions rather than trusting prompt instructions alone.

### Agent Skills / Agents CLI

Bike Doc includes an Agents CLI-compatible ADK entrypoint in
`apps/api/src/bike_doc_api_adk_agent/__init__.py`. That package exposes a
`root_agent` so the diagnostic agent can be discovered by ADK/Agents CLI
tooling while the production application still runs as the custom FastAPI
service.

This is intentionally separate from the product runtime. The entrypoint exists
for agent tooling compatibility, validation, and inspection. The user-facing
application continues to expose Bike Doc's `/v1` API rather than default ADK
development endpoints.

The test `apps/api/tests/unit/adk/test_agents_cli_entrypoint.py` verifies that
the Agents CLI entrypoint exposes the same diagnostic agent name and prompt as
the backend diagnostic agent. Backend agent notes also call out
`agents-cli lint` and dry-run validation as part of the expected ADK workflow
when the agent graph is in use.

The learning demonstrated here is that ADK agent code can be made visible to
agent development tooling without forcing the whole product to adopt ADK's
default serving shape.

### Deployability: FastAPI Wrapper Around ADK

Bike Doc is designed for deployment as one product backend service rather than
as a default ADK API server. This is a deliberate deployment choice.

The default ADK development server is useful for testing an agent directly, but
it is not the right public surface for this app. Bike Doc needs product routes
for users, bikes, repair sessions, artifact uploads, reports, event replay,
idempotent turn acceptance, and mobile-friendly SSE streaming. It also needs
backend-owned safety enforcement and database transactions around product
state. Those concerns belong in the product API, not in raw ADK endpoints.

The custom FastAPI app gives the project a deployment boundary that looks like
a normal production API:

- The app is created by `bike_doc_api.main:create_app`.
- Versioned product routes are mounted under `/v1`.
- Settings are loaded from environment variables through
  `BIKE_DOC_API_...` configuration.
- The backend can run locally with `task run`, test with `task test`, verify
  with `task check`, and build as a package with `task build`.
- PostgreSQL persistence is handled through async SQLAlchemy sessions.
- Artifact storage can be local for development or GCS for deployed
  environments.
- ADK runtime credentials are validated before diagnostic execution starts.

Wrapping ADK this way improves deployability because the deployed service has a
stable public API independent of ADK's internal runtime shape. Mobile clients
do not need to know about ADK sessions, ADK events, ADK tool context, model
provider details, or prompt structure. The backend can change how it runs ADK
internally while preserving the `/v1` contract.

It also gives the application better operational behavior:

- Long-running model work is moved out of the HTTP request lifecycle with
  FastAPI background tasks.
- Accepted turns return `202 Accepted`, so the client is not stuck waiting for
  model inference.
- Assistant output streams through persisted product events, so clients can
  replay from a cursor after reconnecting.
- Product state changes and safety transitions are committed through the same
  service/repository boundaries used by non-agent routes.
- Runtime dependencies such as database URL, model provider, API keys, storage
  provider, and GCS bucket are environment-configured instead of hardcoded.

The current deployment story is therefore "deployment-ready backend
architecture," not "already deployed production system." The project can be run
as a FastAPI service today, and the code is structured so a future deployment
can expose one stable product API while keeping ADK as an internal orchestration
library.
