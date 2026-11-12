# Conversation History And Cross-Device Resume

Status: Implementation spec; not yet implemented
Last updated: 2026-11-12

## Purpose

A signed-in user can open a previous repair session on any device, read their
own messages and BikeDoc's replies, view retained photos and reports, and
continue the repair when its current workflow permits input. Opening history
must not start another agent execution.

This feature extends the existing FastAPI, PostgreSQL, artifact-storage, and
SSE implementation. It requires no new backend service or synchronization
platform.

## References And Contract Changes

- [Product behavior](../bike-doc.md)
- [Public API contract](../openapi.yaml)
- [Backend architecture](../../../apps/api/ARCHITECTURE.md)
- [Diagnostic API](api-diagnostic.md)
- [Events and SSE](api-events-diagnostic.md)
- [Artifact storage](api-artifacts-diagnostic.md)
- [Authentication](api-auth-dev.md)
- [API errors](api-errors.md)
- [Android MVP](../android/mvp-spec.md)

This document specifies planned changes. It does not claim that the routes or
fields below already exist. During implementation, update `openapi.yaml`, the
affected feature specs, and client contracts together. OpenAPI remains the
canonical wire contract.

In particular, Android MVP sections 4.4 and 5.3 currently restrict discovery
to the first page and navigation to resumable diagnostic sessions. Sections
5.5 and 5.6 prescribe rebuilding history exclusively from `events?after=0`,
which cannot recover user messages from historical `turn.started` payloads.
Implementing this feature must replace those rules with the discovery and
snapshot flows below. Keep the bike-first entry; an account-wide history
screen is not required for the first client release.

## Scope And Decisions

| Decision | First implementation |
|---|---|
| Conversation identity | One existing repair session, owned by one app user and associated with one bike. |
| Durable truth | Existing product turns, events, session state, reports, and artifacts in PostgreSQL/private storage. |
| Transcript storage | A backend query combining existing records; no separate messages table or asynchronous projection. |
| History transport | A paginated JSON transcript snapshot. |
| Live transport | Existing persisted SSE stream, extended additively. |
| Concurrent use | Multiple readers; at most one active agent turn per repair session. |
| Device storage | One pending send in screen saved state; optional cache and other local drafts. Server records remain the source of accepted history. |
| Session discovery | Owner-scoped list, optional bike filter, real cursor pagination. |
| Photo delivery | Authenticated backend content route using the existing storage provider. |
| Retention | Retain history and media for the life of the repair session; preserve current ADK retention protections. |

Out of scope: offline submission queues, synchronized drafts/read positions,
message edits/deletion, branching conversations, full-text search, generated
conversation titles, shared accounts/collaborators, a new account deletion
workflow, and new planning/execution agents. Future features can extend the
public transcript interface without exposing its storage implementation.

## Ownership And Identity

All reads and writes resolve the bearer identity through the existing auth
module to an app-owned user. Signing into the same account on another device
must resolve to the same user ID. Device IDs and email strings are not history
ownership keys. This feature does not introduce identity-provider linking.

Missing and unowned sessions, bikes, reports, and artifacts return
`404 not_found`; missing or invalid authentication returns `401 unauthorized`.
Every cursor request repeats ownership checks. Client caches must be scoped
to the app user and cleared or made inaccessible on sign-out/account change.

## Session Discovery

Extend the existing route:

```http
GET /v1/repair-sessions?bike_id=...&status=...&limit=20&cursor=...
```

- Make `bike_id` optional. Omission lists only the authenticated user's
  sessions across bikes. A supplied bike must be active and owned, matching
  the existing bike-specific behavior.
- Preserve optional `status`, the default limit of 20, maximum of 100, and
  `{items, next_cursor}` response envelope.
- Implement keyset pagination using `(created_at, id)` descending. This
  deliberately preserves today's ordering and avoids a new activity column
  or heartbeat-driven ordering. Clients must not call this "recently active."
- The opaque, versioned cursor contains the ordering position and filter/user
  scope. Reject malformed cursors or changed filters with `422 validation_error`.
  Cursor contents never authorize access; always apply owner predicates.
