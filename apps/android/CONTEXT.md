# Android App Context

This context covers the Android client experience for BikeDoc.

## Scope

- Android product behavior under `docs/specs/android/`
- Android-specific navigation, session discovery, and chat UX decisions

## Key concepts

- A **repair session** is the product record that tracks work for one bike
  through product phases. It is not the same thing as a chat screen.
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
