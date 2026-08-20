# BikeDoc Guided Inspection Sessions Spec

Status: Canonical v1.0
Last updated: 2026-08-20

This document is the canonical product, backend, agent, and Android behavior
specification for guided bike inspection sessions. It defines how BikeDoc
conducts a general, stationary inspection through conversation, photos, and
safe user-performed checks; persists inspection coverage and findings; and
hands actionable findings into the existing repair workflow.

`docs/specs/openapi.yaml` remains the canonical public HTTP contract. The
session, artifact, report, and enum changes required by this feature must be
added to that contract before they are exposed publicly. Within the inspection
scope, this document is authoritative for intended behavior.

## References

- Product design: `docs/specs/bike-doc.md`
- Public HTTP contract: `docs/specs/openapi.yaml`
- Backend organization: `docs/specs/apps/api.md`
- Diagnostic workflow: `docs/specs/apps/api-diagnostic.md`
- Event replay and SSE semantics:
  `docs/specs/apps/api-events-diagnostic.md`
- Artifact behavior: `docs/specs/apps/api-artifacts-diagnostic.md`
- Diagnostic safety behavior: `docs/specs/apps/safety-diagnostic.md`
- Diagnostic ADK architecture:
  `apps/api/src/bike_doc_api/adk/ARCHITECTURE.md`
- Android behavior: `docs/specs/android/mvp-spec.md`

## Normative Language

The terms **must**, **must not**, **should**, **should not**, and **may** are
normative. “Must” and “must not” define required behavior. “Should” and
“should not” define the expected default unless a later canonical spec records
a justified exception.

## 1. Purpose

BikeDoc must let a user assess the general condition of a known bike without
requiring a pre-existing symptom or complaint. The experience should feel like
the existing diagnostic chat: the user exchanges turns with an agent, may
attach photos, receives streamed responses, can leave and resume, and receives
a structured result.

An inspection is not a broad diagnosis. It surveys multiple bike systems,
records what was and was not assessed, identifies evidence-backed findings,
and recommends an appropriate next action. An actionable finding may seed a
separate repair session for focused diagnosis, planning, and execution.

The V1 product target is a guided, stationary, approximately 10–15 minute
full-bike inspection. The user may skip checks that are unavailable,
uncomfortable, or unsafe to perform.

## 2. Scope

This spec covers:

- creation, discovery, resumption, and completion of inspection sessions
- a versioned, server-owned inspection checklist
- conversational text, choice, confirmation, and photo input
- durable inspection progress and evidence-backed check results
- inspection safety escalation
- a single ADK inspection agent and its tool catalog
- an `inspection_report.v1` structured report
- durable REST-turn and SSE replay/reconnect semantics
- inspection photo upload, use, and later handoff
- Android inspection chat, progress, report, and repair handoff behavior
- deterministic backend tests, model evaluations, telemetry, and rollout order

## 3. Non-Goals

V1 does not provide:

- a professional, legal, certified, or comprehensive shop inspection
- a guarantee that the bike is safe to ride
- post-crash structural assessment as a distinct specialized workflow
- root-cause diagnosis for every inspection finding
- repair planning, pricing, part compatibility, or step-by-step repair guidance
  inside the inspection workflow
- manufacturer-specific procedures, invented torque values, or unsupported
  measurements
- riding tests or checks that require the user to ride a suspect bike
- autonomous inspection without user input
- multiple specialist agents, an ADK workflow graph, or one agent per bike
  system
- web search, RAG, Memory Bank, code execution, or external shop/manual lookup
- a tool for every checklist item or bike system
- offline-authoritative inspection state on Android

## 4. Canonical Language

### Bike Session

A **bike session** is the durable product record for one guided workflow on one
owned bike. A bike session has a workflow, phase, status, safety state, turns,
events, artifacts, and reports.

### Repair Session

A **repair session** is a bike session whose workflow addresses one complaint
cluster through diagnostic, planning, and execution phases. Related symptoms
may share a repair session; unrelated concerns require separate repair
sessions.

### Inspection Session

An **inspection session** is a bike session whose workflow surveys the bike's
general condition without requiring an initial complaint. It contains one
inspection phase and ends with an inspection report.

### Inspection Checklist

An **inspection checklist** is the immutable, versioned definition of the
areas and checks applicable to an inspection session. The server, not the
agent, owns checklist identity, applicability, ordering, and completion rules.

### Inspection Check Result