- New sessions appear on refresh of the first page. No total count or frozen
  historical list snapshot is required. Sessions changing status may enter or
  leave a filtered list; clients merge pages by session ID.

Keep the existing `RepairSession` item shape. Titles, previews, and an activity
sort can be added later if the product needs them.

### Android Entry And Navigation

Keep the existing Home -> bike selection -> session chooser entry. Label the
entry "Repair conversations" so users can find completed history as well as
ongoing work. Include bikes with any owned repair session, regardless of phase
or status. Do not hide a bike because its first session page has no resumable
diagnostic session.

Every session row opens its transcript, including completed, cancelled, failed,
blocked, and non-diagnostic sessions. Reuse the chat screen as a transcript
viewer with a disabled composer when `interaction.can_submit_turn` is false;
this does not implement agents or interactive controls for other phases.
Use "View conversation" for the row action. The existing resume shortcut may
target the newest diagnostic session that appears resumable from its list
metadata, labelled "Continue repair", but the loaded snapshot always decides
input eligibility. Running sessions can be opened to follow existing work.

The chooser must offer "Load more" while `next_cursor` exists and merge rows
by session ID. Likewise, offer "Load older messages" in the transcript and
preserve the visible scroll position when prepending items. Do not retain the
old first-page-only rule. Bike discovery must also follow its existing list
cursors so bikes with history remain reachable. No new global history screen
or session-preview query is needed.

## Transcript Snapshot

Add:

```http
GET /v1/repair-sessions/{sessionId}/messages?limit=50&cursor=...
```

Default limit: 50; maximum: 100. The first request returns the newest page,
with items ordered oldest to newest for rendering. `next_cursor` requests an
older page, also returned oldest to newest.

The initial response has this logical shape; names are normative:

```text
ConversationHistoryPage
  session: RepairSession
  interaction: SessionInteraction
  active_safety_flags: SafetyFlag[]
  items: ConversationMessage[]
  active_reply: ActiveReply | null
  event_cursor: string
  state_event_id: string
  next_cursor: string | null
```

All initial-response fields must come from one short, read-only PostgreSQL
REPEATABLE READ transaction. `event_cursor` is the session's latest committed
event sequence in that snapshot, encoded using existing decimal SSE cursor
semantics; use `"0"` for an empty log.

`state_event_id` is the latest non-heartbeat event sequence at that snapshot,
or `"0"`. It is a submission precondition, not an SSE replay cursor.

### Message Shape And Mapping

| Field | Meaning |
|---|---|
| `id` | Stable public identity within the session. |
| `turn_id` | Associated accepted turn, or null for a session annotation. |
| `client_turn_id` | Original client ID for user messages; null otherwise. |
| `role` | `user`, `assistant`, or `system`. |
| `text` | Public text, nullable for an attachment-only user message. |
| `artifact_ids` | Retained attachment references; empty when absent. |
| `sequence` | Ordering position in the session's event sequence. |
| `created_at` | Stored timestamp of the source turn or event. |
| `display_safety_level` | Existing assistant display level; null otherwise. |
| `kind` | `message`, `error`, or `phase_transition`. |
| `error` | Existing public `ErrorEventData` for an error item; null otherwise. |
| `from_phase` / `to_phase` | Transition phases for a phase-transition item; null otherwise. |

Mapping rules:

- Each accepted `repair_turns` record produces one user message, using the
  turn ID as `id`, its `start_event_sequence` as `sequence`, and its stored
  normalized message as content. Preserve attachment-only messages.
- Each `assistant.message.completed` event produces one assistant message,
  using its existing `message_id`, full text, attachments, safety level, and
  event sequence. Final full text is authoritative; do not append it to deltas.
- Persisted `error` and `phase.transitioned` events produce system items with
  IDs `event:<sequence>`. Error text uses the existing public error message;
  `error` preserves its `code`, `message`, `retryable`, and nullable `artifact_id`.
  Legacy missing optional error fields default to `retryable: false` and
  `artifact_id: null`. Phase transitions use a deterministic client-localized
  description of `from_phase`/`to_phase`; their `text` is null. Use the same
  mapping for live events so history and SSE produce the same IDs and metadata.
