# BikeDoc Image-Based Diagnosis Spec

Status: Draft v0.1
Last updated: 2026-07-21

This document defines how BikeDoc uses user-submitted diagnostic images as
actual visual evidence during the diagnostic phase. It covers image validation
and normalization, isolated structured observation extraction, delivery of the
same image pixels to the diagnostic agent, evidence handling, safety behavior,
and evaluation methodology.

`docs/specs/openapi.yaml` remains the canonical public HTTP contract. This
feature uses the existing artifact-upload and diagnostic-turn interfaces, but
the contract and `api-diagnostic.md` must add `maxItems: 3` to
`UserTurnMessage.artifact_ids` before implementation. The limit is public
request validation behavior, not an internal image-processing detail.

## References

- Product behavior: `docs/specs/bike-doc.md`
- Backend organization: `docs/specs/apps/api.md`
- Diagnostic API behavior: `docs/specs/apps/api-diagnostic.md`
- Diagnostic artifact storage: `docs/specs/apps/api-artifacts-diagnostic.md`
- Diagnostic ADK tools: `docs/specs/apps/adk-diagnostic-tools.md`
- Diagnostic report schema: `docs/specs/apps/diagnostic-report-v1.md`
- Diagnostic safety rules: `docs/specs/apps/safety-diagnostic.md`
- Bike-profile image inference:
  `docs/specs/apps/agent/bike-profile-inference.md`

## Normative Language

The terms **must**, **must not**, **should**, **should not**, and **may** are
normative. “Must” and “must not” define required behavior. “Should” and
“should not” define the expected default unless a later canonical spec records
a justified exception.

## 1. Purpose

BikeDoc must inspect the pixels of diagnostic images rather than only recording
that an image was submitted. Images should help the system identify visible
wear, damage, contamination, leakage, misalignment, missing parts, and other
conditions relevant to the user's reported problem.

The system must use images conservatively. A photo can provide important
evidence, but it cannot prove conditions that are hidden, require physical
measurement, or require an in-person inspection.

## 2. Goals

- Make submitted diagnostic images available as multimodal input during the
  same diagnostic turn.
- Extract structured, artifact-backed visual observations in a fresh model
  context before diagnostic reasoning.
- Give the diagnostic agent both the normalized images and the structured
  observations.
- Preserve explicit uncertainty, assessability, and visibility limitations.
- Let the diagnostic agent request a more useful image, measurement, or textual
  answer when the available evidence is insufficient.
- Keep safety enforcement and report validation app-owned.
- Evaluate image behavior using real image inputs and condition-level labels.

## 3. Non-Goals

V1 does not require:

- a specialized computer-vision model or service
- multiple model ensembles or majority voting
- model routing based on issue type or risk
- automatic object detection, segmentation, or crop generation
- a separate OCR pipeline
- fine-tuning a vision model
- searching external image catalogs or repair references
- requiring the user to submit two different photos
- replaying every historical image to the diagnostic agent on every turn
- selecting or reloading a prior turn's image for a new visual question
- treating visual observations as durable bike-profile facts
- adding new public artifact-upload or turn-submission shapes or routes beyond
  documenting the three-artifact-per-turn validation limit

Targeted crops, specialist models, verification models, or routing policies may
be added later only when evaluation evidence justifies the additional
complexity.

## 4. Canonical Language

### Diagnostic Image

A **diagnostic image** is an owned image artifact attached to the active repair
session and referenced by an accepted diagnostic user turn.

### Normalized Diagnostic Image

A **normalized diagnostic image** is a safe, correctly oriented model-input
derivative of the uploaded image. The original uploaded object remains the
artifact source of truth.

### Visual Observation

A **visual observation** is a short, structured statement about something
visible or not assessable in one or more diagnostic images. It is evidence for
diagnostic reasoning, not a diagnosis and not hidden model reasoning.

Examples include:

- “Dark residue is visible below the rear caliper bleed port.”
- “The rear derailleur cage appears out of plane with the cassette.”
- “Remaining brake-pad thickness cannot be assessed from this angle.”

### Observation Extraction Run

An **observation extraction run** is one isolated, versioned model call over the
new diagnostic images referenced by an accepted user turn.

### Two-View Image Analysis

**Two-view image analysis** means that the normalized image pixels are inspected
twice for different purposes:

1. A fresh-context observation extractor produces structured visual evidence.
2. The regular diagnostic agent independently receives the pixels while using
   the observations during diagnostic reasoning.

“Two-view” does not mean that the user must upload two photos. One submitted
image is sufficient to enter this flow.

The two model inspections are not independent real-world evidence. Agreement
between them must not be treated as equivalent to a second photo, a physical
measurement, or a mechanic's inspection.

## 5. Guiding Decisions

### 5.1 The Same Pixels Support Two Different Tasks

