# Bike Doc Transient Photo Observations MVP

Status: Draft v0.1
Last updated: 2026-06-27

This note describes a minimal MVP variant where Bike Doc accepts user-uploaded
diagnostic photos for immediate model processing, persists only derived
observations, and does not persist the original photo artifact. It is intended
as a temporary simplification before a later version adds durable artifact
storage.

## References

- Product design: `docs/specs/bike-doc.md`
- Backend scaffold: `docs/specs/apps/api.md`
- Diagnostic API delta: `docs/specs/apps/api-diagnostic.md`
- Diagnostic artifact storage spec: `docs/specs/apps/api-artifacts-diagnostic.md`
- Diagnostic report schema: `docs/specs/apps/diagnostic-report-v1.md`
- Diagnostic safety spec: `docs/specs/apps/safety-diagnostic.md`

## Goal

Support photo-assisted diagnosis in MVP without taking on full persisted
artifact storage, retrieval, retention, and deletion work.

The backend may accept a photo upload as part of a diagnostic turn, pass a safe
decoded representation to the LLM, persist model- or service-derived
observations about the photo, and then discard the uploaded binary.

## Non-Goals

- Do not persist original photos in Postgres, local disk, GCS, or another blob
  store.
- Do not implement artifact download or metadata read endpoints for transient
  photos.
- Do not implement long-term media retention, deletion workflows, or signed
  media URLs.
- Do not treat this as the final artifact architecture. Future versions may
  restore the broader `artifact_refs` model.

## Product Consequences

This MVP trade reduces backend scope, storage cost, and privacy exposure, but
it changes product behavior in important ways.

Accepted limitations:

- The system cannot later re-open the exact photo the user uploaded.
- Planning and execution phases cannot rely on original photo artifacts from
  diagnosis unless the user uploads new photos.
- Debugging and auditability are weaker because only derived observations are
  retained.
- Safety review is limited to what the backend persisted about the image rather
  than the image itself.

This is acceptable only if the MVP explicitly treats photo handling as
"transient assistive input" rather than "durable repair evidence."

## High-Level Design

Recommended MVP flow:

1. Client uploads a diagnostic photo with a turn request or an upload step tied
   to the pending turn.
2. Backend validates size, type, and decodability.
3. Backend converts the file into a trusted in-memory image representation.
4. Backend passes that representation to the diagnostic model call.
5. Backend persists only selected derived outputs, such as structured visual
   observations or report fields.
6. Backend discards the uploaded bytes and any temporary files before the
   request completes.

The photo should be treated as request-scoped data, not as an app-owned
artifact record.

## What Must Change

### 1. API Semantics

Pick one of these temporary API approaches:

- Preferred: keep photo input attached to diagnostic turn submission and do not
  expose it as a durable `ArtifactRef`.
- Alternate: keep `POST /v1/artifacts` temporarily, but redefine diagnostic MVP
  behavior so it returns ephemeral upload acceptance rather than a persisted
  artifact resource.

The cleaner approach is to avoid calling transient uploads "artifacts" at all.
That keeps the API honest and avoids implying later retrieval support.

### 2. Persistence Model

Persist only data that the application needs after the request ends. Minimal
examples:

- report-level visual observations
- assistant-visible notes derived from the photo
- safety-relevant image findings
- a small provenance flag such as `photo_input_used: true`

Do not persist:

- raw bytes
- storage bucket/path metadata
- signed URLs
- image hashes for retrieval
- filename unless there is a narrow product reason to display it

If needed, add a small structured object on the turn, event, or report side,
for example:

```json
{
  "photo_observations": [
    {
      "label": "rear derailleur appears misaligned",
      "confidence": "medium",
      "source": "llm_visual_analysis"
    }
  ]
}
```

### 3. ADK / Orchestration

The diagnostic agent boundary should change from "list persisted diagnostic
artifacts" to "receive transient image context for this turn."

Practical implications:

- The turn-processing path must be able to carry image data or normalized image
  references through the current request/background workflow.
- The agent prompt and tool contracts should stop assuming durable artifact IDs
  for diagnostic MVP.
- Any event such as `artifact.referenced` should be removed, replaced, or
  explicitly treated as a transient input event rather than a persisted
  artifact reference.

### 4. Specs And Docs

The following specs would need small updates:

- `docs/specs/bike-doc.md`
  Clarify that MVP photo input is transient and durable artifact storage is
  deferred.
- `docs/specs/apps/api-diagnostic.md`
  Update the diagnostic photo input path and response examples.
- `docs/specs/apps/api-artifacts-diagnostic.md`
  Either mark it deferred for MVP or replace it with a transient-upload note.
- `docs/specs/apps/diagnostic-report-v1.md`
  Define what persisted photo-derived observations look like.
- `docs/specs/apps/safety-diagnostic.md`
  Clarify which safety decisions may rely on transient photo analysis.

## Minimal Persisted Observation Shape

For MVP, keep persisted image-derived data small and product-oriented.

Good candidates:

- observed component or area
- notable visible issue
- confidence band
- whether the image was sufficient or insufficient
- whether safety concerns were visually detected

Avoid persisting verbose raw model narration. Persist normalized observations
that can support phase handoff and later UI display.

## Safety Requirements That Still Apply

Not storing the image does not eliminate input hardening requirements. The
backend still accepts untrusted files and must process them safely.

Minimum required controls:

- Accept only explicitly allowed image types such as JPEG and PNG.
- Enforce strict max upload byte limits.
- Reject zero-byte files.
- Decode with a trusted image library before model use.
- Reject malformed or truncated files.
- Defend against decompression bombs or pathological dimensions.
- Strip or ignore client filename path components.
- Avoid logging raw image contents, base64 payloads, or sensitive metadata.
- Ensure temporary files are deleted promptly if disk buffering is used.
- Bound request, decode, and model-processing timeouts.

## What Can Be Deferred

These can reasonably wait until durable artifact support is introduced:

- virus scanning for persisted media
- signed upload URLs
- artifact retrieval endpoints
- artifact deletion and garbage collection
- image deduplication
- EXIF retention policy
- background media processing pipelines
- bucket-level lifecycle management

For this MVP, malformed image handling, resource exhaustion protection, and
privacy discipline matter more than traditional malware scanning.

## Virus Scanning Guidance

Full antivirus scanning is not required for this minimal design if all of the
following are true:

- only narrow image MIME types are accepted
- the backend decodes images rather than treating them as arbitrary files
- uploads are not redistributed to other users
- binaries are not retained long-term

If any of those assumptions changes, reevaluate scanning.

## Recommended MVP Boundary

The smallest coherent implementation is:

- keep authenticated diagnostic turns
- allow one or more transient JPEG/PNG photo inputs
- run image validation and safe decode
- pass the photo to the diagnostic model for the current turn only
- persist normalized photo observations into the diagnostic report or turn state
- discard the binary immediately after processing

This keeps the user-visible value of photo-assisted diagnosis while deferring
the operational complexity of durable artifact storage.

## Migration Path To Persisted Artifacts Later

When the product is ready for durable artifacts:

1. Restore or complete the `ArtifactRef` persistence model.
2. Move binary storage behind the storage provider boundary.
3. Reintroduce artifact-linked events and report references.
4. Add retention, deletion, and retrieval policies.
5. Revisit stronger upload scanning and media-processing controls.

The persisted-observations MVP should be treated as a temporary stepping stone,
not as the long-term media architecture.