- Heartbeats and assistant deltas are not transcript items. Input requests,
  safety state, and report links retain their existing structured interfaces;
  they must not be invented as duplicate assistant messages.
- IDs are opaque to clients; merge using `(session_id, id)`. Sequence, not
  wall-clock time, determines display order. The listed sources have distinct
  sequence positions; preserve multiple completed assistant messages per turn.

The query performs filtering and pagination in the database. It must not load
the entire event log and paginate in Python, consult ADK storage, or call a
model. Add targeted indexes only where query plans show they are needed.

### In-Progress And Interrupted Replies

`active_reply` is separate from finalized transcript items:

```text
ActiveReply
  turn_id: string
  text: string
```

An active turn is the latest accepted turn without a corresponding terminal
`turn.completed` event. Determine this from product records, not solely from
session status: a tool may request input before orchestration has finalized.
When a turn is active, derive its unfinished text from ordered persisted
`assistant.delta` events after that turn's latest
`assistant.message.completed`, bounded by `event_cursor`. If none exist,
return an empty string and show a working indicator. Return null if no turn
is active. Include this information regardless of the message page limit.

Subsequent deltas extend this draft. The next completion replaces it with the
canonical message ID and full text. A terminal turn without completion clears
the draft; retain its accepted user message and any persisted error annotation.
The first implementation does not archive unfinished assistant text as a
completed reply. Existing durable-job recovery governs abandoned running
turns; opening history does not retry them.

### Older Pages

The older-page cursor includes the session/user scope, first response's
`event_cursor`, and exclusive `before_sequence`. Each older page uses the
same event upper bound. Accepted user messages are included only when their
start sequence is within it. Fetch one extra candidate to determine whether
`next_cursor` exists.

Older-page responses retain that original `event_cursor` and return null for
`session`, `interaction`, `active_safety_flags`, `state_event_id`, and
`active_reply`; declare those fields nullable in OpenAPI. They load older items
only and never replace live state. A fresh request without a cursor starts a
new snapshot.

These are position cursors, not long-lived database snapshots. Immutable
source rows and the sequence upper bound keep transcript pagination stable
while new messages arrive. Invalid or cross-session/user cursors return
`422 validation_error` after checking access to the requested session.

## Live User Messages And Reconnection

Extend `TurnStartedEventData` with an optional `user_message` containing the
same user `ConversationMessage` shape as history. Preserve `turn_id`, `phase`,
the `turn.started` event type, its sequence, and `TurnAccepted.start_event_id`.

New acceptances must populate `user_message` in the same transaction as the
turn, session state, and durable jobs. Notify SSE readers only after commit.
Idempotent replay returns the original acceptance without adding another
event or starting work again. This avoids a new event taxonomy and event-type
constraint migration while allowing existing consumers to ignore the field.

Historical `turn.started` events may omit `user_message`; do not rewrite or
renumber them. Transcript reads obtain those user messages from `repair_turns`.
Clients receiving a legacy event without content refresh the snapshot if they
need to reconcile a newly observed turn.

Extend `TurnCompletedEventData` additively with optional `interaction` using
the same `SessionInteraction` shape as the snapshot. New completions populate
it from the shared eligibility policy in the transaction that finalizes the
turn and persists its terminal event. Evaluate the policy with that turn
finished. Historical completion events may omit it and are not rewritten.

Client entry and reconnect algorithm:

1. Load the first transcript snapshot and render its session, interaction,
   active safety flags, messages, and active reply.
2. Open existing SSE with `after=event_cursor`. Events committed during the
   HTTP-to-SSE gap are replayed from PostgreSQL.
3. Upsert `turn.started.user_message` and assistant completions by message ID.
   Reconcile optimistic user messages using `client_turn_id`. Apply deltas
   only once per event ID to the active turn's unfinished reply.
4. Apply existing input, safety, report, transition, and terminal event behavior.
   Advance `state_event_id` on every non-heartbeat event. `turn.started` sets
   running, disables submission, and clears the old input request immediately
   on every device. Apply the eligibility rules below on turn completion.