When observation extraction is enabled, the observation extractor and the
diagnostic agent must both receive the normalized diagnostic images for the
current turn. When extraction is disabled in `pixels_only` mode, the diagnostic
agent must still receive the normalized images and no extraction call may be
made.

`off` is a safe rollback mode. The backend must perform ordinary artifact
ownership, repair-session association, and ready-status validation, but it must
not decode, normalize, or send image pixels to either model view. For a
text-bearing turn, the diagnostic agent must receive the text, artifact IDs,
and an explicit status that the images were not inspected. For an image-only
turn, the backend must call neither model view, emit a recoverable
`image_analysis_unavailable` processing error, and leave the session awaiting
textual input.

The observation extractor provides consistent, structured, auditable evidence.
The diagnostic agent receives the pixels so it can notice details omitted by
the extractor, relate the image to the conversation, and challenge an incorrect
or incomplete observation.

### 5.2 Extraction Uses Fresh Context

The observation extractor must not receive diagnostic conversation history,
working hypotheses, repair history, prior reports, or the resolved bike profile.
This isolation reduces pressure to make the image agree with an existing
diagnosis.

The extractor may receive:

- server-owned artifact IDs and MIME types
- the normalized image bytes
- the output schema and extraction instructions

In V1, the extractor must not receive `message.text` or any other user-authored
turn text. This keeps observation extraction independent of symptom-led
diagnostic hypotheses; the regular diagnostic agent receives that text and is
responsible for relating it to the visual evidence.

The extractor must also not receive the prompt from a prior photo input
request, a server-generated conversation summary, a distilled diagnostic
question, or any other symptom- or hypothesis-directed context. Its task is a
context-free inventory of visually apparent bicycle-condition evidence,
assessability, and limitations. In extractor output, “relevant” means relevant
to the visible condition of the bicycle or its components, not relevant to the
user's reported symptom. Only the diagnostic agent determines symptom
relevance in V1.

Extractor output is not an exhaustive inventory of everything visible in an
image. The absence of an observation about a condition must not be treated as
evidence that the condition is absent. The diagnostic agent may form and cite
an artifact-backed conclusion directly from the pixels even when the extractor
did not mention that feature, while preserving the ordinary uncertainty and
safety rules.

Images are untrusted evidence, not instructions. Both model views must be
instructed to ignore instruction-like content found in the image and to use it
only when assessing bicycle evidence. Fixed backend instructions, tool
boundaries, and safety controls remain authoritative.

### 5.3 Observations Are Not Diagnoses

The extractor must describe visible evidence, limitations, and useful follow-up
views. It must not select a primary diagnosis, recommend a repair, estimate
cost, determine DIY suitability, or complete the diagnostic phase.

The diagnostic agent remains responsible for combining visual evidence with
symptoms, bike configuration, repair history, contradictions, and safety rules.

### 5.4 Condition Evidence Is Session-Scoped

Visual observations about wear, damage, leakage, alignment, or contamination
belong to the repair session. They must not be persisted as bike-profile facts.

The separate bike-profile inference flow may inspect the same submitted image
to infer relatively durable installed configuration. Its claims and resolver
rules remain governed by `bike-profile-inference.md`.

### 5.5 The Backend Remains Authoritative

The model may propose observations, confidence scores, follow-up needs, and
safety-relevant cues. Backend code must validate the output structure,
artifact references, ownership, and version before the output is used.

Model output must not directly mutate repair-session safety state or persist a
diagnostic report outside the existing app-owned services and tools.

### 5.6 Implementation Contracts

This section fixes the persistence, recovery, and privacy choices needed to
implement this specification. It defines contracts, not SQLAlchemy models,
migrations, provider code, or a general artifact-deletion workflow.

#### 5.6.1 Artifact-ID Request Validation

`UserTurnMessage.artifact_ids` MUST contain at most three entries and MUST NOT
contain duplicate values. The public OpenAPI contract and app-owned request
schema MUST express both rules (`maxItems: 3` and `uniqueItems: true`).

The limit applies to the submitted list before any deduplication. A request
containing a duplicate ID or more than three entries MUST fail with the normal
`422 validation_error` response before artifact lookup, turn persistence,
background work, or model access. The backend MUST NOT silently deduplicate
the list.

#### 5.6.2 Per-Artifact Processing Failure Contract

Ownership, session-association, and existence failures remain pre-acceptance
HTTP errors and MUST NOT reveal an unowned artifact through a stream event.

After a turn has been accepted, each recoverable image-processing failure that
applies to one artifact MUST be persisted and emitted as one `error` event
whose data includes that artifact's ID. `ErrorEventData` in the public OpenAPI
contract and app-owned event schema MUST add an optional `artifact_id` field.
For artifact-specific image-processing codes, `artifact_id` is required by
this specification even though it remains optional for unrelated error events.

