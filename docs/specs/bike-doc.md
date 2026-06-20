# Bike Doc High-Level Design Spec

Status: Draft v0.2
Last updated: 2026-06-20

*v0.2 revises v0.1 per design review: clarifies the phase/session/turn
relationship, makes safety severity a server-enforced invariant rather than a
prompting convention, names vendor concentration risk explicitly, and adds
notes on price-lookup staleness and agent-loop backpressure.*

## 1. Purpose

Bike Doc is an Android-first AI bike mechanic assistant. It helps a rider
diagnose bicycle problems, decide whether a repair is safe and worthwhile to
attempt, estimate parts/tools/shop cost, and, when appropriate, walk through a
repair with step-by-step photo verification.

The product is not a replacement for a qualified mechanic. Its core value is
guided triage, safer DIY decision-making, and practical repair assistance for
common bicycle maintenance tasks.

## 2. Product Goals

- Help non-expert riders describe and diagnose common bike issues.
- Reuse saved bike profile, skill level, and repair history instead of
  re-asking static questions.
- Separate diagnosis, planning, and repair execution into clear workflow
  phases.
- Use photos as first-class diagnostic and verification inputs.
- Escalate to shop referral when the repair is unsafe, unclear, or outside
  reasonable DIY scope.
- Keep infrastructure simple and low cost for a solo, low-traffic project.

## 3. MVP Scope

The MVP supports turn-based text and photo interaction for common mechanical
bike issues such as drivetrain noise, shifting problems, flats, simple brake
adjustments, chain wear, loose parts, and basic maintenance checks.

The MVP includes:

- Android app written in Kotlin.
- Google ADK Python backend embedded inside a custom FastAPI service.
- Phase-based agent orchestration.
- Bike profiles, repair history, phase reports, and session records stored in
  Postgres.
- Preset tool catalog used for planning and cost estimation. User-owned tool
  inventory is deferred beyond V1.
- Uploaded media stored in Google Cloud Storage through ADK
  `GcsArtifactService`.
- Server-sent events for streaming assistant responses to the Android client.
- Search-backed or stubbed price lookup behind a thin backend interface.
- Stubbed repair-reference lookup that can later become pgvector-backed RAG.

The MVP excludes:

- Live voice or low-latency bidirectional streaming.
- Full video diagnosis as a required path.
- Running Gemini models locally.
- Fully offline repair guidance.
- Complex repairs involving e-bike batteries, suspension internals, structural
  frame damage, or advanced hydraulic service.

## 4. Guiding Design Principles

### 4.1 Phase Boundaries Are Context Boundaries

The assistant is implemented as a sequential phase pipeline:

1. Diagnostic phase
2. Planning and cost phase
3. Guided repair execution phase

Each phase gets a fresh ADK session/invocation. The next phase receives only
the prior phase's structured report, plus durable user/bike data needed for
that phase. Full transcripts and media are archived, not replayed into every
future phase.

**Clarification: phase vs. session vs. turn.** A phase is not a single
request/response. Within a phase the user and assistant typically exchange
several turns — for example, the assistant asks a diagnostic question, the
user replies and uploads a photo, the assistant asks a follow-up. All turns
within one phase share a single ADK session/invocation, so the agent keeps
full conversational context for the duration of that phase. When the phase
produces its structured report, that ADK session is closed and archived. The
next phase opens a brand-new ADK session and is seeded only with the
structured report and durable user/bike data — never with the raw turns from
the prior session. The API contract reflects this: one `repair session`
resource spans all phases, multiple `turns` can occur within a phase, and
`phase.transitioned` events mark the session-boundary moments where context is
intentionally discarded.

### 4.2 Structured Reports Over Full Transcripts

Every phase ends by producing a compact structured artifact. These artifacts
are the canonical handoff format between phases and are also persisted for
future audit, repair history, and debugging.

### 4.3 Safety Is Always Active

Safety escalation is not a single step. It can occur during diagnosis,
planning, or repair execution. The system should bias toward shop referral
when confidence is low and the consequence of a mistake is high.

### 4.4 Keep Infrastructure Boring

Use one custom backend service, one relational database, and simple provider
interfaces for replaceable external services. Avoid splitting into multiple
deployables until traffic or team size demands it.

## 5. System Architecture

At a high level:

```text
Android app
  |
  | HTTPS REST + SSE
  v
Custom FastAPI backend
  |
  | imports ADK Runner as a library
  v
ADK phase agents
  |
  +-- Postgres: app data, ADK sessions, reports, history
  +-- GCS: uploaded media/artifacts
  +-- Gemini API: model inference
  +-- Search provider: price lookup, behind an internal interface
```

The backend does not run `adk api_server` as the application server. Instead,
it owns authentication, app data routes, media upload, and agent streaming in a
single FastAPI application.