5. Advance the reconnect cursor only after applying each event. Reconnect from
   it on timeout, disconnect, or process/network interruption.
6. Merge older pages into the transcript without replacing live session state.

A client may retain a processed event cursor only together with the local
message/draft/state it represents. After losing that state, fetch a fresh
snapshot. A cursor alone cannot rebuild prior messages. Replayed or stale
events must never regress newer state. Unknown event types remain ignorable
for forward compatibility.

For the first client release, refresh session discovery on entry/foreground;
an account-wide live event subscription is unnecessary.

On successful submission, also reconcile the optimistic message using
`TurnAccepted.turn_id` and `start_event_id`. This is necessary when a sending
client subscribes after the start event rather than receiving it over SSE.
Do not let that acceptance response overwrite state already advanced by SSE.

## Viewing And Continuing

The snapshot exposes:

```text
SessionInteraction
  can_submit_turn: boolean
  reason: null | running | awaiting_decision | blocked_safety |
          completed | failed | cancelled | phase_not_supported |
          diagnostic_complete
```

For the current diagnostic implementation, allow submission only in diagnostic
phase, with status `created` or `awaiting_user`, without an existing diagnostic
report, an active turn, or a blocking safety policy. A completed diagnostic
report makes the diagnostic transcript read-only even if legacy status still
says awaiting user.
Choose the reason in this order: terminal status (`completed`, `failed`, or
`cancelled`), safety blocking, unsupported phase, diagnostic completion,
running status or active turn, then `awaiting_decision`. Return null reason
only when input is permitted. The same policy must govern acceptance under
the session lock.

### Safety Context And Submission Policy

The initial snapshot's `active_safety_flags` comes from the session's existing
reconciled flags, using the public `SafetyFlag` shape; return an empty array
when none exist. Render the current safety banner from `session.safety_state`
and these flags' messages before subscribing to SSE, even when no report has
been produced. Subsequent `safety.escalated` events replace the active flag
list with their reconciled `safety_flags` and update the banner. Loading older
messages must not replace current safety context. No new safety table or
historical-event replay is needed to restore it.

For submission eligibility, "blocking safety policy" means session status
`blocked_safety` or safety state `blocked`. Either disables diagnostic
submissions, including photos, with reason `blocked_safety` unless a terminal
status takes precedence.
`caution` and `shop_recommended` alone do not prohibit further diagnostic
answers or photos when the other workflow conditions allow them.
`blocks_repair_instructions` continues to restrict the generated repair
guidance; a warning with that field set does not by itself block diagnostic
clarification. This feature adds no safety override or acknowledgement flow.

### Live Eligibility

The backend owns the eligibility policy; Android does not reproduce the full
policy to decide when submission becomes permitted. Apply a current
`turn.completed.interaction` together with its session snapshot. An initial or
refreshed transcript snapshot can also enable input. A legacy completion
without `interaction` triggers a fresh snapshot; keep submission disabled
until it arrives. Do not allow a delayed snapshot to overwrite newer SSE
state: use its `state_event_id` to detect that race and refresh again if needed.

Between snapshots/completions, `turn.started` disables input with reason
`running`. Safety blocking, report completion, phase transitions, and terminal
session states may further restrict input as they arrive. `input.requested`
may update the visible question but cannot enable submission while a turn is
active. An `error` event does not end a turn or re-enable submission; wait for
`turn.completed`. Older or duplicate events never re-enable input. These
rules reuse existing events and require no separate eligibility event stream.

This object describes workflow eligibility, not agent-storage or provider
health. History reads must not perform ADK I/O. Continue the existing phase
binding on the next accepted turn; never recreate, rebind, or replay a visible
transcript into a confirmed-missing bound ADK session. Retain the existing
safe `diagnostic_session_unavailable` processing error. Keep history readable
and offer refresh or a new repair session, without automatically resending the
failed turn. Read failures from unavailable PostgreSQL remain ordinary server
failures, not empty history.

There is no resume mutation endpoint. Viewing fetches history; continuing uses
the existing turn endpoint. Completed/cancelled/failed sessions stay readable
and are not reopened. A new repair uses the existing create-session flow.

## Concurrent Devices And Stale Submissions

