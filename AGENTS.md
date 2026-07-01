# BikeDoc

BikeDoc is an AI-powered bike repair and diagnostic assistant.

## Notable Docs
- **High-Level Spec**: `docs/specs/bike-doc.md`
- **API Contract**: `docs/specs/openapi.yaml`
- **Canonical Specs**: `docs/specs/`

## Components

### App

App is Android Kotlin. always read `apps/android/AGENTS.md` if working on android app.

### Notable Docs
- **AGENTS.md**: `apps/android/AGENTS.md`
- **High-Level Spec**: `docs/specs/android/mvp-spec.md`
- **Canonical Specs**: `docs/specs/android/`


### Backend API

Backend is a FastAPI service, with Google ADK agent backend. PostgreSQL is used for data persistence. Always read `apps/api/AGENTS.md` if working in backend.

#### Notable Docs
- **AGENTS.md**: `apps/api/AGENTS.md`
- **High-Level Spec**: `docs/specs/apps/api.md`
- **Canonical Specs**: `docs/specs/apps/`

## Agent skills

### Issue tracker

Issues are tracked in GitHub Issues for this repository. External PRs are not part of the triage intake flow. See `docs/agents/issue-tracker.md`.

### Triage labels

This repo uses the default canonical triage labels: `needs-triage`, `needs-info`, `ready-for-agent`, `ready-for-human`, and `wontfix`. See `docs/agents/triage-labels.md`.

### Domain docs

This repo uses a multi-context layout with a root `CONTEXT-MAP.md` that points to per-area `CONTEXT.md` files, plus shared ADRs in `docs/adr/`. See `docs/agents/domain.md`.