The public code set MUST be bounded. V1 uses `image_not_ready`,
`image_decode_failed`, `image_normalization_failed`, and
`image_analysis_unavailable` where applicable. Public messages MUST be safe,
short descriptions and MUST NOT expose parser, storage, provider, or security
details. A separate event is emitted for each affected artifact; the backend
MUST NOT rely on an aggregate message or ambiguous prose to identify it.

`retryable` means that repeating processing of the same artifact may succeed
without replacement. It is normally true only for a transient not-ready or
loading condition, and false for malformed, MIME-mismatched, oversized, or
otherwise invalid image content. It does not promise an inline retry of the
accepted turn.

The diagnostic-agent input MUST receive a typed, artifact-ID-keyed processing
status for every submitted artifact excluded before model access. It MUST NOT
derive that status by parsing public event messages.

#### 5.6.3 Mode Snapshot and Logical Extraction Run

An accepted diagnostic turn with one or more artifact IDs MUST snapshot the
effective image-analysis mode in an immutable `RepairTurn.image_analysis_mode`
field. Exact idempotent replay MUST reuse the accepted turn and its stored
mode; it MUST NOT read current deployment configuration again. The field may
be null only when image analysis does not apply to the accepted turn.

`off` and `pixels_only` turns MUST NOT create an observation extraction run.
Each `shadow` or `enabled` turn MUST create exactly one logical observation
extraction run with an unconditional unique constraint on its accepted turn ID.
The run is the only durable visual-evidence record for that turn; changing a
preprocessing, extractor, prompt, schema, provider, or model version MUST NOT
create a second run for the same turn.

The logical run MUST contain:

- an app-owned ID, accepted turn ID, and repair-session ID;
- the copied, snapshotted `shadow` or `enabled` mode;
- the unique ordered input artifact IDs;
- preprocessing, extractor, prompt, output-schema, provider, and model
  versions;
- a per-artifact preprocessing manifest containing no image bytes, including
  effective MIME type, original and normalized dimensions, normalized content
  hash when available, and processing outcome;
- lifecycle status; validated output when available; bounded failure metadata;
  and timestamps;
- provider-attempt count plus aggregate provider latency, token usage, and
  cost when available;
- the diagnostic-agent-start and redaction markers defined below.

Run lifecycle status MUST be `pending`, `completed`, or `failed`. `pending` is
the nonterminal state from durable run creation through active processing.
`completed` means a schema-valid result was stored, including a valid result
with zero observations. `failed` means no validated output is usable. A failed
run may return to `pending` only during explicit eligible recovery.

Each provider call MUST be recorded as an ordered provider-attempt record on
the same run, with a unique `(run_id, attempt_number)` identity. An attempt
MUST record its provider/model identity, start and completion times, terminal
outcome, bounded failure metadata when applicable, latency, token usage, and
cost when available. Attempt records are execution history, not independent
visual evidence. Raw provider responses MUST NOT be persisted; only validated
structured output may be stored on the logical run.

Provider-attempt count starts at zero. It is incremented only when a provider
call begins. Therefore, a run in which every image fails preprocessing is
`failed` with zero provider attempts; a provider response that fails output or
artifact validation is a failed provider attempt.

#### 5.6.4 Normalized-Derivative Lifecycle

Normalized image bytes MUST be ephemeral in V1. They may exist only in bounded
process memory or securely cleaned temporary storage while the accepted turn is
being processed. The same ephemeral bytes MUST be reused for both model views
within that orchestration pass. A normalized derivative MUST NOT become a
public artifact or a privately persisted byte object in V1.

The original artifact remains the source of truth. The run MUST snapshot a
deterministic preprocessing version and the metadata listed above. Explicit
recovery reloads the original artifact and reprocesses it using that exact
version; it MUST NOT silently substitute the currently deployed preprocessor.
When a prior normalized hash exists, recovery MUST verify that the regenerated
derivative has the same hash. If the snapshotted preprocessing version is no
longer available, recovery MUST fail explicitly.

V1 normalization MUST produce 8-bit sRGB, composite any transparency onto a
white background, and encode a baseline non-progressive JPEG using pinned
quality and chroma-subsampling settings. The normalized MIME type is therefore
`image/jpeg`. EXIF, ICC profiles, comments, thumbnails, and other unnecessary
metadata MUST be removed. These encoder settings are part of the
preprocessing-version contract.

#### 5.6.5 Diagnostic-Agent Start Gate

Each logical run MUST have a nullable, write-once
`diagnostic_agent_started_at` timestamp. It records the point at which
orchestration durably commits to invoking the regular diagnostic agent for the
accepted turn. The backend MUST set it transactionally immediately before that
invocation and MUST NOT clear it.

An additional provider attempt is permitted only when all of the following are
true: the run is failed for a retryable reason, recovery of the same turn is
explicit, and `diagnostic_agent_started_at` is null. Attempt creation MUST
enforce this gate atomically. Once the timestamp is non-null, extraction MUST
not be retried or completed later in the background.

