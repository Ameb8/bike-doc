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

**Bike session**:
The durable record for one guided workflow on one owned bike.
_Avoid_: Chat session, ADK session

**Repair session**:
A bike session that addresses one complaint cluster through diagnosis,
planning, and guided execution.
_Avoid_: Inspection session, chat

**Inspection session**:
A bike session that surveys the general condition of a bike without requiring
an initial complaint.
_Avoid_: General diagnostic, safety certification

**Inspection checklist**:
The immutable, versioned definition of areas and checks applicable to an
inspection session.
_Avoid_: Agent plan, prompt checklist

**Inspection check result**:
The durable, evidence-backed outcome for one applicable inspection check.
_Avoid_: Agent memory, checklist answer

**Inspection coverage**:
The complete account of which applicable inspection areas were assessed,
skipped, unavailable, or not applicable.
_Avoid_: Pass rate, safety score

**Inspection finding**:
An evidence-backed condition observation that may require monitoring,
maintenance, diagnosis, or shop assessment.
_Avoid_: Diagnosis, defect verdict

**Finding handoff**:
Structured provenance that seeds a repair session from an inspection finding.
_Avoid_: Transcript replay, copied chat

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
