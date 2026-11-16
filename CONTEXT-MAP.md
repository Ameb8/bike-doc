# Context Map

This repository is a monorepo. Read the context file for the area you are working in.

## Contexts

- `apps/api/CONTEXT.md` — Backend API and agent platform context.
- `apps/android/CONTEXT.md` — Android client experience context.

## Relationships

- Android and API share the bike-session contract; repair and inspection are
  workflows on the same aggregate. Inspection findings can seed repair sessions
  through structured finding handoff and associated evidence.

## Shared decisions

- [ADR 0001](docs/adr/0001-generic-bike-session-cutover.md) — Shared aggregate,
  coordinated cutover, workflow dispatch, and evidence associations.