Preserve the current session row lock, one-running-turn rule, durable job
acceptance, and `(session_id, client_turn_id)` idempotency.

Add optional `expected_state_event_id` to `TurnCreate`. New clients must send
their latest processed `state_event_id`; old clients may omit it during rollout.
When supplied, require a canonical nonnegative decimal sequence string;
malformed values return `422 validation_error`.

Under the session lock, compare the supplied value with the latest committed
non-heartbeat event sequence. A mismatch returns
`409 session_state_conflict`; the client refreshes the snapshot and retains
the unsent draft. Do not silently apply an answer to a newer question.
Heartbeats must not cause conflicts.

Check existing client-turn idempotency before this precondition. A retry of
an already accepted request returns its original result even if the session
has advanced. Exclude the new precondition field from the semantic request
hash so existing stored hashes remain valid. Reusing a client-turn ID with
different message content remains `409 idempotency_conflict`.

Continue validating `responds_to_input_request_id`; a stale supplied request
ID becomes `409 session_state_conflict` rather than the current `404`. New
clients include it when answering a specific current input request. Requests
without the new precondition retain existing compatibility behavior but must
still pass the authoritative workflow, safety, and row-lock checks.

Check active-turn existence under that lock as well as status; an input-request
tool changing status to awaiting user must not allow a second turn before the
first has a persisted terminal event. The client may display that request
immediately but enables submission only after the active turn ends.

### Pending Sends And Retry

Generate one `client_turn_id` when the user chooses to send, before making the
POST. Keep one pending request per open session, including its text, uploaded
artifact IDs, input-request ID, and `expected_state_event_id`. Scope it to the
signed-in app user and session. Save this small record in Android screen saved
state (`SavedStateHandle`) for normal process recreation; no local database,
offline queue, or background sender is required. Clear it on sign-out/account
change. Do not replace an unresolved pending request with a new send. Recovery
after force-stop or uninstall is not guaranteed.

- On a timeout, connection loss, or other outcome that does not confirm
  acceptance or rejection, show "Delivery unconfirmed" and retain the request.
  Refresh history and reconcile using `client_turn_id`. If it remains
  unresolved, an explicit "Retry send" resends the exact request, including
  its original ID and precondition. Never mint a new ID or silently advance
  its precondition for this retry. Resolving that existing attempt is allowed
  even while the composer is disabled; server idempotency checks run first.
- HTTP acceptance, `turn.started.user_message`, or a snapshot user message
  matching `client_turn_id` confirms delivery. Merge into one accepted message
  and clear the pending record. A later HTTP failure or old acceptance response
  must not revert delivery or session state already confirmed by SSE/snapshot.
- An explicit `409 session_state_conflict` confirms rejection of that attempt.
  Retire its ID, refresh the snapshot, and restore the text and attachment IDs
  to the unsent draft. Require the user to review and send again when eligible;
  that new send gets a new ID and the current precondition/input-request ID.
  Other definitive rejection responses likewise preserve editable content;
  `idempotency_conflict` must not trigger an automatic retry under a new ID.
- On process recreation, load a fresh snapshot before offering retry for a
  restored pending request. If its message is outside the newest page, the
  same-ID POST retry can still resolve acceptance. Reconnection, restoration,
  or merely opening history must never resend automatically. Optional caching
  of other unsent drafts remains separate from this one pending record.

An accepted turn's processing error is not a delivery failure. Retain its
accepted user message and structured error annotation. `error.retryable` is
informational and never automatically retries agent execution. If interaction
permits, the user can compose and submit a new turn with a new ID. For
`diagnostic_session_unavailable`, offer refresh or starting a new repair
session; do not offer a button that resends the old accepted turn. The same
error presentation and actions apply to live and reopened conversations.

## Retained Photos And Reports

Implement the already-declared metadata operation and add content retrieval:

```http
GET /v1/artifacts/{artifactId}
GET /v1/artifacts/{artifactId}/content
```

