# BikeDoc Guided Inspection Sessions Spec

Status: Canonical v1.1
Last updated: 2026-11-12

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
- Durable background execution: `docs/specs/apps/durable-job-queue.md`
- Current backend architecture: `apps/api/ARCHITECTURE.md`
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
- queue-only turn execution with bounded recovery and stale-write protection
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
- a second job runtime, stream, consumer, or worker deployment solely for
  inspection turns
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

An inspection may create zero or more repair sessions. In V1, each repair
session handoff originates from exactly one inspection finding. A single
finding may already span multiple supporting check IDs when those observations
form one coherent complaint cluster. Selecting or grouping several durable
findings into one handoff is deferred beyond V1.

### 5.2 Conversation Infrastructure Is Shared

Inspection must reuse the existing product-owned turn, event, replay, SSE,
artifact, ownership, idempotency, durable-job, execution-fencing, and error
semantics. The system must not create a second chat transport stack,
inspection-specific SSE protocol, or inspection-specific job runtime.

Workflow-specific behavior must sit behind two small dispatch seams:

- persisted `workflow` selects the product workflow adapter for session and
  turn behavior; and
- the registered background-job kind selects the worker handler for an
  accepted turn.

Repair and inspection are the first two real adapters at the workflow seam.
Callers submit a turn to a bike session; they do not select an ADK agent, job
kind, worker, model, or executor directly. PostgreSQL is authoritative for
accepted work and product state. JetStream carries execution notifications
only.

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

### 5.7 One Canonical Bike-Session Aggregate

Repair and inspection use one canonical `BikeSession` aggregate because they
share ownership, bike association, turns, input requests, events, artifacts,
phase sessions, reports, safety state, idempotency, and background execution.
The canonical persistence and public language is bike-session language; repair
is one workflow rather than the name of the shared infrastructure.

Workflow-specific state must remain in workflow-owned records. Inspection
checklist state, check results, findings, and report derivation must not become
nullable repair-or-inspection columns on `bike_sessions`. Likewise, the shared
module must not encode inspection checklist rules or repair diagnostic rules.

At the V1 cutover decision, no production data or released compatibility
contract exists for the current repair-named persistence and routes. V1
therefore performs one coordinated cutover to `bike_sessions`, `ses_` public
IDs, and `/v1/sessions`; it does not add dual reads, dual writes, compatibility
aliases, or a data backfill for the development-only repair-named schema. The
migration must still be explicit, reviewable, and deterministic.

## 6. Session and State Model

### 6.1 Workflow and Phase

The shared bike-session model must expose:

```text
workflow: repair | inspection
```

V1 must make this workflow-neutral model canonical throughout persistence and
shared application code. The canonical table and model names are
`bike_sessions` and `BikeSession`; all workflows use public IDs with the
generic `ses_` prefix. Related shared records use bike-session language,
including `bike_phase_sessions`, `bike_session_turns`, and
`bike_session_events`. Repair-specific and inspection-specific modules may use
their workflow terms internally, but shared modules, foreign keys, schemas,
and repository interfaces must not retain repair-only naming.

The cutover migration may replace the existing development-only
`repair_sessions`, `repair_phase_sessions`, `repair_turns`, and
`repair_session_events` data rather than backfill it. The application and
Android client must cut over together, and tests must prove that the existing
diagnostic workflow still behaves correctly through the generic model before
inspection producers are enabled.

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
- `running`: an accepted turn has outstanding durable work, whether queued,
  retrying before its effect boundary, or executing in a worker
- `awaiting_user`: a persisted input request is waiting for a response
- `blocked_safety`: no turn is outstanding and a blocking safety concern is
  waiting for user action
- `completed`: an inspection report was persisted successfully
- `failed`: processing ended in a non-recoverable failure
- `cancelled`: the user or server cancelled the session

`awaiting_decision` is not used by the inspection workflow.

Background-job lifecycle states are internal and must not be copied into the
public bike-session status. While a turn is outstanding, status remains
`running` even if a tool changes `safety_state` to `blocked`. When that turn
settles and more user input is possible, the derived status is
`blocked_safety` if a blocking flag remains active and otherwise
`awaiting_user`. A recoverable or terminal turn-processing failure follows the
same derivation and emits a durable safe error and `turn.completed` event.
`failed` is reserved for a session-level failure that cannot be recovered by
submitting another turn.

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

An unresolved conditional check is provisionally applicable and is included
in `total_applicable_checks`. The total may decrease when explicit evidence
resolves that check to not applicable. Once all conditional applicability is
resolved, the denominator must remain stable.

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
assessed condition, applicability rule, qualifying evidence rules,
safety-critical classification, safe manual procedure, stop conditions,
prohibited actions, expected user observations, applicable skill or profile
constraints, and recommended ordering or interaction group. Implementations
must not infer or independently invent that data.

Conversational prompt wording remains implementation-owned and may evolve
without creating a new checklist version, provided the meaning, evidence
requirements, and safety constraints of the manifest entries do not change.
Checklist IDs and manifest rules are versioned data rather than public enum
values.

The agent may conversationally paraphrase a manifest entry's manual procedure,
but it must not add a physical action that the manifest does not authorize.
When an entry's stop condition is met, the agent must stop that procedure,
record the supported result or limitation, and apply the safety policy before
continuing.

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
safety-relevant check. Each session must persist per-check applicability as
`applicable`, `not_applicable`, or `unresolved`. Known presence initializes a
conditional check as applicable, known absence initializes it as not
applicable, and uncertainty initializes it as unresolved and provisionally
applicable for progress and completion.