This rule intentionally favors evidence consistency over an extra extraction
attempt if a process fails after the marker is recorded but before the provider
receives the diagnostic-agent call.

#### 5.6.6 Artifact Invalidation and Evidence Redaction

Artifact deletion or a transition that renders an artifact inaccessible MUST
invoke one app-owned diagnostic-evidence invalidation hook for that artifact.
The hook is part of the artifact lifecycle interface; this specification does
not define a public deletion route or a general retention workflow.

If an invalidated artifact appears in a logical run, the backend MUST redact
the whole run rather than selectively editing individual observations or image
assessments. It MUST set an irreversible redaction marker and reason, clear the
validated output and per-artifact preprocessing manifest, and prevent ordinary
repository reads from returning the run as diagnostic or report evidence. It
may retain only non-content operational metadata such as versions, lifecycle
status, counts, timing, and cost.

The invalidation hook MUST also ensure that later diagnostic turns and new
reports cannot use the redacted run. If an already persisted report contains or
cites evidence from the run, the artifact/privacy workflow MUST redact that
affected evidence or make the report ineligible as current evidence before it
is served for that purpose. This preserves the rule that observations do not
outlive a source artifact without introducing a partially-redacted extractor
output format in V1.

## 6. Required Turn Flow

When an accepted diagnostic turn references one or more image artifacts, the
system must perform this flow:

0. Accept at most three artifact IDs in a user turn, as declared by
   `UserTurnMessage.artifact_ids` in the public OpenAPI contract. A turn that
   references more than three artifacts must fail validation before any
   artifact is loaded or model call is made.
1. Validate that every artifact is owned by the user, attached to the active
   repair session, ready, and an accepted image type.
   - In `off` mode, stop image processing after this validation. If the turn
     contains text, proceed directly to the diagnostic agent with the text,
     artifact IDs, and explicit image-unavailable statuses. If the turn is
     image-only, call neither model view, emit `image_analysis_unavailable`,
     and leave the session awaiting textual input.
2. Safely decode each image and validate that its content matches its effective
   MIME type. Exclude an image that fails validation, record a recoverable
   per-artifact processing error, and continue when at least one submitted image
   remains valid.
3. Preserve the original object in app-owned storage. Production storage is
   GCS; local and test adapters may use the existing storage provider seam.
4. Produce or load a normalized diagnostic image derivative.
   - If every submitted image is unusable and the turn contains text, skip
     extraction and invoke the diagnostic agent with the text plus the artifact
     IDs and failure statuses, but without image parts or observations.
   - If every submitted image is unusable and the turn is image-only, call
     neither model view, emit a recoverable processing error, and leave the
     session awaiting a replacement upload.
5. When configured for extraction, run one fresh-context structured observation
   extraction over the valid images from the current turn. In `pixels_only`
   mode, skip this step without making an extraction provider call.
6. When extraction ran, validate the structured extraction result.
7. Invoke the regular diagnostic agent with:
   - the current user text
   - the valid normalized images, each labeled with its artifact ID
   - the artifact IDs and failure status of submitted images excluded during
     validation or normalization
   - the validated structured observations when the configured mode supplies
     them
   - the ordinary app-owned bike profile, history, and diagnostic context
8. Let the diagnostic agent request more input, raise a safety concern, refine
   its hypotheses, or complete the diagnostic report through the existing
   tools.

When extraction is configured, it must finish before the diagnostic agent is
invoked so the observations are available during the same turn. Bike-profile
inference may remain an independent background process and must not delay this
flow.

Each accepted image-bearing turn processed in an extraction-enabled mode must
have exactly one logical observation extraction run, keyed to the accepted
turn. Replaying the same client turn or resuming diagnostic orchestration must
reuse that run; a completed run must not be executed again. A
backend retry after a retryable failure may make another provider call, but it
must be recorded as an additional attempt on the same logical run rather than
as competing visual evidence. `pixels_only` mode must not create an observation
extraction run or provider attempt.

Normal turn processing must make at most one extraction-provider attempt. On a
timeout or other retryable extraction failure, the backend must continue to the
diagnostic agent with pixels as described in Section 11 rather than delaying
the turn for an inline retry. An additional provider attempt is allowed only
when the same turn undergoes explicit orchestration recovery before diagnostic
agent processing has begun. Extraction must not be retried in the background
after the diagnostic agent has proceeded.

The backend must snapshot the effective image-analysis mode when it accepts an
image-bearing turn. Retries, idempotent client replays, and orchestration
recovery for that turn must use the snapshotted mode even if deployment
configuration changes afterward. The snapshot is stored on the accepted repair
turn as defined in Section 5.6.3.

## 7. Image Validation and Normalization

### 7.1 Validation

Existing upload-size, MIME-type, ownership, repair-session, idempotency, and
storage rules remain governed by `api-artifacts-diagnostic.md`.

Before model access, the system must additionally ensure that the image:

- can be safely decoded
- has positive dimensions
- is not truncated or malformed
- does not exceed 40 megapixels after decode
- is in a provider-supported format after normalization

An invalid or undecodable image must not be sent to either model view. When at
least one other submitted image is valid, the turn must surface a recoverable
per-artifact processing error and continue with the valid images. The invalid
artifact must not be silently omitted from diagnostic-agent context.

When every submitted image is invalid or undecodable, extraction must make no
provider call. An extraction-enabled mode must still create or reuse the turn's
logical extraction run and record it as `failed` with zero provider attempts.
If the turn contains usable text, the diagnostic agent must receive that text
and the per-artifact failure statuses without image parts. If the turn is
image-only, neither model view may be called; the backend must emit a
recoverable processing error and leave the session awaiting a replacement
upload.

### 7.2 Normalization

Normalization should perform only the work needed to create reliable model
input:

- apply the effective image orientation
- convert to the deterministic normalized color and encoding format defined in
  Section 5.6.4
- preserve aspect ratio and limit the normalized derivative to a 2048-pixel
  long edge
- strip EXIF and other unnecessary embedded metadata from the derivative
- record original and normalized width and height, content hash, and
  preprocessing version internally

The implementation should retain the original uploaded object and use the
normalized derivative for both model views. V1 does not require public exposure
of the derivative or a second public artifact record.

Images exceeding the decoded-pixel limit must be rejected before normalization
or model access. The original upload-size limit remains governed by
`api-artifacts-diagnostic.md`.

V1 must not introduce automatic cropping or enhancement. A later version may do
so if real-image evaluation shows that small details are routinely missed.

### 7.3 Image Quality

The observation extractor must report when blur, glare, darkness, framing,
distance, occlusion, or perspective prevents a reliable assessment.

V1 does not require a separate client-side or computer-vision quality-scoring
system. The diagnostic agent may use the extractor's limitations to request a
specific replacement or additional view.

## 8. Structured Observation Extraction

### 8.1 Input

Images must be paired with server-owned artifact labels so the extractor can
reference the correct evidence. Multiple images from the same turn should be
sent together so an observation can cite every artifact that supports it.

The extraction call should use deterministic generation settings and a strict
structured-output schema.

### 8.2 Output

The output schema must contain:

| Field | Meaning |
|---|---|
| `schema_version` | Version of the structured observation contract. |
| `image_assessments` | Per-artifact assessability and image-quality limitations. |
| `observations` | Context-free, visible, artifact-backed evidence relevant to the condition of the bicycle or its components. |
| `suggested_follow_up` | Optional targeted photo, measurement, or text request. |

V1 has no top-level `abstentions` field. Because the extractor receives no
diagnostic question, open-ended condition abstentions would be unbounded and
noisy. Inability to assess the submitted view must be represented through the
corresponding `image_assessments` entry and its specific limitations. An
unsupported positive finding must simply be omitted from `observations`.

`image_assessments` must contain exactly one entry for every normalized image
sent to the extractor, and no entry for an image rejected before extraction.
Each entry must contain:

- the submitted `artifact_id`
- `assessability`: `usable`, `limited`, or `unusable`
- `visible_areas`: a short list of bicycle areas or components that can be
  meaningfully inspected
- `limitations`: zero or more typed limitations, each with a short factual
  description

The allowed limitation types are `blur`, `glare`, `darkness`, `framing`,
`distance`, `occlusion`, `perspective`, and `other`. `assessability` states
whether the image supports any reliable bicycle-condition observation; it must
not imply that every depicted component or condition is assessable. Images
rejected during validation or normalization receive backend-owned processing
failure statuses instead.

Each observation must contain:

- one or more submitted artifact IDs
- the observed bicycle area or component when identifiable
- front, rear, whole-bike, or unknown position when relevant
- a short factual finding
- one or more short visible evidence cues
- visibility such as `clear`, `partial`, or `poor`
- a finite raw model score from `0.0` to `1.0`, inclusive
- whether the cue may be safety relevant

One extraction run may return at most 12 observations across all submitted
images. When more visible details are available, the extractor must prioritize,
in order:

1. potentially safety-relevant condition cues
2. clear signs of damage, wear, leakage, contamination, misalignment,
   corrosion, or missing parts
3. distinct evidence rather than duplicate descriptions of the same condition
   across views

Normal installed-component inventory belongs to bike-profile inference and
must not consume diagnostic observation capacity.

The exact Pydantic and persistence schemas belong to implementation work. They
must remain app-owned and must reject unknown fields by default.

### 8.3 Extraction Rules

The extractor must:

- describe only evidence grounded in the submitted images
- inventory visually apparent bicycle-condition evidence without attempting to
  infer which observations matter to the user's unprovided complaint
- distinguish installed parts from loose parts, packaging, reference images,
  and ambiguous subjects