**Vendor concentration.** This design deliberately concentrates on Google's
stack: ADK for orchestration, Gemini for inference, and GCS for artifact
storage via ADK's built-in `GcsArtifactService`. This is consistent with the
"keep infrastructure boring" principle and is the right trade for a solo
project, but it is a single-vendor dependency across the entire AI/storage
path — there is currently no fallback model provider, no alternate artifact
store, and no abstraction layer between ADK and the orchestration code. This
is an accepted risk for V1, not an oversight; revisit if priorities shift
toward portability.

## 6. Backend Design

### 6.1 Runtime

- Language: Python
- Framework: FastAPI
- Agent framework: Google ADK Python
- Agent execution: custom orchestration layer using ADK `Runner`
- Session storage: ADK `DatabaseSessionService`
- Artifact storage: ADK `GcsArtifactService`
- Primary database: self-hosted Postgres

`DatabaseSessionService` should use the same Postgres instance as the app's
own tables. This keeps persistence operationally simple while supporting
proper session isolation and concurrent access behavior.

### 6.2 Orchestration

The backend owns phase transitions. It starts a fresh ADK session/invocation
for each phase and seeds that phase with:

- The current user's identity and permissions.
- The active bike profile.
- Relevant durable state, such as repair history.
- The immediately preceding structured phase report.

The orchestration layer should not rely on ADK `SequentialAgent` chaining for
the main product flow, because the product deliberately avoids sharing a single
long-lived context across all phases.

### 6.3 Agent Tools

Agents access app data and external services through explicit backend tools.
Important tool interfaces include:

- `get_bike_profile`
- `lookup_tool_catalog`
- `lookup_repair_history`
- `save_diagnostic_report`
- `save_plan_report`
- `save_repair_progress`
- `append_repair_history`
- `price_lookup`
- `lookup_repair_reference`

`lookup_tool_catalog`, `price_lookup`, and `lookup_repair_reference` are
intentionally provider-neutral interfaces. The initial implementation can be
simple or stubbed, but the agent contract should exist from the start.

V1 does not maintain a per-user owned-tool inventory. During the planning and
cost phase, the agent generates the tools required for the repair and maps each
tool to a preset catalog item when possible. Unmatched tools stay as
plan-scoped generated requirements with lower confidence.

## 7. Android Client Design

The Android app is the primary user experience.

Tech stack:

- Kotlin
- Jetpack Compose
- ViewModel-based screen state
- Local persistence via Room or DataStore where useful
- HTTPS REST calls for normal actions
- SSE client for streamed agent responses

Primary screens:

- Bike list and bike profile editor
- New repair intake
- Diagnostic chat
- Plan and cost review
- Guided repair execution
- Repair history

The client should treat the backend as the source of truth for phase state.
Local persistence is used for caching, drafts, and ergonomic offline-tolerant
behavior, not for authoritative repair records.

## 8. API Surface

The exact API contract will be specified separately. At a high level, the
backend should expose:

- Authentication routes
- Bike profile CRUD
- Repair history reads
- Repair session creation and lookup
- Media upload and artifact reference creation
- Streaming chat/agent route using SSE
- Repair decision route for DIY vs shop handoff
- Repair completion route

The main streaming route should use server-sent events rather than WebSocket.
The product is turn-based: the user submits text/photos with normal HTTP
requests, and the server streams the assistant response back. 

## 9. Persistence Model

Postgres is the system of record for app data and ADK session storage.

Core application entities:

- User
- Bike profile
- Preset tool catalog item
- Repair session
- Diagnostic report
- Plan report
- Execution step/progress
- Repair history entry
- Artifact reference
- Price lookup result
- Repair reference lookup result

GCS stores large media artifacts such as photos and future video uploads.
Postgres stores metadata and references to those artifacts.

ADK Memory is not used for bike profile or repair history. Those domains
require precise reads and transactional writes, so they belong in relational
tables exposed to agents through tools.

The preset tool catalog may live in a database table, seed file, or admin-only
data source. It is not user-owned state in V1, and the public app API does not
need user tool CRUD.

At minimum, preset tool catalog entries should include an ID, display name,
category, aliases or matching terms, pricing metadata, and any constraints
that affect planning.

## 10. Phase Reports

### 10.1 Diagnostic Report

Captures the best current diagnosis, alternate hypotheses, evidence summary,
confidence, safety flags, key media references, and the diagnostic session ID.

### 10.2 Plan Report

Captures parts needed, plan-scoped tools needed, catalog matches or generated
tool requirements, estimated DIY cost, estimated shop cost, rough time
estimate, safety concerns, and the user's DIY/shop decision.

### 10.3 Execution Report

Captures completed steps, verification checkpoints, unresolved concerns, final
repair summary, tools used when relevant, and repair-history updates.