An unresolved conditional check may be resolved from an explicit user answer
during the inspection. The backend may accept such a resolution only for a
check declared conditional by the checklist manifest. Confirmed absence
becomes `not_applicable` coverage without creating a limitation. This
session-local resolution must not mutate the saved bike profile.

### 7.1 Manifest Identity and Grouping

`general_inspection.v1` contains these 14 checks in the following recommended
order and conversational interaction groups. Qualifying evidence in this
table is the minimum required for `no_issue_observed`; evidence supporting a
concern may be narrower when it directly establishes that concern.

| Group | Stable check ID | Assessed condition | Applicability | Safety-critical | Qualifying evidence for `no_issue_observed` |
|---|---|---|---|---|---|
| 1. Overview | `whole_bike_overview` | Overall visible configuration and obvious whole-bike concerns | All bikes | No | Targeted guided `user_report` or clear `photo` coverage |
| 1. Overview | `frame_fork_visible_condition` | Visible frame, fork, steerer, and structural condition | All bikes | Yes | Targeted guided visual `user_report` or clear `photo` coverage |
| 2. Wheels and tires | `front_tire_condition` | Visible front-tire damage, inflation concern, and gross wear | All bikes | Yes | Targeted guided visual `user_report`; `photo` and `measurement` may supplement it |
| 2. Wheels and tires | `rear_tire_condition` | Visible rear-tire damage, inflation concern, and gross wear | All bikes | Yes | Targeted guided visual `user_report`; `photo` and `measurement` may supplement it |
| 2. Wheels and tires | `front_wheel_security_rotation` | Front-wheel retention, gross side play, rotation, and obvious wobble or rubbing | All bikes | Yes | Guided `functional_check` with a specific `user_report`; `photo` may supplement it |
| 2. Wheels and tires | `rear_wheel_security_rotation` | Rear-wheel retention, gross side play, rotation, and obvious wobble or rubbing | All bikes | Yes | Guided `functional_check` with a specific `user_report`; `photo` may supplement it |
| 3. Brakes | `front_brake_condition_operation` | Visible front-brake condition and stationary engagement | Conditional only on confirmed absence of a front brake | Yes | Guided stationary `functional_check` plus a targeted visual `user_report`; `photo` may replace only the visual portion |
| 3. Brakes | `rear_brake_condition_operation` | Visible rear-brake condition and stationary engagement | Conditional only on confirmed absence of a rear brake | Yes | Guided stationary `functional_check` plus a targeted visual `user_report`; `photo` may replace only the visual portion |
| 4. Contact and control points | `steering_cockpit_security` | Obvious headset, steering, handlebar, stem, lever, and control security concerns | All bikes | Yes | Guided `functional_check` with a specific `user_report`; `photo` may supplement it |
| 4. Contact and control points | `saddle_seatpost_security` | Obvious saddle and seatpost damage or movement | All bikes | Yes | Guided `functional_check` with a specific `user_report`; `photo` may supplement it |
| 5. Drivetrain | `drivetrain_condition_operation` | Visible drive-medium and component condition, retention or tension concern, and stationary operation | All pedal bikes; procedure adapts to resolved configuration | Yes | Targeted visual `user_report` plus a configuration-appropriate guided `functional_check`; `photo` may replace only the visual portion |
| 6. Suspension | `front_suspension_condition_operation` | Visible fork damage, leakage, binding, or failure to support and return | Conditional on front suspension presence | Yes | Targeted visual `user_report` plus a safe guided `functional_check`; `photo` may replace only the visual portion |
| 6. Suspension | `rear_suspension_condition_operation` | Visible rear-shock or linkage damage, leakage, binding, or failure to support and return | Conditional on rear suspension presence | Yes | Targeted visual `user_report` plus a safe guided `functional_check`; `photo` may replace only the visual portion |
| 7. Electric assist | `electric_assist_condition_operation` | Visible battery, wiring, motor-system damage or hazard and stationary power-on warnings | Conditional on electric-assist presence | Yes | Targeted powered-off visual `user_report` plus a safe stationary power-on `functional_check`; `photo` may replace only the visual portion |

All procedures are designed for a novice who is comfortable performing them.
Lower skill or discomfort does not make a check not applicable; the user may
skip it or record that it could not be assessed. Photos may supplement any
entry but are never required. A generic statement about the bike does not
satisfy a targeted user-report requirement.

For atomic escalation of an `unsafe_condition`, the manifest defines these
default safety codes:

| Check IDs | Default blocking safety code |
|---|---|
| `frame_fork_visible_condition` | `frame_or_fork_damage_suspected` |
| `front_brake_condition_operation`, `rear_brake_condition_operation` | `brake_failure_suspected` |
| `front_suspension_condition_operation`, `rear_suspension_condition_operation` | `suspension_internal_concern` |
| `electric_assist_condition_operation` | `ebike_electrical_concern` |
| All other safety-critical manifest checks | `unsafe_riding_condition` |

The agent may raise a more specific valid code when the evidence supports it,
but the atomic default must not be delayed while waiting for a second tool
call.

### 7.2 Safe Procedure Manifest

The checks in each interaction group inherit the corresponding procedure,
stop conditions, prohibited actions, expected observations, and configuration
constraints below. Small off-bike repositioning and hand rotation of an
unridden bike count as stationary inspection. Riding does not.