Metadata returns `ArtifactRef`. Content returns the stored image bytes with
validated MIME type, inline disposition, and `Cache-Control: private, no-store`.
Use the existing asynchronous storage provider `get_object`; do not expose
storage paths, buckets, local file URLs, or bearer tokens in image URLs.
Clients fetch content with the normal authorization header. This works with
both local storage and GCS without a signed-URL subsystem.

Verify artifact ownership and access to its parent repair session/bike before
reading storage. Never treat knowledge of an artifact ID as authorization.
Missing metadata, unowned resources, and confirmed-missing bytes return
`404 not_found`; a retained artifact not yet ready returns
`409 artifact_not_ready`. Provider outages return generic `500 server_error`.
Show an unavailable-image placeholder without hiding the surrounding message.
Fetch images lazily; thumbnails, range requests, and media CDN delivery are
future optimizations.

Continue using existing report list/read operations and versioned report
schemas. History loading must not regenerate reports or reprocess photos.

## Storage, Retention, And Legacy Data

- No new conversation/message table, sync worker, or per-device cursor table.
  Add only supporting indexes demonstrated by the actual read queries.
- Preserve accepted user messages and completed assistant replies for the
  repair session's life. Do not add automatic TTLs or event compaction here.
- Retain referenced photo bytes with their session. Preserve existing ADK
  deletion guards while retained phase references exist; this feature does
  not weaken those guards to implement a shorter agent retention period.
- Do not delete server history on logout, app uninstall, or device change.
- Read historical turns and completion events directly; no transcript backfill
  job is needed. Missing optional fields use documented defaults. Corrupt
  required persisted data is a server error, not silently omitted history.
- History GETs have no workflow mutations, model calls, or automatic retries
  of agent execution. They do not expose prompts, tool traces, ADK IDs, or
  internal model configuration.

## Implementation Placement And Sequence

Keep the existing direction: routes -> services -> repositories -> models/db.
The transcript service owns public mapping and snapshot coordination; the
repository owns ordered bounded queries. Reuse existing event delivery and
artifact services rather than introducing another orchestration framework.

1. Update OpenAPI with snapshot/message/interaction models, pagination rules,
   active safety flags, structured message errors, optional turn fields,
   additive start/completion event data, artifact reads, and errors.
   Update affected API/event/artifact and Android specs to match. In particular,
   replace replay-only history, first-page-only discovery, untappable closed
   sessions, optional historical-photo rendering, and stale-input `404` rules.
2. Implement owned transcript reads, snapshot consistency, legacy mapping,
   active-reply reconstruction, and real session-list pagination.
3. Extend atomic turn acceptance with the live user payload, shared interaction
   policy, and optional stale-state precondition. Include eligibility in turn
   completion events without changing job scheduling or recovery behavior.
4. Add authenticated artifact metadata/content reads using existing storage.
5. Update client repositories/DTOs and chat state reduction for snapshot entry,
   message deduplication, pending sends, draft reconciliation, older pages,
   photos, and read-only sessions. Update the bike/session chooser navigation
   and pagination. Preserve the existing ViewModel-owned SSE lifecycle.
6. Deploy backend before requiring the new client flow. New clients must wait
   for backend support; old clients continue using existing operations during
   rollout. Update affected architecture docs as implementation responsibilities
   change; do not describe proposed behavior as already implemented.

## Contract Examples

### Opening Completed History

1. A bike's first page contains 20 sessions. "Load more" retrieves the next
   page, including a completed session; its "View conversation" action opens
   the same transcript viewer used for ongoing work.
2. The snapshot returns `interaction: {can_submit_turn: false, reason:
   completed}`, `active_reply: null`, retained messages, and report IDs in
   `session.latest_reports`. Render history and report links with input disabled.
3. "Load older messages" prepends the next message page without changing
   eligibility. Reading history starts no agent work and does not reopen the
   session. A separate "New repair" action uses the existing creation flow.

### Joining Mid-Reply After A Safety Escalation

1. Device B opens a snapshot at `event_cursor: "40"` with an active reply for
   turn `turn_1`, text `"Please inspect"`, and interaction reason `running`.
   It includes the active warning flag raised before event 40, so B displays
   its message immediately without replaying old safety events.