An **inspection check result** is the durable, evidence-backed outcome for one
applicable checklist item. It records what was observed, how it was assessed,
what artifacts support it, and any limitation or finding.

### Inspection Coverage

**Inspection coverage** is the complete account of applicable checklist areas
and whether each was assessed, skipped, unavailable, or not applicable. It
must not treat missing evidence as a successful check.

### Inspection Finding

An **inspection finding** is an evidence-backed observation that may require
monitoring, maintenance, diagnosis, or shop assessment. A finding is not a
root-cause diagnosis.

### Finding Handoff

A **finding handoff** is structured provenance that seeds a new repair session
from one inspection finding. It carries the finding identity and approved
evidence references, not the full inspection transcript.

## 5. Guiding Decisions

### 5.1 Inspection Is a Separate Workflow

Inspection must not be implemented as a diagnostic prompt mode or as an
ordinary diagnostic phase. Diagnosis begins with a complaint cluster and seeks
a supported causal explanation; inspection surveys multiple systems and may
produce zero, one, or several unrelated findings.

An inspection may create zero or more repair sessions. Related findings may be
grouped only when they form one coherent complaint cluster.

### 5.2 Conversation Infrastructure Is Shared

Inspection must reuse the existing product-owned turn, event, replay, SSE,
artifact, ownership, idempotency, and error semantics. The system must not
create a second chat transport stack or inspection-specific SSE protocol.

Workflow-specific behavior must sit behind a workflow-dispatch seam. The
diagnostic and inspection adapters are the first two real adapters at that
seam. Callers submit a turn to a bike session; they do not select an ADK agent
or background executor directly.

### 5.3 Progress Is Durable Product State

The inspection checklist version, applicability, check results, findings,
current input request, safety state, progress, and report are app-owned durable
state. They must not exist only in an ADK session, prompt, transcript, Android
cache, or process memory.

The ADK session provides conversational continuity within the inspection
phase. PostgreSQL remains authoritative for inspection progress and completion.

### 5.4 One Agent, Four Tools

V1 uses one normal ADK `Agent`. Deterministic checklist behavior belongs in
backend modules and tool implementations, not a `SequentialAgent`,
`LoopAgent`, workflow graph, or multi-agent hierarchy.

The model-visible tool catalog contains exactly these tools:

1. `record_inspection_results`
2. `request_inspection_input`
3. `raise_safety_flag`
4. `complete_inspection`

Read-only context that the server already knows must be seeded into the agent
invocation rather than exposed through redundant read tools.

### 5.5 Findings Are Not Diagnoses

The inspection agent may report visible wear, damage, looseness, contamination,
corrosion, misalignment, abnormal operation, or another supported condition.
It must not present an unsupported cause as fact. Findings that need causal
investigation should recommend `start_diagnostic`.

### 5.6 No Positive Safety Certification

The system may say that no issue was observed in the assessed evidence. It must
not declare the bike definitively safe, certified, or professionally inspected.
Skipped, unassessable, poorly visible, and non-applicable checks must remain
explicit in coverage and limitations.

## 6. Session and State Model

### 6.1 Workflow and Phase

The shared bike-session model must expose:

```text
workflow: repair | inspection
```

Repair workflow phases remain:

```text
diagnostic -> planning -> execution -> completed
```

Inspection workflow phases are:

```text
inspection -> completed
```

The server must reject invalid workflow/phase combinations.

### 6.2 Inspection Statuses

Inspection uses the existing shared status vocabulary:

- `created`: session exists but no inspection turn is running
- `running`: a turn is being processed
- `awaiting_user`: a persisted input request is waiting for a response
- `blocked_safety`: a blocking safety concern is active
- `completed`: an inspection report was persisted successfully
- `failed`: processing ended in a non-recoverable failure
- `cancelled`: the user or server cancelled the session

`awaiting_decision` is not used by the inspection workflow.

A blocked inspection may continue with safe visual or stationary checks. It
must not request riding or another check made unsafe by the active concern. A
blocked inspection may still complete with an `unsafe_to_ride` or
`shop_assessment_recommended` report outcome.

### 6.3 Inspection Progress

The public session projection should include:

```text
inspection_progress:
  completed_checks
  total_applicable_checks
  current_section
```

Progress must be derived from durable check results, not incremented by the
agent. `completed_checks` includes assessed, skipped, and unable-to-assess
terminal results. It excludes checks that remain pending.

### 6.4 Checklist Version

Session creation must snapshot one checklist version, initially:

```text
general_inspection.v1
```

