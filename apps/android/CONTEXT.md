# Android App Context

This context covers the Android client experience for BikeDoc.

## Scope

- Android product behavior under `docs/specs/android/`
- Android-specific navigation, session discovery, and chat UX decisions

## Key concepts

- A **bike session** is the durable product record for one guided workflow on
  one bike; it is not the same thing as a chat screen or ADK session.
- A **repair session** is the product record that tracks work for one bike
  through diagnostic, planning, and execution phases.
- An **inspection session** guides a general-condition survey without requiring
  an initial complaint and records explicit inspection coverage.
- An **inspection finding** is an evidence-backed condition observation, not a
  root-cause diagnosis.
- A **finding handoff** starts a repair session from one inspection finding
  using structured provenance rather than replaying the inspection transcript.
- **Diagnostic chat** is the Android UI for interacting with the diagnostic
  phase of a repair session.
- A session is **resumable** in the Android MVP when its `phase` is
  `diagnostic` and its `status` is one of `created`, `running`,
  `awaiting_user`, or `awaiting_decision`.
- **Bike-scoped session discovery** means listing only the signed-in user's
  repair sessions for one selected bike, newest first.

## Sources of truth

- `docs/specs/android/mvp-spec.md`
- `docs/specs/openapi.yaml`
- `docs/adr/`