| Group | Safe manual procedure and expected observations | Stop conditions and prohibited actions | Skill and profile constraints |
|---|---|---|---|
| 1. Overview | Stabilize the bike, walk around it, and inspect the whole bike, frame, fork, steerer area, joints, and tubes. Report cracks, dents, bends, corrosion, unusual paint changes, missing parts, or other obvious damage. | Stop on suspected structural damage, significant impact evidence, or sharp broken parts. Do not flex, probe, scrape, remove, or disassemble anything. | Visual procedure only; adapt attention and safety language for known carbon components. |
| 2. Wheels and tires | Inspect each tire around its accessible circumference for cuts, bulges, exposed casing, embedded objects, gross wear, or obvious loss of air. Reposition the unridden bike as needed. If comfortable, lift one wheel slightly or securely support the bike, rotate it slowly, let it stop, and gently check the stopped wheel for gross side play. Report retention concerns, wobble, rubbing, looseness, and whether each tire appears to hold air. | Stop on a loose or displaced wheel, severe tire damage, major wobble, jamming, or discomfort stabilizing the bike. Never touch a moving wheel or tighten retention hardware. | Do not require lifting when the user cannot safely stabilize the bike; use safe off-bike repositioning, or record the unsupported portion as unable to assess. Do not infer exact pressure without a measurement. |
| 3. Brakes | Inspect visible brake parts for detachment, severe wear, damage, cable or hose problems, and fluid leakage. Operate each brake separately while stationary and gently rock the bike to confirm that it engages and restrains the corresponding wheel. Adapt the action for a confirmed coaster brake. | Stop on leakage, detached or broken parts, a control reaching its limit without braking, or failure to restrain the wheel. Do not ride-test, adjust, tighten, or touch a rotor after movement. | Use only the action appropriate to the resolved brake configuration. An unresolved configuration must be clarified rather than guessed. |
| 4. Contact and control points | With both wheels grounded, gently check the steering and cockpit for knocking, twisting, or obvious movement, using a brake during rocking only if that brake already held in group 3. Gently test the saddle and seatpost for obvious rocking or rotation. Report movement, cracks, damaged controls, or looseness. | Stop on unexpected movement, cracking, a loose control surface, or discomfort. Do not tighten fasteners or apply forceful leverage. | Omit the brake-assisted headset action when brake evidence makes it unsafe; record any unassessed portion as a limitation. |
| 5. Drivetrain | Inspect the chain, belt, sprockets, chainrings, cranks, derailleurs or gear unit, and guards for rust, damage, debris, poor retention, or obviously abnormal slack or tension. Only when the bike is securely supported and the configuration permits it, slowly rotate the crank by hand and report binding, skipping, derailment, unusual noise, or abnormal motion. | Stop on sharp or broken parts, jamming, derailment, or inability to support the bike. Keep fingers, hair, clothing, and tools away from teeth, wheels, the moving drive medium, and other pinch points. Do not shift under load, adjust tension, or disassemble guards. | Adapt for chain, belt, fixed-gear, coaster-brake, geared, and enclosed systems. If safe rotation is incompatible or unavailable, record the operational portion as unable to assess rather than improvising. |
| 6. Suspension | Inspect applicable stanchions, seals, crowns, shock body, mounts, and linkages for damage, leakage, or abnormal position. Only when earlier findings do not make it unsafe, gently compress and release the suspension from a stable control point and report support, smooth movement, binding, noise, leakage, or failure to return. | Stop on structural damage, significant leakage, binding, collapse, or earlier brake, steering, wheel, frame, or fork evidence that makes compression unsafe. Do not adjust, open, inflate, deflate, or touch pressurized internals. | Resolve front and rear presence separately. Do not apply a generic compression procedure to an incompatible design. |
| 7. Electric assist | With power off, visually inspect the installed battery, mounts, accessible wiring, connectors, display, and motor area. If no hazard is present, power the system on while stationary and report whether it starts normally or shows warnings. | Stop immediately for heat, swelling, odor, smoke, leakage, sparking, exposed conductors, crash damage, or water-ingress concern. Do not charge, remove or open the battery, touch damaged areas, clear codes, or ride-test. | Perform only on confirmed electric-assist bikes. A system that cannot safely be powered on must not receive a positive operational result. |

### 7.3 Check Result Status

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

### 7.4 Evidence Sources

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

Photos are optional for the entire V1 inspection. Every checklist entry must
provide a safe, guided, photo-free assessment path. A specific user-reported
result from that guided observation or functional check may satisfy the
entry's qualifying-evidence rule. Photos may improve confidence, resolve
ambiguity, or reduce user effort, but refusing or being unable to submit a
photo must not by itself force a check to be skipped or unable to assess.

The manifest must define qualifying evidence alternatives for
`no_issue_observed`, not only a list of accepted input types. A broad,
unguided statement about the whole bike does not establish several
safety-critical results at once. The backend must reject a positive result
whose recorded evidence sources do not satisfy the applicable check's
qualifying-evidence rule.

### 7.5 Check Ordering

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

The worker must load immutable accepted input from the turn row and load
mutable supporting state, including the current bike profile and inspection
progress, from PostgreSQL at execution time. The turn row pins its image
analysis mode so a later deployment change cannot reinterpret the accepted
turn. V1 inspection turns do not create profile-inference jobs; inspection
photos affect the saved bike profile only through a separately specified
future capability.