Deploying a new checklist version must affect only new inspection sessions.
An existing inspection must resume with its snapshotted version.

## 7. V1 Checklist Shape

The checklist should produce approximately 5–8 conversational exchanges while
representing roughly 10–15 internal check IDs. Before implementation begins,
this document must contain the normative `general_inspection.v1` checklist
manifest. Each manifest entry must define its stable check ID, section,
assessed condition, applicability rule, acceptable evidence sources,
safety-critical classification, and recommended ordering or interaction group.
Implementations must not infer or independently invent that data.

Conversational prompt wording remains implementation-owned and may evolve
without creating a new checklist version, provided the meaning, evidence
requirements, and safety constraints of the manifest entries do not change.
Checklist IDs and manifest rules are versioned data rather than public enum
values.

V1 must cover these sections:

1. Whole-bike overview and visible frame/fork condition
2. Wheels and tires
3. Front and rear braking systems
4. Steering, cockpit, saddle, and obvious looseness
5. Drivetrain
6. Suspension, when applicable
7. Electric-assist system, when applicable

Applicability must be derived deterministically from checklist policy and the
resolved bike profile. Uncertain profile data must not silently remove a
safety-relevant check; the check should remain applicable or be recorded as
unable to assess.

### 7.1 Check Result Status

Every applicable check must end in exactly one status:

```text
no_issue_observed
attention_needed
unsafe_condition
unable_to_assess
skipped
```

Checklist entries determined not to apply use `not_applicable` in coverage but
do not count toward `total_applicable_checks`.

`no_issue_observed` means that the available evidence did not reveal a concern
within the limits of that check. It must not be rendered as “passed” or “safe.”

### 7.2 Evidence Sources

An inspection check result may cite:

```text
user_report
photo
functional_check
measurement
repair_history
other
```

Photo evidence must cite approved artifact IDs. Photos cannot establish
measurement-only facts such as torque, bearing preload, chain wear percentage,
or exact pad, rotor, or rim thickness.

### 7.3 Check Ordering

The inspection-plan module owns the recommended next check. The agent must
normally follow that recommendation. It may deviate only when:

- a safety concern requires immediate clarification
- one user response supports several pending checks
- the user cannot or will not perform the recommended check
- the user reports a material concern that should be recorded promptly

## 8. Agent Invocation Context

Before every inspection-agent invocation, the orchestrator must seed strict,
server-owned context containing:

- authenticated user and inspection-session identity
- app-owned inspection phase-session identity
- user skill level when known
- resolved bike profile and relevant uncertainty/conflicts
- checklist version
- applicable checks and their current results
- current recommended check or section
- inspection progress
- active safety flags
- current turn ID and approved artifact IDs
- current-turn normalized images and visual observation projections
- current-turn artifact processing statuses
- prior structured check results and artifact references

Historical image bytes must not be blindly replayed. Prior check results carry
their structured observations, limitations, and artifact IDs. Storage paths,
signed URLs, provider objects, raw extraction scores, prompts, raw ADK events,
and opaque ADK session IDs must not be representable in the context exposed to
the model or public contract.

The agent does not need `get_bike_profile`, `get_inspection_progress`, or
`list_inspection_artifacts` tools because this context is supplied by the
server on each invocation.

## 9. Agent Conversation Flow

### 9.1 Start

After creating an inspection session, the Android client presents a “Begin
inspection” action. Activating it submits an ordinary idempotent user turn.
No separate server-initiated conversation protocol is required.

The agent's first response must briefly explain:

- the inspection is guided, visual, and stationary
- it is not a professional safety certification
- the user may skip any check they cannot perform safely
- the user must not ride the bike during the inspection

The first input request should normally ask for useful whole-bike overview
photos, such as drive-side and non-drive-side views.

### 9.2 Normal Turn

For each accepted user turn, the agent must:

1. Review the server-seeded plan, current check, progress, and safety state.
2. Interpret the user's text, choices, safe functional-check result, and
   current-turn images.
3. Decide whether the evidence supports one or more check results.
4. Call `record_inspection_results` for supported results.
5. Call `raise_safety_flag` immediately for a material safety concern.
6. Use the returned next-check projection to request one coherent next input,
   or call `complete_inspection` when no required checks remain.

One user response may record multiple check results. The agent should minimize
repetitive turns without treating one broad photo as proof of conditions it
does not show.

### 9.3 Insufficient Evidence