The report schemas should be versioned early, even if the first versions are
simple.

## 11. Safety Policy

The assistant should recommend shop referral for high-risk cases, including:

- Suspected frame or fork damage
- Carbon component damage
- Brake failures that cannot be confidently diagnosed
- E-bike battery, motor, or wiring damage
- Suspension internals
- Stripped or damaged safety-critical bolts
- Missing or uncertain torque specifications for safety-critical parts
- Any case where photo evidence contradicts the working diagnosis

The assistant may continue with general education after a shop referral, but it
should not provide step-by-step repair instructions for repairs it has deemed
unsafe for DIY.

### 11.1 Severity and Decision Enforcement

Safety flags carry a `severity` (`info`, `caution`, `warning`, `blocking`) and
a `blocks_repair_instructions` flag. Severity is not advisory text only — it
has a server-enforced effect on what the user is allowed to do next:

- **`blocking`**: the backend rejects `decision: diy` outright (HTTP 422),
  regardless of `acknowledged_safety_flags`. Only `shop` or `not_now` are
  accepted. The execution phase will not produce step-by-step instructions
  while a blocking flag from planning remains active.
- **`warning`**: `decision: diy` is allowed only if the request sets
  `acknowledged_safety_flags: true`. The assistant should still bias its own
  recommendation toward shop referral, but the user retains the final call.
- **`caution` / `info`**: advisory only. No effect on what decisions the API
  accepts.

This turns "safety is always active" from a prompting convention into an
invariant the backend enforces independent of model output quality.

## 12. RAG and Repair References

RAG is deferred, but the product should not be designed in a way that blocks it.

The execution phase should call `lookup_repair_reference(component,
bike_model)` from the start. The initial implementation can return a small
hand-curated result or "no reference available." Later, the same interface can
be backed by a corpus of manuals, torque tables, and service documents indexed
in Postgres with `pgvector`.

Torque specs and manufacturer-specific instructions should never be invented.
If no reliable source is available, the assistant should say so and lower
confidence or refer the user to a shop/manual.

## 13. Pricing

Parts and tool pricing should be treated as estimates, not quotes. The backend
should expose a `price_lookup(item_name, item_type)` interface and hide the
provider choice from agent code.

The first implementation may use:

- A stubbed/manual table for common parts.
- A lightweight search provider.
- Cached prior lookup results.

Because search provider pricing and terms can change, provider-specific SDKs
should stay behind the backend interface.

Cached price lookups should carry a staleness window (for example, 14–30 days
depending on item type) after which the backend re-queries rather than reusing
a stored `price_lookup_result`. The exact window is a follow-up detail, but the
persistence model should store a timestamp on each cached result so this can
be enforced later without a schema change.

## 14. Deployment

Recommended v1 deployment:

- One backend container running FastAPI and ADK orchestration.
- One Postgres instance.
- One GCS bucket for artifacts.
- Android app distributed separately.
- Gemini inference through Google's model endpoint.

"Self-hosted" means the application server, database, and app data are owned
by the project. It does not mean Gemini model inference runs locally, and it
does not remove the GCS dependency for artifact storage in the recommended
v1 design.

The single-container design assumes turn volume low enough that synchronous
Gemini calls inside the FastAPI process don't starve SSE delivery for other
concurrent sessions. This is fine at expected V1 traffic. If p95 turn latency
degrades or concurrent active sessions grow past what one process comfortably
serves, the next step is a background task queue for agent execution, with the
FastAPI process only handling HTTP/SSE — an addition, not a re-architecture.
No need to build this prematurely.

## 15. Repository Layout

Monorepo layout:

```text
apps/
  android/
  api/
packages/
  shared-schemas/
infra/
docs/
  specs/
evals/
```

The backend and Android app should share schema definitions where practical,
either through generated OpenAPI clients, shared JSON Schema, or another
explicit contract-generation step.

## 16. Evaluation

The project should maintain agent behavior evaluations separate from normal
unit tests.

Core evaluation scenarios should cover:

- Correctly asking for missing diagnostic evidence.
- Escalating safety-critical repairs to a shop.
- Avoiding invented torque specs.
- Producing complete parts/tool plans.
- Producing catalog-grounded tool requirements while allowing generated
  plan-scoped tools when no catalog match exists.
- Stopping execution when verification photos reveal unexpected damage.
- Producing clean structured reports between phases.

Normal backend tests should verify API behavior, database logic, tool contracts,
and schema validation. They should not assert on exact LLM response wording.

## 17. Open Follow-Up Specs

This document is intentionally high-level. Follow-up specs should define:

- API contract and SSE event schema
- Database schema
- Android screen architecture
- Agent prompts and phase report schemas
- Safety policy details
- Evaluation dataset and grading criteria
- Deployment and local development setup