- preserve front/rear uncertainty rather than guessing a position
- omit a positive observation when the condition is hidden, too small, blurred,
  occluded, or distorted, and record the applicable assessability limitation
  for the image
- avoid converting apparent pixel size into an exact physical measurement
- avoid interpreting dirt, reflections, shadows, or normal finish differences
  as damage without sufficient visual support
- provide concise visible cues rather than chain-of-thought reasoning

The raw model score is the extractor's self-assessment of support for its own
observation. It must be a finite number from `0.0` to `1.0`, inclusive, but must
not be described or interpreted as a calibrated probability. The backend must
validate the range. The score has no runtime threshold or effect in V1 and is
persisted only for evaluation and telemetry.

## 9. Diagnostic Agent Use

### 9.1 Multimodal Input

The diagnostic agent must receive each current-turn normalized image as a real
multimodal image part. A nearby text part must identify the corresponding
artifact ID.

Artifact metadata, filenames, or IDs alone do not count as visual access.
Storage paths, bucket names, signed URLs, and provider objects must not be
exposed to the agent.

### 9.2 Observation Use

The diagnostic agent must treat structured observations as candidate evidence,
not authoritative truth. It should compare them with the pixels, the user's
description, bike-profile facts, and repair history.

The diagnostic-agent projection of a structured observation must omit the raw
model score. It may include the artifact IDs, component or area, position,
finding, visible evidence cues, visibility, and safety-relevance cue. The raw
score must not enter the diagnostic-agent prompt, context, or tool results.

The diagnostic agent must not treat extractor silence as negative evidence. It
may rely directly on visible pixel evidence omitted by the extractor and cite
the corresponding artifact, provided it communicates uncertainty and does not
claim that a hidden or unassessable condition was established visually.

When the agent sees a material conflict between the pixels and the structured
observations, it should lower confidence and do one of the following:

- request a more useful photo or measurement
- retain alternate hypotheses
- raise `contradictory_evidence` when the conflict affects safety
- complete a low-confidence report with appropriate referral when more input
  is not practical

The agent must not increase confidence merely because both model views agree on
the same image.

### 9.3 Next Actions

After considering the images and observations, the diagnostic agent may:

- ask a targeted textual question
- request a concrete photo angle or distance
- request a physical measurement or simple functional observation
- update its primary and alternate hypotheses
- raise an existing diagnostic safety flag
- save a diagnostic report when the evidence is sufficient

Photo requests should explain what must be visible. For example, “Upload a
straight-on photo of the rear caliper with the wheel installed and both pad
edges visible” is preferable to “Send another brake photo.”

## 10. Context and Reuse

Raw image pixels must be sent only as current input for the accepted turn that
submits them for diagnosis. Subsequent turns must use the durable structured
observations and artifact references rather than replaying historical images
through the model.

V1 must not provide a historical-image selection workflow, automatically reload
prior pixels, or expose an agent tool for retrieving them. When a later visual
question cannot be answered from the persisted observations, the diagnostic
agent should request a new targeted photo.

Structured observations must be durable enough to support later diagnostic
turns, report evidence summaries, evaluation, and operational review. ADK
session state must not be their sole source of truth.

## 11. Failure Behavior

Image processing must not manufacture diagnostic evidence when a dependency
fails.

| Failure | Required behavior |
|---|---|
| Artifact missing, unowned, or attached to another session | Reject before model access using existing app error behavior. |
| Image not ready | Return or record a retryable processing failure. |
| Image processing is disabled by `off` mode in a text-bearing turn | Do not decode or normalize the image; continue with text and explicitly tell the diagnostic agent that the referenced images were not inspected. |
| Image processing is disabled by `off` mode in an image-only turn | Call neither model view; emit the recoverable `image_analysis_unavailable` processing error and leave the session awaiting textual input. |
| Decode or normalization failure for some images | Exclude each invalid artifact, record a recoverable per-artifact error, tell the diagnostic agent which submitted artifacts were unavailable, and continue both configured model views with the remaining valid images. |
| Decode or normalization failure for every image in a text-bearing turn | Make no extraction call; continue with text-only diagnosis and tell the diagnostic agent which artifacts were unavailable. |
| Decode or normalization failure for every image in an image-only turn | Call neither model view; emit a recoverable processing error and leave the session awaiting a replacement upload. |
| Observation provider timeout or transient failure | Make no inline retry; continue to the diagnostic agent with the pixels when safe, mark observations unavailable, and require conservative reasoning. |
| Observation output fails schema or artifact validation | Discard the output, continue with pixels when safe, and record a validation failure. |
| Diagnostic-agent image processing failure | Record a recoverable diagnostic error and preserve the accepted turn and artifact. |

Observation extraction failure must not be presented as a successful visual
assessment. The diagnostic agent must not claim that an image was inspected
when it received neither pixels nor validated observations.

A failed extraction run may receive another provider attempt only during
explicit recovery of the same turn before diagnostic-agent processing begins.
Once the diagnostic agent has proceeded, the extraction run remains failed and
must not be retried or completed later in the background.

