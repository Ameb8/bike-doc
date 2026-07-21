# Backend API Context

This context covers the FastAPI backend and its Google ADK-based agent backend.

## Scope

- API contracts under `docs/specs/apps/`
- Backend implementation under `apps/api/`
- Backend-specific operational and architectural decisions

## Key concepts

- BikeDoc is an AI-powered bike repair and diagnostic assistant.
- The backend is a FastAPI service.
- Google ADK powers the agent backend.
- PostgreSQL is used for persistence.

## Language

**Bike profile**:
The current resolved, user-visible description of one bike and its installed
configuration.
_Avoid_: AI profile, inferred profile

**Bike fact claim**:
An evidence-backed assertion about one canonical bike-profile field that may
support, conflict with, or supersede the current value.
_Avoid_: Suggested field, inferred-profile row

**Field resolution**:
The current value and epistemic state selected for one canonical bike-profile
field from its available claims.
_Avoid_: Last write, confirmed field

**Profile inference run**:
One idempotent, versioned attempt to extract bike fact claims from the images in
an accepted user action.
_Avoid_: Profile subagent session

## Sources of truth

- `apps/api/AGENTS.md`
- `docs/specs/apps/api.md`
- `docs/specs/apps/`
- `docs/adr/`
