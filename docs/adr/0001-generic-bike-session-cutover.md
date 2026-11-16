# ADR 0001: Generic bike-session cutover

Status: Accepted

## Context

Repair and guided inspection are distinct workflows on an owned bike, but share
conversation and execution infrastructure. The existing implementation names
that infrastructure after repair. The [inspection spec](../specs/inspection-sessions.md)
(especially §§5.2, 5.7, 13, 14.1, 16.3 and 17.1) defines the target contract.
There is no production or important data and no released compatibility contract
for the development-only repair-named schema and routes.

## Decision

`BikeSession` is the shared aggregate. It owns the user and bike association,
workflow, phase, public status, safety state, current input request, event
sequence, idempotency metadata and timestamps. Shared infrastructure owns
turns, phase sessions, events, artifacts, reports and background execution.
Phase reports are associated by session and phase, rather than by fixed nullable
report columns for each workflow. Workflow-specific records own their state:
inspection checklist version and applicability, results, findings and report
derivation stay in inspection; diagnostic rules stay in repair. The shared
aggregate and conversation module do not encode either workflow's rules or
acquire nullable columns for each workflow.

The shared conversation module owns session loading and ownership, session and
turn idempotency, transaction control, durable turn and event allocation,
artifact validation, replay and SSE. A static application registry selects
one of the shipped repair and inspection adapters from the persisted workflow.
The adapter validates workflow-specific turn and phase/status rules, ensures
phase state, identifies the typed turn job and determines settled status.
The shared module records that job through the existing background-job interface;
it neither publishes nor executes it. The registered job kind selects the
worker handler. Callers and routes do not select or directly import agents,
workers or transport adapters. PostgreSQL owns product state and accepted work;
JetStream carries execution notifications. Inspection uses the existing
model-intensive worker runtime, not a dedicated deployment or dynamic plugins.

An artifact is immutable bike-owned evidence. Upload validates the owned
session and workflow purpose and creates its initial session association. The
steady-state model has one artifact record and a unique association per
`(artifact_id, bike_session_id)`, with creation time and `uploaded` or
`finding_handoff` provenance. Association does not change purpose, bike
ownership, content hash or storage identity. Turn acceptance checks ownership,
association, availability and applicable purpose and image type.

A finding handoff creates a repair session from exactly one eligible finding
in a completed inspection's durable report. The finding must allow
`start_diagnostic`; only ready images cited directly by it are eligible. The
transaction creates the repair session, immutable structured origin and its
artifact associations together, with at most one repair per inspection finding.
It copies neither image bytes nor the inspection transcript. Diagnostic context
receives the finding, supporting observations and eligible evidence references.

## Cutover and migration assumptions

The cutover is coordinated across persistence, API and Android: `bike_sessions`
replaces repair-named shared persistence, public IDs use `ses_`, and
`/v1/sessions` replaces `/v1/repair-sessions`. Shared records and public parent
references use bike-session terminology; workflow-specific report types remain.
The bike projection changes from `has_repair_sessions` to `has_sessions`.
Existing development data may be replaced, not preserved. Migrations must still
be explicit, reviewable and deterministic. As permitted by §16.3, a temporary
app-owned reference to the same immutable stored object may be used during the
cutover if migration constraints require it, with explicit ownership and
provenance and no duplicated bytes; it is not the steady-state model.

## Consequences and alternatives

Backend and Android must move together, and existing development clients and
data cease to work across the breaking change. Implementations must preserve
repair behavior through the generic infrastructure while adding inspection.
Workflow logic remains independently owned, while shared acceptance and execution
semantics have one implementation.

We reject dual reads, dual writes, compatibility aliases and data backfill:
they add ongoing ambiguity and complexity for an unreleased development model.
We also reject making repair-named storage the shared aggregate, embedding both
workflows' rules and nullable state in it, copying evidence bytes for handoff,
and creating an inspection-specific transport or worker runtime.