Historical image bytes must not be blindly replayed. Prior check results carry
their structured observations, limitations, and artifact IDs. Storage paths,
signed URLs, provider objects, raw extraction scores, prompts, raw ADK events,
and opaque ADK session IDs must not be representable in the context exposed to
the model or public contract. Background-job IDs, execution tokens, deadlines,
attempt counts, and effect-boundary state are also private execution authority
and must not be serialized into ADK state, model input, tool input, public
events, or public schemas.

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
photos, such as drive-side and non-drive-side views, while offering the guided
manual overview as an equally valid photo-free path.

### 9.2 Accepted-Turn Execution

Turn acceptance and turn execution are separate. Acceptance must durably
commit the turn, its `turn.started` event, the session's `running` state, and
one `inspection_turn` background job before returning `202 Accepted`. It must
not call ADK, initialize an ADK session, publish directly to JetStream, or
schedule in-process execution.

The inspection job handler must:

1. load and validate the accepted turn and its bike- and phase-session
   relationships;
2. ensure or resume the deterministic durable ADK session for the inspection
   phase, binding it only while the app-owned phase reference is unbound;
3. fail closed if an already-bound ADK session is confirmed missing;
4. cross the inspection effect boundary using the claimed job's execution
   token immediately before any model call, assistant event, mutating tool, or
   other non-replayable effect; and
5. invoke the inspection orchestrator with one executor-owned, fenced product
   database session used by the orchestrator, tools, and terminalization.

Preparation failures explicitly classified as transient may retry within the
registered pre-boundary attempt policy. Once the effect boundary is crossed,
the whole turn must never be replayed automatically. Section 20 defines the
required product outcome for handled errors and interrupted execution.

### 9.3 Normal Turn

For each accepted user turn, the agent must:

1. Review the server-seeded plan, current check, progress, and safety state.
2. Interpret the user's text, choices, safe functional-check result, and
   current-turn images.
3. Decide whether the evidence supports one or more check results.
4. Call `record_inspection_results` for supported results.
5. Call `raise_safety_flag` immediately for a material safety concern that was
   not already escalated atomically with an `unsafe_condition` result, or when
   a more specific or additional flag is supported.
6. Use the returned next-check projection to request one coherent next input,
   or call `complete_inspection` when no required checks remain.

One user response may record multiple check results. The agent should minimize
repetitive turns without treating one broad photo as proof of conditions it
does not show.

### 9.4 Insufficient Evidence

When evidence is missing, ambiguous, contradictory, poorly visible, or unable
to support a requested condition, the agent must not create a positive result.
It should request one safe, concrete, high-value follow-up that names the view,
action, or answer needed and the question it will help resolve.

If the user declines, cannot perform the check, or the check cannot be assessed
remotely, the agent must record `skipped` or `unable_to_assess` rather than
leaving the checklist implicitly complete.

### 9.5 Safety Interruption

When a material hazard is observed or reported, the agent must:

1. State the factual concern without overstating its cause.
2. Ensure the safety flag is raised, either atomically with the recorded unsafe
   result or through `raise_safety_flag`, before continuing normal checklist
   progression.
3. Tell the user not to ride or perform a check made risky by the concern.
4. Continue only with safe visual or stationary checks, or offer to finish with
   a report whose outcome is derived from the recorded hazard.

### 9.6 Completion

The agent may complete only by successfully calling `complete_inspection`.
Normal text saying the inspection is finished has no state-changing effect.

If completion validation reports missing checks, the agent must request the
next missing input or explicitly resolve those checks as skipped or unable to
assess. Successful completion persists the report, transitions the session,
and ends the conversational phase.

### 9.7 Finish Early and Cancellation

Finishing early and cancelling are distinct confirmed user actions:

- **Finish inspection now** records every remaining applicable check as
  `skipped`, attaches a structured `ended_early` limitation, and completes the
  inspection with a report. The normal deterministic outcome rules still
  apply, so a higher-priority supported finding remains visible; otherwise any
  skipped safety-critical check produces `outcome: incomplete`. This is a
  product-owned completion operation and must not invoke the model.
- **Cancel inspection** creates no report and transitions the session
  permanently to `cancelled` through the product-owned cancellation endpoint,
  never through a model tool. V1 permits this operation only between turns;
  cancellation while an accepted turn has outstanding durable work is
  deferred.

An ambiguous conversational request such as “stop” or “I'm done” must ask the
user to choose between those actions. The agent and client must not silently
interpret it as cancellation.

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
  limitation?:
    code
    description
applicability_resolutions[]:
  check_id
  applicable
  observation
  evidence_source   # explicit user_report
findings[]:
  finding_id?             # omit to create; use a seeded ID to refine
  supporting_check_ids[]
  component
  observation
  condition
  urgency
  confidence
  recommended_next_action