2. B subscribes after 40. Event 41 is `assistant.delta` with text `" the rim."`;
   the draft becomes `"Please inspect the rim."`. Event 42 completes message
   `msg_1` with that full text; B replaces the draft with one finalized message.
3. Event 43 is `input.requested`. B shows the question with submission disabled.
   Event 44 is `turn.completed`, with status `awaiting_user` and interaction
   `{can_submit_turn: true, reason: null}`. B enables input. A warning may
   restrict repair instructions while allowing this diagnostic clarification;
   a blocking safety state would instead keep submission disabled.

### Losing The POST Response While SSE Confirms Acceptance

1. Device A saves a pending request with `client_turn_id: "send-1"` and
   `expected_state_event_id: "44"`, then POSTs it. The server accepts turn
   `turn_2` and persists `turn.started` at sequence 45, but the HTTP response
   is lost.
2. A receives event 45 with `user_message.client_turn_id: "send-1"`. It merges
   the optimistic bubble into the accepted message with ID `turn_2`, clears
   the pending request, and keeps input disabled while the turn runs. The
   later HTTP timeout does not mark that message failed.
3. If SSE is also unavailable, A retains the pending request as unconfirmed.
   An explicit retry sends the identical request with ID `"send-1"` and
   precondition `"44"`. Idempotency returns `turn_2` even though the session
   has advanced; there is one user message and one diagnostic job.

## Verification And Acceptance Criteria

Use deterministic service/route tests and real PostgreSQL integration tests
for locking and snapshot races; no live model calls are required.

- A message accepted on device A appears once on device B, both when B is
  already connected and when B opens the conversation later. Both devices
  display the same user text, completed assistant text, and attachment IDs.
- Another signed-in user's IDs/cursors cannot reveal sessions, messages,
  artifacts, or reports. Signing out and switching accounts hides cached data.
- Existing pre-feature turns are readable without rewriting events. Include
  attachment-only input, empty sessions, several assistant messages in a turn,
  errors, phase transitions, and legacy optional event fields.
- More than 100 sessions/messages remain reachable through pagination.
  Appends during transcript pagination do not lose or duplicate older items;
  loading old pages does not revert current input/safety/session state.
  Android can navigate to terminal and non-diagnostic history from later
  chooser pages and preserves scroll position when older messages load.
- An event committed between snapshot loading and stream subscription appears
  exactly once after merging. Joining during a partial reply reconstructs the
  stored prefix and merges the final message without duplicated text.
- Duplicate SSE delivery, normal stream timeout, lost local state, and app
  restart recover using the specified cursor/snapshot rules.
- Concurrent submissions accept one turn and one diagnostic job. Stale new
  submissions conflict; heartbeat-only changes do not. Retry after a lost
  acceptance response returns the original turn/job even with a stale token.
  Android reuses the pending request ID across retry/process recreation,
  reconciles SSE acceptance before a late HTTP failure, and preserves the
  editable draft after explicit rejection without automatically resending.
- A tool persisting an input request before turn finalization does not enable
  another submission on either device until the terminal event is committed.
  Errors do not enable input early; a legacy completion without eligibility
  refreshes the snapshot before enabling submission.
- Running sessions attach to existing work; viewing never enqueues work.
  Completed, blocked, unsupported-phase, and report-complete diagnostic
  sessions are readable and enforce the same submission policy on the server.
- A second device restores active safety messages before any report exists.
  Warning flags can allow diagnostic clarification while restricting repair
  guidance; blocked safety prevents submission on the client and server.
- Reopened error items preserve public codes, retryability, and artifact IDs
  with legacy defaults. Live and historical errors offer the same actions and
  never automatically retry an accepted turn's agent execution.
- Missing bound ADK state preserves history and produces the established safe
  error without recreating the session. An interrupted reply is not presented
  as a finalized assistant answer.
- Historical images load from a second device with authorization. Missing
  bytes display a placeholder; storage outages do not erase transcript text.
- Query inspection confirms database-side limits/keyset predicates and short
  snapshot transactions; normal history loading does not replay every delta
  and heartbeat for every completed turn.

Run the repository's required backend/client checks for the implementation
areas changed. The spec-only change does not require application test runs.