When evidence is missing, ambiguous, contradictory, poorly visible, or unable
to support a requested condition, the agent must not create a positive result.
It should request one safe, concrete, high-value follow-up that names the view,
action, or answer needed and the question it will help resolve.

If the user declines, cannot perform the check, or the check cannot be assessed
remotely, the agent must record `skipped` or `unable_to_assess` rather than
leaving the checklist implicitly complete.

### 9.4 Safety Interruption

When a material hazard is observed or reported, the agent must:

1. State the factual concern without overstating its cause.
2. Raise the safety flag before continuing normal checklist progression.
3. Tell the user not to ride or perform a check made risky by the concern.
4. Continue only with safe visual or stationary checks, or offer to complete
   with a shop-assessment outcome.

### 9.5 Completion

The agent may complete only by successfully calling `complete_inspection`.
Normal text saying the inspection is finished has no state-changing effect.

If completion validation reports missing checks, the agent must request the
next missing input or explicitly resolve those checks as skipped or unable to
assess. Successful completion persists the report, transitions the session,
and ends the conversational phase.

## 10. Tool Contracts

All model-visible tool functions must derive user, session, phase-session, and
turn identity from strict server-owned tool context. The model must not supply
or replace those identifiers. Tools return the existing common success/error
envelope and normalize known backend failures to stable, non-leaky error codes.

### 10.1 `record_inspection_results`

Purpose: atomically validate and persist one or more supported check results,
recalculate progress, and return the next recommended check.

Model-visible input:

```text
results[]:
  check_id
  status
  observation
  evidence_sources[]
  artifact_ids[]
  confidence
  finding?          # optional proposed finding for this result
```

Each proposed finding may include:

```text
component
condition
urgency
recommended_next_action
```

Allowed confidence values are `low`, `medium`, and `high`.

The tool must:

- reject unknown or non-applicable check IDs
- reject unsupported status values
- validate that cited artifacts are owned, available, inspection-purpose
  images associated with this bike session, and allowed in the current context
- reject image-only claims that require a measurement or functional check
- preserve evidence provenance when a later turn refines a check result
- be idempotent for an identical `(turn_id, check_id, canonical payload)`
- reject conflicting repeated writes for the same check in one turn
- validate the batch atomically
- derive progress and next-check selection in backend code

The successful response must include:

```text
recorded_check_ids[]
progress:
  completed_checks
  total_applicable_checks
next_check?:
  id
  section
  title
  accepted_inputs[]
```

Returning `next_check` from this tool eliminates the need for a separate
progress-read tool.

### 10.2 `request_inspection_input`

Purpose: persist the next user-input affordance and emit `input.requested`.

Model-visible input:

```text
check_ids[]
type
prompt
required
accepted_media_types[]
choices[]
min_artifacts?
max_artifacts?
```

Supported input types are:

```text
text
photo
multiple_choice
confirmation
none
```

The tool must reuse the existing input-request persistence and event semantics.
It must additionally verify that the referenced checks are applicable and
pending or legitimately need clarification.

The agent should normally request one coherent user action. Common choices may
include “Looks normal,” “I found something,” “I cannot check this,” and “Skip
this check.” User-facing wording remains contextual rather than fixed by this
spec.

### 10.3 `raise_safety_flag`

Purpose: reuse server-owned safety validation, persistence, session-state
derivation, and `safety.escalated` events for inspection concerns.

Model-visible input retains the existing safety-flag shape:

```text
code
severity
phase
message
blocks_repair_instructions
```

Inspection flags must use `phase: inspection`. The existing safety codes and
severity rules should be reused where applicable, including frame/fork damage,
brake failure, carbon damage, e-bike electrical concern, suspension concern,
safety-critical fastener damage, insufficient evidence, contradictory
evidence, and unsafe riding condition.

Safety remains authoritative backend behavior. Prompts and tools may propose a
flag but may not bypass validation, deduplication, highest-severity state
derivation, or blocking rules.

### 10.4 `complete_inspection`

Purpose: validate readiness, derive canonical report coverage, persist one
inspection report, reconcile safety, transition the session, and emit report
and transition events.

Model-visible input:

```text
summary
findings[]
limitations[]
```

Each proposed finding must identify its supporting check IDs and supply the
component, observation, condition, urgency, confidence, and recommended next
action. The backend owns stable `finding_id` generation and canonical artifact
references; the model must not invent either.

The agent must not resend checklist coverage or all check results. The backend
derives those from durable inspection state.

The tool must:

- verify every applicable check has a terminal result
- verify proposed findings are supported by stored check results
- derive the canonical outcome and ride guidance from durable check results,
  findings, safety flags, and material coverage gaps; the model must not select
  either value
- derive coverage and evidence references from stored state
- reconcile all active safety flags
- reject a non-blocking outcome when blocking evidence remains
- persist at most one report for the inspection phase session
- make exact retries idempotent and return the existing report
- close the inspection phase session and transition the bike session only
  after report persistence succeeds

Validation failure must return the missing or invalid check IDs in bounded,
non-sensitive details so the agent can recover.

## 11. Tools Explicitly Excluded from V1

The inspection agent must not receive these tools in V1:

- `get_bike_profile`
- `get_inspection_progress`
- `list_inspection_artifacts`
- `lookup_repair_history`
- web or manual search
- repair-reference lookup
- pricing or compatibility lookup
- repair planning or execution tools
- generic database or OpenAPI toolsets

`lookup_repair_history` may be added later only when durable repair history is
implemented and evaluations demonstrate that it materially improves inspection
quality. Tool-catalog growth must be justified by a concrete capability that
cannot be supplied safely in seeded context.

## 12. Inspection Report V1

The report envelope must use:

```text
type: inspection
phase: inspection
schema_version: inspection_report.v1
```

The payload must contain:

```text
InspectionReportV1
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

`inspection_session_id` is the public bike-session ID for the inspection. It
is not an ADK session ID or the opaque internal inspection phase-session ID.

### 12.1 Scope

V1 scope is:

```text
guided_stationary_full_bike
```

### 12.2 Outcomes

Allowed outcomes are:

```text
no_actionable_findings
maintenance_recommended
diagnostic_follow_up_recommended
shop_assessment_recommended
unsafe_to_ride
incomplete
```

The backend must derive the outcome deterministically from durable inspection
state. The agent must not select or override it. The derivation and precedence
rules are part of the checklist/report policy and must be specified before
implementation.

### 12.3 Ride Guidance

Allowed ride-guidance values are:

```text
no_known_blocking_issue
use_caution
do_not_ride
professional_assessment_required
not_assessed
```

`no_known_blocking_issue` is not a safety certification. Reports and Android
copy must not render it as “safe to ride.”

The backend must derive ride guidance deterministically from durable inspection
state. The agent must not select or override it. Identical check results,
findings, safety flags, and material coverage gaps must produce the same outcome
and ride guidance.

### 12.4 Coverage

Each coverage entry must include:

```text
area
status
evidence_summary
check_ids[]
artifact_ids[]
```

Coverage status must distinguish assessed, partially assessed, skipped,
unable-to-assess, and not-applicable areas.

### 12.5 Findings

Each finding must include:

```text
finding_id
area
component
observation
condition
urgency
confidence
supporting_check_ids[]
artifact_ids[]
recommended_next_action
```

Allowed urgency values are:

```text
routine
soon
before_next_ride
immediate
```

Allowed next actions are:

```text
monitor
routine_maintenance
start_diagnostic
stop_riding
shop_assessment
```

Every finding must reference at least one stored inspection check result.
Artifact IDs are required only when image evidence supports the finding.

### 12.6 Limitations

Limitations must include every skipped, unavailable, poorly visible,
contradictory, or otherwise materially incomplete area that affects how the
report should be interpreted.

## 13. Persistence Requirements

The durable model must support:

- a workflow discriminator on the shared bike-session record
- `inspection` in active phase constraints
- a snapshotted checklist version on the inspection phase session or an
  inspection-specific state record
- inspection progress on the public session projection
- durable check results keyed by inspection session and check ID
- evidence sources, artifact IDs, confidence, observation, status, and optional
  proposed finding on each check result
- one inspection report associated with the inspection phase session
- `inspection` report type and `inspection_report.v1` schema version
- an inspection report reference in the session's latest-report projection
- owner-scoped indexes for inspection discovery and resumption
- idempotency constraints for session creation, turns, uploads, check-result
  writes, and report completion

V1 may keep one current row per `(inspection_session_id, check_id)`. A later
turn may refine a result, but the update must retain prior evidence references
and must not silently replace a more severe safety result with a less severe
one. Full public revision history is not required in V1.

Database migrations are authoritative. Updating persistence models without a
migration does not implement this feature.

## 14. Public HTTP Contract

### 14.1 Canonical Session Paths

The inspection-capable public contract must use workflow-neutral paths:

```text
POST /v1/sessions
GET  /v1/sessions
GET  /v1/sessions/{sessionId}
POST /v1/sessions/{sessionId}/turns
GET  /v1/sessions/{sessionId}/events
GET  /v1/sessions/{sessionId}/reports
GET  /v1/sessions/{sessionId}/reports/{reportId}
```

Existing `/v1/repair-sessions` paths may remain as compatibility aliases for
the repair workflow during migration. The workflow-neutral paths must be
available before inspection is released publicly. Public callers must not
choose an ADK agent, prompt, model, or background executor.

### 14.2 Session Creation

Inspection creation uses:

```json
{
  "bike_id": "bike_123",
  "workflow": "inspection",
  "client_session_id": "android-inspection-001"
}
```

The response must include `workflow: inspection`, `phase: inspection`,
`status: created`, `inspection_progress`, the checklist version or a stable
inspection-state reference, `latest_reports.inspection_report_id`, and the
existing event cursor and timestamps.

Session creation must preserve the existing owner-scoped not-found and
idempotency-conflict behavior.

### 14.3 Listing and Resumption

Session listing must support filtering by bike and workflow. Inspection
sessions are resumable when `phase: inspection` and status is `created`,
`running`, `awaiting_user`, or `blocked_safety`. Terminal sessions open their
report rather than the live conversation.

### 14.4 Turns

Inspection turns use the existing `ai_turn.v1` message shape, client-turn
idempotency, artifact limits, acceptance response, and background-processing
semantics. Turn dispatch must derive the inspection workflow from persisted
session state.

### 14.5 Finding Handoff

Starting diagnosis from an inspection finding creates a new repair workflow
session with structured origin provenance:

```json
{
  "bike_id": "bike_123",
  "workflow": "repair",
  "origin": {
    "type": "inspection_finding",
    "inspection_session_id": "ses_inspection_123",
    "finding_id": "finding_front_brake_1"
  },
  "client_session_id": "android-repair-from-finding-001"
}
```

The backend must verify ownership, bike identity, finding existence, and
evidence eligibility. The diagnostic phase is seeded with the structured
finding handoff and approved evidence references, not the full inspection
transcript.

## 15. Event and SSE Contract

Inspection must preserve the existing durable event-log contract:

- persist and commit before live fan-out
- monotonically increasing sequence per bike session
- decimal public cursors
- `after` precedence over `Last-Event-ID`
- persisted replay followed by live delivery
- heartbeat and timeout behavior
- clean mobile reconnect from the latest processed event ID

V1 uses the existing event types:

```text
turn.started
assistant.delta
assistant.message.completed
input.requested
artifact.referenced
phase.report.created
phase.transitioned
safety.escalated
turn.completed
error
heartbeat
```

Inspection does not require a custom progress event in V1. Updated progress is
returned in the authoritative session snapshot carried by `turn.completed`.

Inspection-specific event payload rules are:

- `turn.started.phase` is `inspection`
- `phase.report.created.report_type` is `inspection`
- `phase.report.created.schema_version` is `inspection_report.v1`
- `phase.report.created.phase` is `inspection`
- `phase.transitioned` moves from `inspection` to `completed`
- safety events may carry inspection-phase flags

Unknown additive event types must continue to be ignored by older Android
clients while their cursors advance normally.

## 16. Artifact and Image Behavior

### 16.1 Purpose

Inspection uploads use a new artifact purpose:

```text
inspection_photo
```

Inspection evidence must not be mislabeled `diagnostic_photo`.

### 16.2 Upload and Preparation

Inspection photos reuse the diagnostic image policy and implementation:

- accepted MIME types: JPEG, PNG, and WebP
- Android conversion of unsupported HEIC/HEIF selections to JPEG
- configured upload-size limit
- owner-scoped upload idempotency
- app-owned artifact metadata
- storage-provider isolation
- safe normalization and visual-observation extraction rollout modes
- no public storage paths, bucket names, provider objects, or signed URLs

### 16.3 Cross-Session Evidence

An inspection photo may support a later repair session. Artifact bytes must
not be copied merely to move evidence between workflows.

The durable artifact model must make immutable bike-owned artifacts associable
with one or more authorized bike sessions through a session-artifact
association. If migration constraints require an intermediate implementation,
it may create a new app-owned reference to the same immutable stored object,
but ownership and provenance must remain explicit and storage bytes must not be
duplicated.

Turn acceptance must verify that every referenced artifact is owned, associated
with the active bike session, available, inspection-purpose when used for
inspection, and an accepted image type.

Automatic bike-profile inference from inspection photos is not required by
this spec and may be enabled later only under the canonical profile-inference
rules.

## 17. Backend Module Shape

### 17.1 Shared Session Conversation Module

The shared module owns session loading, turn acceptance, ownership,
idempotency, durable turn/event creation, artifact validation, event replay,
SSE formatting, and workflow dispatch. Its external interface remains the
workflow-neutral session/turn/event contract.

The diagnostic and inspection implementations are adapters selected from the
persisted session workflow. Routes must not import an inspection or diagnostic
agent directly.

### 17.2 Inspection Plan Module

The inspection-plan module is the authoritative implementation for checklist
applicability, current state, result validation, progress calculation, and
next-check selection. Its interface should remain small:

```text
load_state(session_id) -> InspectionState
record_results(session_id, turn_id, results) -> InspectionState
validate_completion(session_id) -> CompletionState
```

ADK tools adapt this interface; they do not duplicate its rules.

### 17.3 Inspection Orchestration

The inspection orchestrator processes one already-accepted turn. It prepares
current-turn visual context, seeds strict app context, invokes the inspection
runner, translates normalized runner events into product events, and finalizes
the turn safely.

The runner owns ADK adaptation and raw-event normalization. It does not persist
public events. State-mutating tools write authoritative product state once;
their runner notifications must not cause the orchestrator to repeat those
writes.

### 17.4 Report and Safety Modules

The report module validates and persists inspection reports from durable
inspection state. The safety module validates inspection flags and derives
session safety state. Neither invariant may exist only in the prompt.

## 18. Android Behavior

### 18.1 Entry and Discovery

Android must provide:

- an “Inspect a bike” entry action
- owned-bike selection
- inspection session creation
- bike-scoped inspection session discovery
- resumption of active inspection sessions
- report opening for completed inspection sessions

### 18.2 Conversation Reuse

Android must not copy the diagnostic ViewModel and screen wholesale. Shared
conversation behavior should be extracted behind a reusable module that owns:

- session loading
- SSE replay, cursor tracking, reconnect, backoff, and cancellation
- pure event reduction
- optimistic turn submission and retry
- input-request rendering
- photo preparation and upload
- common chat presentation

Workflow presentation remains thin and explicit. Diagnostic and inspection may
provide different title, subtitle, progress, completion copy, report route, and
finding actions. A `BaseChatViewModel` inheritance hierarchy is not required.

### 18.3 Inspection Chat

The inspection chat must:

- show the current section and completed/total progress
- support text, choices, confirmations, camera, and gallery input
- show per-photo upload progress and retry
- disable conflicting input while a turn is active
- reconstruct the conversation from durable events on resume
- reconcile current input and progress from the latest session snapshot
- display safety escalation prominently
- preserve the existing leave-while-streaming confirmation behavior

### 18.4 Completion and Report

After completion, the chat becomes read-only and offers the inspection report.
The report screen must show:

- summary and outcome
- non-certifying ride guidance
- coverage, including skipped and unavailable areas
- findings grouped by urgency or bike area
- limitations
- safety flags
- “Start diagnosis” for findings whose next action is `start_diagnostic`
- shop-assessment guidance for appropriate findings

Copy must not collapse `no_issue_observed` or `no_known_blocking_issue` into a
claim that the bike is safe.

## 19. Safety Requirements

Inspection safety is always active and server enforced.

The agent must not ask the user to:

- ride-test a bike with a suspected brake, steering, wheel, tire, frame, fork,
  or other safety-critical concern
- touch exposed e-bike electrical damage, a hot/swollen battery, leaking
  hydraulic fluid, sharp broken parts, or pressurized suspension internals
- disassemble safety-critical parts during inspection
- estimate or apply an unknown torque specification
- perform a check beyond the user's stated comfort or skill

The backend must ensure:

- blocking flags set `safety_state: blocked`
- blocking concerns produce `do_not_ride` or
  `professional_assessment_required` ride guidance
- an unsafe check result cannot be downgraded merely by later model text
- active safety flags appear in the final report
- completion remains possible when unsafe checks are stopped and limitations
  are recorded

## 20. Error Recovery and Idempotency

Inspection follows the existing safe error model:

- accepted turns are durable before model processing begins
- exact client-turn retries return the existing accepted turn
- recoverable processing failures emit a safe `error` event and return the
  session to a user-recoverable state
- whole-turn model execution is not retried automatically
- cancellation propagates without manufacturing a successful result
- report persistence and phase transition are atomic from the client's
  perspective
- the durable event log, not the in-process broker, is the reconnect mechanism

If an ADK session becomes unavailable after process restart, the system must
emit a recoverable failure rather than silently create a new conversation with
lost context. Durable inspection state allows a later explicit recovery design
without losing checklist progress.

## 21. Testing and Evaluation

### 21.1 Deterministic Tests

Backend unit and contract tests must cover:

- workflow/phase/status combinations
- owner-scoped creation, reads, listing, turns, reports, findings, and artifacts
- session, turn, upload, result-write, and completion idempotency
- checklist version snapshots and applicability
- progress calculation and next-check selection
- batch result validation and atomicity
- skipped, unable-to-assess, and not-applicable coverage
- safety escalation and unsafe-result downgrade prevention
- completion rejection for missing checks or unsupported findings
- report derivation from durable state
- cross-session artifact association and finding handoff
- event sequencing, persistence-before-fan-out, cursor replay, SSE reconnect,
  heartbeat, and timeout behavior
- public schema/OpenAPI alignment
- runner event normalization, tool wrappers, orchestration, and setup-failure
  recovery with fakes

Tests must not make live model, network, database, storage, or provider calls
when a deterministic adapter is sufficient.

### 21.2 Agent Evaluations

Model behavior belongs in `evals/bike-doc`, not pytest assertions over exact
response wording. Evaluation scenarios must include:

- a healthy-looking bike with explicit coverage limits
- user-declined and unable-to-perform checks
- blurry or irrelevant images requiring a targeted follow-up
- one photo that legitimately supports several checks
- material frame/fork, brake, tire, and e-bike safety findings
- a blocking finding that stops unsafe testing but still permits report
  completion
- multiple unrelated findings
- a finding that should start diagnosis rather than claim a cause
- conditional suspension and e-bike applicability
- contradictory user and image evidence
- attempted completion with missing evidence
- refusal to claim professional certification or definitive safety
- refusal to provide repair instructions, invented torque, or unsupported
  manufacturer procedures

Evaluation should assess tool trajectory, checklist coverage, evidence
calibration, safety behavior, quality of input requests, completion readiness,
and report consistency.

## 22. Telemetry and Privacy

Inspection telemetry should extend the existing privacy-safe diagnostic
patterns. Useful scalar dimensions include:

- workflow and checklist version
- turn index and phase-session duration
- applicable/completed/skipped/unable-to-assess check counts
- finding count by urgency and next action
- safety state and highest severity
- completion outcome
- report validation failure category
- visual-processing mode and bounded success/error counts

Telemetry must not include report text, observations, prompts, user messages,
image bytes, storage locations, model reasoning, or arbitrary tool payloads.
Model/content capture remains disabled unless explicitly allowed by the
existing local-only trace-content policy.

## 23. Implementation Order

Implement the vertical slice in this order:

1. Add canonical OpenAPI enums and schemas for workflow, inspection progress,
   inspection artifacts, findings, and reports.
2. Add persistence migrations for workflow state, checklist version, check
   results, reports, and artifact associations.
3. Implement and test the inspection-plan, safety, and report modules without
   an agent.
4. Generalize session creation, turn acceptance, event paths, and background
   workflow dispatch while preserving diagnostic behavior.
5. Generalize shared image preparation and artifact handling for
   `inspection_photo`.
6. Add strict inspection tool context and the four ADK tool adapters.
7. Add the single inspection agent, prompt, runner adaptation, orchestration,
   and safe background execution.
8. Extract the Android conversation module and add inspection creation, chat,
   progress, report, and finding handoff.
9. Add agent evaluations, telemetry, and rollout gates.

Each stage must keep the diagnostic workflow passing. Agent prompt behavior
must not be used as a substitute for unfinished deterministic product rules.

## 24. Deferred Extensions

The following may be specified later but are not required for V1:

- specialized pre-ride, post-crash, used-bike purchase, seasonal, or shop-style
  inspection checklists
- repair-history-aware maintenance intervals
- sourced manufacturer manuals or inspection specifications
- inspection-result revision history visible to users
- explicit issue grouping before repair-session creation
- profile inference triggered from inspection photos
- dedicated progress SSE events if turn-completion snapshots prove inadequate
- durable job queues and durable ADK session storage

These extensions must preserve the core distinction between inspection
findings and diagnostic conclusions, and must not weaken explicit coverage or
safety limitations.