```

Allowed confidence values are `low`, `medium`, and `high`.

Findings are batch-level so that one finding may be supported by several
related check results without duplication. The backend assigns the stable
finding ID when the batch is recorded. A later refinement may include an
existing ID only when that ID belongs to this inspection and was supplied in
the strict server-seeded context; the model must not invent IDs. Every
`attention_needed` or
`unsafe_condition` result must be linked to at least one finding in the batch
or to an already-durable finding being refined. Findings must not be attached
to unsupported check IDs.

The backend must validate finding consistency before persistence. A finding
may be supported only by `attention_needed` or `unsafe_condition` results. If
any supporting result is `unsafe_condition`, the finding must use
`recommended_next_action: stop_riding` and urgency `before_next_ride` or
`immediate`. Otherwise an attention finding must use one of these combinations:

- `monitor` or `routine_maintenance` with urgency `routine` or `soon`
- `start_diagnostic` or `shop_assessment` with urgency `soon` or
  `before_next_ride`

Any other result-status, urgency, and next-action combination must be rejected.

The tool must:

- reject unknown or non-applicable check IDs
- reject applicability resolutions for non-conditional checks, already-resolved
  checks, or resolutions without explicit supporting evidence
- reject unsupported status values
- validate that cited artifacts are owned, available, inspection-purpose
  images associated with this bike session, and allowed in the current context
- reject image-only claims that require a measurement or functional check
- preserve evidence provenance when a later turn refines a check result
- persist linked findings and structured limitations as authoritative
  inspection state
- allow a validated finding refinement to add supporting checks and evidence,
  but never silently reduce the urgency or safety effect of an unsafe finding
- atomically create or reconcile a blocking safety flag through the backend
  safety service whenever a result has status `unsafe_condition`, using the
  manifest's default safety code
- be idempotent for an identical `(turn_id, canonical batch payload)`
- reject conflicting repeated writes for the same check in one turn
- validate the batch atomically
- derive progress and next-check selection in backend code

The successful response must include:

```text
recorded_check_ids[]
resolved_applicability_check_ids[]
created_finding_ids[]
updated_finding_ids[]
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
```

The agent must not resend checklist coverage, check results, findings, or
limitations. The backend derives those from durable inspection state.

The tool must:

- verify every applicable check has a terminal result
- verify every durable finding is supported by stored check results
- verify every `attention_needed` and `unsafe_condition` result has a linked
  durable finding
- derive the canonical outcome and ride guidance from durable check results,
  findings, safety flags, and material coverage gaps; the model must not select
  either value
- derive coverage, findings, limitations, and evidence references from stored
  state
- reconcile all active safety flags
- verify the derived outcome and ride guidance reflect all blocking evidence
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
monitoring_recommended
maintenance_recommended
diagnostic_follow_up_recommended
shop_assessment_recommended
unsafe_to_ride
incomplete
```

The backend must derive the outcome deterministically from durable inspection
state. The agent must not select or override it. The derivation and precedence
rules are defined in Section 12.4.

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

### 12.4 Outcome and Ride-Guidance Derivation

The backend must apply the following outcome rules in precedence order. The
first matching rule wins:

1. An active blocking safety flag or any `unsafe_condition` check result yields
   `outcome: unsafe_to_ride` and `ride_guidance: do_not_ride`.
2. An active warning safety flag or a finding whose recommended next action is
   `shop_assessment` yields `outcome: shop_assessment_recommended` and
   `ride_guidance: professional_assessment_required`.
3. A finding whose recommended next action is `start_diagnostic` yields
   `outcome: diagnostic_follow_up_recommended`.
4. A finding whose recommended next action is `routine_maintenance` yields
   `outcome: maintenance_recommended`.
5. If no higher-priority rule applies and any applicable safety-critical check
   is `skipped` or `unable_to_assess`, the report yields `outcome: incomplete`
   and `ride_guidance: not_assessed`.
6. A finding whose recommended next action is `monitor` yields
   `outcome: monitoring_recommended`.
7. Otherwise the report yields `outcome: no_actionable_findings` and
   `ride_guidance: no_known_blocking_issue`.

Ride guidance defaults to `no_known_blocking_issue` for outcomes selected by
rules 3, 4, and 6, then remains independently safety constrained. An active
caution flag raises it to `use_caution`; an active warning flag raises it to
`professional_assessment_required`; and an active blocking flag raises it to
`do_not_ride`. Missing non-safety-critical checks must be recorded in coverage
and limitations but do not, by themselves, force `outcome: incomplete`.

### 12.5 Coverage

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

### 12.6 Findings

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
`outcome: no_actionable_findings` requires the durable finding list to be
empty. A finding whose recommended next action is `stop_riding` must be linked
to an `unsafe_condition` result and a reconciled blocking safety flag; the
backend must reject any other combination.

### 12.7 Limitations

Limitations must include every skipped, unavailable, poorly visible,
contradictory, or otherwise materially incomplete area that affects how the
report should be interpreted.

Each limitation must use:

```text
InspectionLimitationV1
  code
  area
  check_ids[]
  description
  artifact_ids[]
```

Allowed limitation codes are:

```text
skipped
unable_to_assess
poor_visibility
contradictory_evidence
unsafe_to_continue
ended_early
other
```

The backend must automatically create limitations for skipped and
unable-to-assess checks, checks stopped by safety policy, and early completion.
An inspection-result tool call may supply the specific description and may
identify another applicable code, but it must not omit a required limitation.
`artifact_ids` must contain only approved images that directly explain the
limitation and otherwise must be empty.

## 13. Persistence Requirements

The durable model must support:

- `bike_sessions` as the shared aggregate root, with owner, bike, workflow,
  phase, public status, safety state, current input request, event sequence,
  idempotency metadata, and timestamps
- generic `bike_phase_sessions`, `bike_session_turns`, and
  `bike_session_events` records with no repair-only foreign-key names
- a workflow discriminator on the shared bike-session record
- `inspection` in active phase constraints
- one inspection-state record keyed to the inspection session, containing the
  snapshotted checklist version and other workflow-specific state
- durable per-check applicability, including session-local resolution of
  initially unknown conditional equipment