## 12. Safety Rules

Image-based diagnosis remains subject to `safety-diagnostic.md`. Server-owned
safety validation and state transitions remain authoritative.

The system must not treat the absence of a visible problem as proof that a
safety-critical part is safe. Photos commonly cannot establish:

- chain elongation
- bearing condition or preload
- torque or fastener tension
- exact rotor, rim, or pad thickness without a valid measurement view
- internal suspension condition
- hidden frame, fork, steerer, or carbon damage
- electrical or battery condition that is not visibly apparent

When such a fact materially affects safe guidance, the agent should request a
measurement, request a better view, lower confidence, raise an existing safety
flag, or recommend in-person inspection.

Visual evidence may support a suspected safety condition without proving its
root cause. The agent should communicate that distinction in questions,
safety messages, and reports.

## 13. Interaction With Bike-Profile Inference

Diagnostic observation extraction and bike-profile inference have different
purposes:

| Flow | Extracts | Scope |
|---|---|---|
| Diagnostic image observation | Wear, damage, leakage, contamination, alignment, missing or visibly abnormal conditions | Repair session |
| Bike-profile inference | Installed configuration, component identity, bike type, readable specifications | Bike profile and fact claims |

The flows may share private image loading and normalization implementation.
They must not share output schemas, persistence rules, or confidence policies.

A diagnostic observation such as “chain appears rusty” must not mutate the bike
profile. A profile claim such as “the bike has a chain drivetrain” must not by
itself establish that the chain is serviceable.

## 14. Testing and Evaluation

### 14.1 Deterministic Tests

Backend tests must cover:

- artifact ownership and repair-session validation
- `off` mode skipping decode and normalization while still validating artifact
  ownership, session association, and ready status
- `off` mode text-bearing fallback with explicit not-inspected image statuses
- `off` mode image-only handling with no model call and a recoverable
  `image_analysis_unavailable` error
- safe decode and normalization outcomes
- partial processing of valid images when another submitted image fails decode
  or normalization, including per-artifact error recording and diagnostic-agent
  awareness of the excluded artifact
- text-only fallback when every image is unusable but the turn contains text
- no-model handling and replacement-upload state when every image in an
  image-only turn is unusable
- preservation of artifact IDs across image parts and observations
- construction of both model views from the normalized image
- `pixels_only` delivery of normalized images to the diagnostic agent without
  creating an extraction run or calling the extraction provider
- strict structured-output validation
- rejection of missing, duplicate, or unknown per-artifact image assessments
  and of unsupported assessability or limitation values
- rejection of extraction output containing more than 12 observations
- validation and persistence of finite raw model scores in the inclusive
  `0.0` to `1.0` range, with verification that they are omitted from the
  diagnostic-agent projection
- rejection of observations that cite unknown artifacts
- extractor failure fallback to conservative pixel-based diagnosis
- replay and orchestration recovery that reuse the accepted turn's one logical
  observation run, while recording retryable provider failures as attempts on
  that run
- one normal extraction attempt, no inline extraction retry, and no late
  background retry after diagnostic-agent processing has proceeded
- prevention of historical-image selection, retrieval, and replay
- separation between diagnostic observations and bike-profile claims
- prompt construction that excludes user-authored turn text from extraction and
  retains the fixed instruction boundary
- diagnostic behavior that does not treat an omitted observation as evidence
  that a condition is absent

Tests must use fake storage and model adapters. They must not assert on exact
LLM wording.

### 14.2 Real-Image Evaluations

Model behavior must be evaluated in `evals/bike-doc` using real image inputs,
not only recorded structured responses.

The initial dataset should include:

- clear and poor-quality images
- contextual and close-up views
- front/rear ambiguity
- partially occluded conditions
- dirt, grease, glare, shadows, and reflections that resemble faults
- visible wear, damage, leakage, contamination, corrosion, and misalignment
- loose parts, packaging, screenshots, multiple bikes, and non-bike images
- images containing instruction-like or prompt-injection content
- safety-relevant cases with insufficient evidence
- conditions that require a physical measurement rather than a photo

Ground truth should come from physical measurement, confirmed repair outcome,
or qualified human review. Closely related photos of the same bike or condition
must remain in the same dataset split.

Required metrics include:

- observation precision and recall by condition category
- assessability and limitation correctness
- front/rear and installedness accuracy
- primary and alternate diagnosis accuracy
- false-safe rate for safety-relevant cases
- unnecessary safety-escalation rate
- usefulness of requested follow-up input
- latency, token usage, and model cost

The prompt-injection evaluation cases must verify that neither model view
follows instruction-like image content. The expected result is an image-grounded
bicycle observation or an image assessment explaining why no reliable
bicycle-condition observation is available, never compliance with the embedded
instruction.