- inspection progress on the public session projection
- durable check results keyed by inspection session and check ID
- evidence sources, artifact IDs, confidence, observation, status, and optional
  structured limitation on each check result
- durable findings linked to one or more supporting check results, with
  backend-assigned stable IDs
- generic phase-report association keyed to the bike session and phase rather
  than fixed nullable report columns for every workflow on `bike_sessions`
- one inspection report associated with the inspection phase session
- `inspection` report type and `inspection_report.v1` schema version
- an inspection report reference in the session's latest-report projection
- owner-scoped indexes for inspection discovery and resumption
- a database-enforced partial uniqueness invariant allowing at most one
  nonterminal inspection session per bike
- idempotency constraints for session creation, turns, uploads, check-result
  writes, report completion, and cancellation
- one `inspection_turn` background job for every newly accepted inspection
  turn, recorded atomically with the turn and its initial event

The strict V1 job input is:

```json
{
  "turn_id": "turn_..."
}
```

It permits no additional fields. The turn row is authoritative for immutable
accepted content, artifacts, bike-session identity, phase-session identity,
and pinned image-analysis mode. The background-job kind is `inspection_turn`,
the input schema version is `inspection_turn.v1`, and the logical
deduplication key is the accepted turn ID. Job identifiers and lifecycle state
are internal and are not exposed through the product contract.

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
POST /v1/sessions/{sessionId}/completion
POST /v1/sessions/{sessionId}/cancellation
```

These paths replace the existing `/v1/repair-sessions` paths for both repair
and inspection workflows. V1 does not require compatibility aliases or
preservation of existing development-app data. Backend and Android changes
must move to `/v1/sessions` together. Public callers must not choose an ADK
agent, prompt, model, job kind, workload, or worker.

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

At most one inspection session may be active for an owned bike. Creation must
resolve an exact `client_session_id` retry first. Otherwise, if that bike
already has an inspection in `created`, `running`, `awaiting_user`, or
`blocked_safety`, creation fails with the stable conflict code
`active_inspection_exists` and bounded details containing the owner-safe active
session ID. The database must enforce the invariant so concurrent creation
requests cannot bypass it. Completed, failed, and cancelled inspections do not
prevent a new inspection. Repair-session concurrency is unchanged.

### 14.3 Listing and Resumption

Session listing must support filtering by bike and workflow. Inspection
sessions are resumable when `phase: inspection` and status is `created`,
`running`, `awaiting_user`, or `blocked_safety`. Completed sessions open their
report rather than the live conversation. Failed and cancelled sessions show
their terminal state without inventing a report and allow the user to start a
new inspection.

### 14.4 Turns

Inspection turns use the existing `ai_turn.v1` message shape, client-turn
idempotency, artifact limits, and acceptance response. For every new
acceptance, one transaction must:

1. lock and validate the owned bike session and current input request;
2. create or reuse the unbound app-owned inspection phase-session reference;
3. persist the immutable turn and `turn.started` event;
4. clear the answered input request and set the session to `running`; and
5. record one `inspection_turn.v1` job with publication intent.

The transaction commits all records or none. An exact idempotent replay returns
the original acceptance and creates no new job. Acceptance must not call ADK,
depend on ADK storage or NATS availability, publish directly to JetStream, or
create an in-process task. Turn dispatch derives the workflow from persisted
session state; public callers cannot choose a job kind or handler.

Inspection image turns do not create `profile_inference` jobs in V1. Image
normalization and observation extraction, when enabled, run as part of the
fenced inspection-turn execution using the mode pinned on the turn.

### 14.5 Finding Handoff

Starting diagnosis from an inspection finding creates a new repair workflow
session with structured origin provenance. V1 accepts exactly one
`finding_id` per new repair session:

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
transcript. Multi-finding selection and grouped handoff are not supported in
V1.

### 14.6 Early Inspection Completion

The confirmed “Finish inspection now” action uses:

```text
POST /v1/sessions/{sessionId}/completion
```

with:

```json
{
  "schema_version": "inspection_completion.v1",
  "reason": "finish_early",
  "client_completion_id": "android-finish-inspection-001"
}
```

This is an authenticated, owner-scoped, product-owned operation and must not
invoke a model. In one transaction, the backend must mark every pending
applicable check `skipped`, create the required per-check and `ended_early`
limitations, derive and persist the inspection report, and transition the
session to `completed`. The response includes the authoritative completed
session and inspection report.

`client_completion_id` is required for idempotency. An exact retry returns the
existing completed session and report; reuse with a different canonical
payload fails with the stable idempotency-conflict behavior. The operation is
valid for inspection sessions in `created`, `awaiting_user`, or
`blocked_safety`. A session with a running turn returns a conflict rather than
racing that turn, and Android must disable the action while a turn is active.
Normal completion after all checks are resolved continues to use the
`complete_inspection` agent tool.

The workflow-neutral completion path also replaces the existing repair-only
completion path. Its repair-workflow request and behavior remain defined by
the canonical repair contract.

### 14.7 Cancellation

Cancelling an inspection uses:

```text
POST /v1/sessions/{sessionId}/cancellation
```

with:

```json
{
  "client_cancellation_id": "android-cancel-001"
}
```

Cancellation is an authenticated, owner-scoped, product-owned operation and
must not invoke a model. `client_cancellation_id` is required for idempotency;
an exact retry returns the existing cancelled session. Cancelling an already
cancelled session also returns its authoritative snapshot. Cancelling a
completed session fails with the existing stable conflict behavior.

V1 cancellation is valid only in `created`, `awaiting_user`, or
`blocked_safety`. A session in `running` has an outstanding durable turn and
returns a stable conflict; Android must disable cancellation until the turn
settles. This deliberately avoids adding business cancellation to the generic
job lifecycle or assuming task cancellation can stop an external model call.
Cancellation creates no report. The response includes the authoritative
session snapshot with `status: cancelled`.

## 15. Event and SSE Contract

Inspection must preserve the existing durable event-log contract:

- persist and commit before live fan-out
- monotonically increasing sequence per bike session
- decimal public cursors
- `after` precedence over `Last-Event-ID`
- persisted replay followed by live delivery
- post-commit Core NATS wake-up hints with PostgreSQL polling fallback across
  API processes; wake-up failure must never roll back an event
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
this spec. V1 must not record a profile-inference job for an inspection turn.
It may be enabled later only under the canonical profile-inference rules and a
versioned job-acceptance update.

## 17. Backend Module Shape

### 17.1 Shared Session Conversation Module

The shared module owns bike-session loading, ownership, session and turn
idempotency, durable turn/event creation, artifact validation, event replay,
SSE formatting, and workflow dispatch. It records typed jobs through the
existing background-job interface but does not publish, consume, or execute
them. Its external interface remains the workflow-neutral
session/turn/event contract.

Repair and inspection implementations are adapters selected from persisted
workflow. A workflow adapter validates workflow-specific phase/status rules,
creates or loads the appropriate phase state, and identifies the registered
turn job to record. Routes must not import an inspection or diagnostic agent,
worker handler, or JetStream adapter directly.

The shared module must hide persistence renaming and queue mechanics from
callers. It must not become a registry of checklist, diagnostic, report, or
prompt rules; those remain local to workflow modules.

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

### 17.3 Inspection Job Adapter and Worker Composition

`inspection_turn.v1` is a typed job adapter registered in the existing
model-intensive workload class that hosts diagnostic turns. Inspection and
diagnostic turns have compatible provider, model, security, latency, and
capacity characteristics in V1, so inspection does not justify a new stream,
consumer, runtime, or worker deployment. The workload registry deployed to
that role must contain both job definitions before the API may record
inspection jobs.

The registered inspection policy must define a bounded attempt limit, retry
backoff, hard execution duration, timeout grace, effect-boundary behavior, and
terminalization behavior. V1 should start with the existing diagnostic-turn
timing and attempt defaults because the workloads are operationally alike, and
must not add nested model or provider retries that multiply that budget.

The inspection handler receives a claimed typed job and invokes application
behavior. It does not receive a JetStream message and cannot acknowledge,
republish, retry, or settle transport deliveries. The shared runtime retains
those responsibilities.

The current diagnostic execution-authority module must be generalized for
both interactive agent-turn job kinds. Its interface carries the opaque job
identity, execution token, and hard deadline privately. It owns boundary
crossing and stale-write validation while keeping queue types out of workflow
modules, model context, tools, and public contracts.

### 17.4 Inspection Orchestration

The inspection orchestrator processes one already-accepted turn. It prepares
current-turn visual context, seeds strict app context, invokes the inspection
runner, translates normalized runner events into product events, and finalizes
the turn safely. It is composed inside the worker process with durable ADK
sessions and other process-owned provider resources. It does not open an
independent product database session; orchestration, mutating tools, events,
reports, and completion use the same fenced executor-owned session.

The runner owns ADK adaptation and raw-event normalization. It does not persist
public events. State-mutating tools write authoritative product state once;
their runner notifications must not cause the orchestrator to repeat those
writes.

### 17.5 Report and Safety Modules

The report module validates and persists inspection reports from durable
inspection state. The safety module validates inspection flags and derives
session safety state. Neither invariant may exist only in the prompt.

### 17.6 Terminalization and Recovery

One inspection terminalization module must own the idempotent product writes
used by normal handled failure, permanent pre-boundary failure, and
post-boundary recovery. It validates the job's stored turn reference and
atomically restores the session to its derived user-recoverable status,
appends one safe `error` event, appends the immediately following
`turn.completed` event, and applies the corresponding terminal job outcome.

Maintenance recovery must call that same module through the job-kind recovery
policy. It must not reconstruct inspection state heuristically or invoke ADK.

## 18. Android Behavior

### 18.1 Entry and Discovery

Android must provide:

- an “Inspect a bike” entry action
- owned-bike selection
- inspection session creation
- bike-scoped inspection session discovery
- resumption of active inspection sessions
- report opening for completed inspection sessions

When creation returns `active_inspection_exists`, Android must open the
returned owned inspection session rather than showing a generic failure.

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
- offer separately confirmed “Finish inspection now” and “Cancel inspection”
  actions, explaining whether a partial report will be created; both actions
  are disabled while a turn is `running`

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
- an `unsafe_condition` result and its blocking safety escalation persist in
  one transaction, so an unsafe result can never leave the session unblocked
- blocking concerns produce `do_not_ride` or
  `professional_assessment_required` ride guidance
- an unsafe check result cannot be downgraded merely by later model text
- active safety flags appear in the final report
- completion remains possible when unsafe checks are stopped and limitations
  are recorded

## 20. Error Recovery and Idempotency

Inspection follows the existing safe error model:

- accepted turns and their jobs are durable before model processing begins
- exact client-turn retries return the existing accepted turn
- duplicate deliveries and idempotent HTTP retries do not create additional
  application attempts or model invocations
- explicitly classified preparation failures before the effect boundary may
  retry within the job's bounded attempt policy
- whole-turn execution is never retried automatically after the effect
  boundary
- report persistence and phase transition are atomic from the client's
  perspective
- the durable event log, not JetStream or Core NATS hints, is the reconnect
  mechanism

Inspection must reuse the production PostgreSQL-backed ADK session adapter.
The worker ensures a deterministic ADK identity before crossing the effect
boundary and binds it with a null-only compare-and-set. A lost binding commit
may repeat that deterministic ensure. Once bound, a confirmed missing ADK
session fails closed and must not be recreated under a new identity.

Every protected inspection write must compare the same immutable execution
token and hard deadline used to cross the effect boundary. Token replacement,
deadline expiry, or explicit stale detection must roll back later writes from
the old execution. Transport `in_progress` acknowledgements may extend only
the JetStream acknowledgement window; they must not extend product execution
authority.

Job outcomes follow these rules:

- A normally completed turn commits its product outcome before the job becomes
  succeeded.
- A handled runner or model error after the boundary commits one safe `error`
  event, restores the session to `blocked_safety` when a blocking flag remains
  active and otherwise `awaiting_user`, appends the immediately following
  `turn.completed`, and then settles the job as succeeded. Here job success
  means a durable product outcome, not a successful model answer.
- Exhausted or permanent pre-boundary failure atomically marks the job dead and
  writes that same recoverable public error/completion pair without invoking
  ADK.
- Abrupt post-boundary loss becomes interrupted. Maintenance recovery replaces
  or invalidates execution authority, writes the error/completion pair through
  the shared inspection terminalization module, and never invokes ADK again.
- Repeated delivery or recovery of a terminal job is an idempotent no-op.

The public error remains retryable by accepting a new user turn and therefore
a new job. It never revives or replays the terminal job. Process-local ADK
sessions and in-process turn executors are allowed only in isolated tests, not
in production or production-parity integration tests.

## 21. Testing and Evaluation

### 21.1 Deterministic Tests

Backend unit and contract tests must cover:

- workflow/phase/status combinations
- owner-scoped creation, reads, listing, turns, reports, findings, and artifacts
- concurrent creation and one-active-inspection-per-bike enforcement
- session, turn, upload, result-write, and completion idempotency
- cancellation idempotency and active-turn cancellation conflict behavior
- `running` status retention when safety becomes blocked mid-turn, followed by
  derived `blocked_safety` terminal status
- atomic turn, event, session-status, and `inspection_turn` job acceptance
- no inspection-triggered profile-inference job in V1
- strict `inspection_turn.v1` input, logical deduplication, and registry policy
- duplicate delivery, pre-boundary retry/exhaustion, effect-boundary crossing,
  stale-token rejection, handled error, and post-boundary interruption
- worker-first job-kind rollout and unknown-version fail-closed behavior
- deterministic ADK ensure/bind, restart resume, and missing-bound-session
  failure
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
- generic bike-session migration and unchanged diagnostic behavior through the
  new shared model, routes, event log, artifacts, reports, and worker

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
- allowlisted job kind, attempt outcome, and interruption category

Telemetry must not include report text, observations, prompts, user messages,
image bytes, storage locations, model reasoning, or arbitrary tool payloads.
Model/content capture remains disabled unless explicitly allowed by the
existing local-only trace-content policy.

## 23. Implementation Order

Implement the vertical slice in this order:

1. Generalize the shared aggregate and persistence names to `BikeSession`,
   `bike_sessions`, generic phase sessions, turns, events, reports, and
   session-artifact associations. Replace development-only data rather than
   adding compatibility storage.
2. Move OpenAPI and Android from `/v1/repair-sessions` to `/v1/sessions`, add
   the workflow discriminator, and prove the existing repair diagnostic flow
   unchanged through the generic contract.
3. Generalize the interactive agent-turn execution authority, terminalization
   interface, and worker composition so diagnostic behavior retains its
   current queue-only guarantees without retaining repair-only shared names.
4. Add inspection persistence migrations for state, applicability, check
   results, findings, report associations, and the one-active-inspection
   invariant.
5. Implement and test the inspection-plan, safety, report, early-completion,
   cancellation, and finding-handoff modules without an agent.
6. Generalize shared image preparation and artifact handling for
   `inspection_photo`, with inspection-triggered profile inference disabled.
7. Implement `inspection_turn.v1`, its handler, effect/retry policy, recovery
   policy, and terminalization. Deploy the expanded worker registry while the
   API producer remains disabled.
8. Add strict inspection tool context, the four ADK tool adapters, the single
   inspection agent, prompt, runner normalization, and fenced orchestration.
9. Verify restart, duplicate delivery, pre-boundary recovery, post-boundary
   interruption, stale-write rejection, cross-process SSE wake-ups, and
   diagnostic/inspection capacity in production-parity integration tests.
10. Enable API production of inspection jobs, then add Android inspection
    creation, chat, progress, report, and finding handoff behind a rollout
    gate.
11. Complete agent evaluations, privacy checks, observability, operational
    inspection, and rollback evidence before general release.

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
- cancellation while a turn has outstanding durable work
- a separately scaled inspection worker workload if measurements later show
  materially different capacity or isolation requirements

These extensions must preserve the core distinction between inspection
findings and diagnostic conclusions, and must not weaken explicit coverage or
safety limitations.