The two-view flow is the V1 behavioral baseline. Evaluation may compare it with
a one-view variant to determine whether the added model inspection continues to
justify its cost and latency, but V1 does not require runtime routing between
the variants.

### 14.3 Regression Rules

Changes to the observation schema, prompt, model, preprocessing, image
resolution policy, or diagnostic image instructions require a version change
and regression comparison against the prior accepted baseline.

## 15. Rollout

Development rollout must use one static deployment setting. It must not require
user cohorts, percentage rollouts, remote flags, runtime experiment assignment,
or automatic promotion.

| Mode | Diagnostic agent receives current-turn pixels | Extraction runs and is persisted | Validated observations reach the diagnostic agent |
|---|---|---|---|
| `off` | No | No | No |
| `pixels_only` | Yes | No | No |
| `shadow` | Yes | Yes | No |
| `enabled` | Yes | Yes | Yes |

In `pixels_only` mode, the backend must supply the current-turn normalized
images to the diagnostic agent and must not create an observation extraction
run or call the extraction provider. This mode allows direct multimodal
diagnosis without the latency or cost of structured extraction.

In `off` mode, the backend must not decode or normalize images after ordinary
artifact validation. Text-bearing turns continue with explicit not-inspected
statuses in diagnostic-agent context. Image-only turns terminate without a
model call using the recoverable `image_analysis_unavailable` behavior defined
in Sections 5.1, 6, and 11.

In `shadow` mode, the backend must validate and persist the observation
extraction output, but must exclude it from the diagnostic-agent context. This
allows extraction prompt, schema, model, and preprocessing changes to be
reviewed without changing diagnostic behavior.

In `enabled` mode, the backend must supply validated observations to the
same-turn diagnostic agent as required by Section 6. Development deployments
should normally use `enabled` so real end-to-end behavior—including follow-up
questions, diagnostic reports, and safety handling—can be reviewed. `shadow`
should be used temporarily when validating a material extraction change.

Every image-bearing development turn may run the configured mode. V1 does not
require sampling, per-user assignment, or a specified promotion gate between
modes. The effective mode must be snapshotted at turn acceptance as required by
Section 6. Each accepted image-bearing turn in `shadow` or `enabled` mode must
persist one versioned logical run record containing its artifact IDs,
preprocessing, extractor, and schema versions; lifecycle status (`pending`,
`completed`, or `failed`); validated output when available; configured rollout
mode; latency; provider cost when available; and its provider-attempt count. A
schema-valid result containing zero observations is `completed`, not a distinct
lifecycle state. Section 5.6 defines the logical run, provider-attempt,
recovery, and redaction contracts.
Retries after retryable failure must update this record's lifecycle rather than
create an independent run for the same accepted turn. `pixels_only` and `off`
must not create extraction-run records.

Shadow results validate the extractor but do not establish that two-view
analysis improves diagnosis, because the diagnostic agent still sees the
pixels. The real-image evaluation suite should therefore compare the
pixels-only and two-view variants offline; V1 does not require runtime routing
between them.

High-risk visual conclusions must remain conservative until the relevant
condition category has sufficient held-out evaluation evidence.

## 16. Observability and Privacy

The backend should record:

- observation extraction started, completed, failed, and retried
- schema and artifact-reference validation failures
- observation counts and per-image assessability outcomes
- extractor and diagnostic-model versions
- preprocessing and output-schema versions
- input and output token usage when available
- extraction and total turn latency
- provider cost when available

Logs and metrics must not contain raw image bytes, base64 media, signed URLs,
storage credentials, faces, locations, license plates, serial numbers, or full
free-form model responses.

Image retention and deletion remain governed by the app-owned artifact and
privacy policies. The observation extractor must avoid returning unrelated
personal or scene information.

Visual observations are derived from their source artifacts and must not outlive
them. When an artifact is deleted or rendered inaccessible under the applicable
retention or privacy policy, the backend must delete or irreversibly redact the
observations and image assessments that cite it, and must prevent those records
from being used as diagnostic or report evidence thereafter.

## 17. Acceptance Criteria

The feature is behaviorally complete when:

- a referenced diagnostic image is safely normalized and supplied as pixels to
  the regular diagnostic agent in every mode except `off`
- `off` mode never decodes or normalizes images, never calls a model for an
  image-only turn, and never implies that a referenced image was inspected
- `pixels_only` mode performs no observation extraction call, while `shadow`
  and `enabled` supply the same normalized pixels to the fresh observation
  extractor
- the extractor returns strict artifact-backed observations and per-image
  assessability limitations
- invalid observations cannot enter diagnostic context
- the agent can request a targeted follow-up based on visual limitations
- the agent can cite image evidence in a validated diagnostic report
- structured observation failure degrades conservatively without pretending
  that the image was assessed
- later turns do not automatically resend all prior raw images
- safety behavior remains enforced by existing app-owned services
- a real-image evaluation baseline exists for the enabled visual-condition
  categories
