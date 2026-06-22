# Bike Doc Diagnostic DB Schema Spec

Status: Draft v0.1
Last updated: 2026-06-21

This spec defines the application-owned Postgres schema needed for the
diagnostic vertical slice. It is scoped to the public contract in
`docs/specs/openapi.yaml` and the backend ownership boundaries in
`docs/specs/apps/api.md`.

The schema is concrete enough to write one Alembic migration without choosing
column types, constraints, delete behavior, indexes, cursor semantics, or enum
storage strategy.

## References

- Backend scaffold: `docs/specs/apps/api.md`
- Diagnostic API delta: `docs/specs/apps/api-diagnostic.md`
- Diagnostic event and SSE semantics:
  `docs/specs/apps/api-events-diagnostic.md`
- Public API contract: `docs/specs/openapi.yaml`
- Design prompt used as guidance: `db-prompt.md`

## Scope

Diagnostic-slice application tables:

- `users`
- `bike_profiles`
- `repair_sessions`
- `repair_phase_sessions`
- `repair_turns`
- `repair_session_events`
- `artifact_refs`
- `phase_reports`

`repair_phase_sessions` is included in addition to the minimum table list
because `api.md` requires one ADK session per product phase, while product
state must remain app-owned. This table stores the app's mapping from a repair
session phase to an opaque ADK session/archive identifier.

Out of scope for this migration:

- ADK-owned session tables.
- Repair history tables.
- Tool catalog, price lookup cache, and repair reference lookup tables.
- Binary artifact objects; this spec stores only artifact metadata and storage
  object references.

## Global Rules

### ID Strategy

All application resource primary keys are app-generated text IDs using one
consistent strategy: prefixed ULID strings generated in Python before insert.
Do not use Postgres sequences, UUID extensions, or bare integer public IDs.

Required prefixes:

| Table | Primary key prefix |
|---|---|
| `users` | `usr_` |
| `bike_profiles` | `bike_` |
| `repair_sessions` | `rs_` |
| `repair_phase_sessions` | `phs_` |
| `repair_turns` | `turn_` |
| `repair_session_events` | `evt_` |
| `artifact_refs` | `art_` |
| `phase_reports` | `rpt_` |

`repair_session_events` is the only table where the internal primary key is
not the public API `id`. The OpenAPI SSE example makes event `id` and
`sequence` identical, so the API layer must serialize
`RepairSessionEvent.id = sequence::text`. `repair_session_events.id` remains an
internal `evt_...` key for foreign-key targets and debugging.

### Timestamps

Use `timestamptz` for every timestamp. Store UTC instants; application code may
render them with `Z` for JSON responses.

Use database defaults for creation timestamps:

```sql
created_at timestamptz NOT NULL DEFAULT now()
```

Mutable tables also have:

```sql
updated_at timestamptz NOT NULL DEFAULT now()
```

The migration must install one shared trigger function, for example
`set_updated_at()`, and attach it to mutable tables:

- `bike_profiles`
- `repair_sessions`
- `artifact_refs`

Append-only tables do not have `updated_at`:

- `repair_turns`
- `repair_session_events`
- `phase_reports`

### Enum Storage

Store OpenAPI enum values as `text` with `CHECK` constraints. Do not use native
Postgres enum types. The stored values must match the OpenAPI wire values
exactly.

This keeps migrations simple while the product contract is still moving and
avoids a mapping layer between database values and JSON responses.

### JSONB Rules

Use `jsonb` for fields that are structured by the API contract but are not
queried as first-class relational entities in the diagnostic slice:

- `repair_sessions.current_input_request`
- `repair_sessions.execution_progress`
- `repair_sessions.active_safety_flags`
- `repair_turns.message`
- `repair_session_events.data`
- `phase_reports.safety_flags`
- `phase_reports.source_artifact_ids`
- `phase_reports.payload`

Add `jsonb_typeof` checks for arrays and objects where the shape is known.
Detailed schema validation remains in Pydantic/service code against the
OpenAPI-derived schemas.

### Idempotency Hashes

For optional or required client idempotency keys, store a canonical request
hash alongside the client key:

- `repair_sessions.request_hash`
- `repair_turns.request_hash`
- `artifact_refs.request_hash`

The hash is lowercase hex SHA-256 of the canonicalized request fields that
define semantic equality for the operation. On key reuse, services compare the
stored hash:

- same key and same hash: return the original response
- same key and different hash: return `409 Conflict` with
  `error.code: idempotency_conflict`

For `artifact_refs`, semantic equality must include the uploaded file's
content, not only the multipart form fields. Compute `request_hash` over
`purpose`, `repair_session_id`, `bike_id`, `filename`, and `content_sha256`
(the file's own content hash, stored separately in
`artifact_refs.content_sha256`). A retried upload with the same
`client_artifact_id` but a different file body must therefore produce a
different `request_hash`, triggering `409 idempotency_conflict` — this is
what makes the diagnostic delta spec's "same `client_artifact_id` reused with
different ... content" case actually detectable.

## Tables

### `users`

Application user profile derived from the external bearer-token identity.

```sql
CREATE TABLE users (
  id text PRIMARY KEY,
  auth_subject text NOT NULL,
  email text NOT NULL,
  display_name text NOT NULL,
  skill_level text NOT NULL DEFAULT 'unknown',
  created_at timestamptz NOT NULL DEFAULT now(),

  CONSTRAINT ck_users_id_prefix
    CHECK (id LIKE 'usr_%'),
  CONSTRAINT ck_users_skill_level
    CHECK (skill_level IN ('unknown', 'beginner', 'intermediate', 'advanced')),
  CONSTRAINT ck_users_email_not_blank
    CHECK (length(trim(email)) > 0),
  CONSTRAINT ck_users_display_name_not_blank
    CHECK (length(trim(display_name)) > 0)
);
```

Indexes:

```sql
CREATE UNIQUE INDEX ux_users_auth_subject ON users (auth_subject);
CREATE INDEX ix_users_email ON users (email);
```

Delete behavior: no API hard-delete is defined for users. Foreign keys from
app-owned user data use `ON DELETE RESTRICT`.

### `bike_profiles`

User-owned bikes returned by `/v1/bikes`.

```sql
CREATE TABLE bike_profiles (
  id text PRIMARY KEY,
  user_id text NOT NULL,
  display_name text NOT NULL,
  make text NULL,
  model text NULL,
  model_year integer NULL,
  bike_type text NOT NULL DEFAULT 'unknown',
  frame_material text NOT NULL DEFAULT 'unknown',
  drivetrain text NULL,
  brake_type text NOT NULL DEFAULT 'unknown',
  wheel_size text NULL,
  tire_size text NULL,
  notes text NULL,
  deleted_at timestamptz NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),

  CONSTRAINT fk_bike_profiles_user
    FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE RESTRICT,
  CONSTRAINT ck_bike_profiles_id_prefix
    CHECK (id LIKE 'bike_%'),
  CONSTRAINT ck_bike_profiles_display_name_not_blank
    CHECK (length(trim(display_name)) > 0),
  CONSTRAINT ck_bike_profiles_model_year
    CHECK (model_year IS NULL OR model_year BETWEEN 1880 AND 2100),
  CONSTRAINT ck_bike_profiles_bike_type
    CHECK (bike_type IN (
      'unknown', 'road', 'gravel', 'mountain', 'hybrid',
      'commuter', 'cargo', 'ebike', 'other'
    )),
  CONSTRAINT ck_bike_profiles_frame_material
    CHECK (frame_material IN (
      'unknown', 'aluminum', 'steel', 'carbon', 'titanium', 'other'
    )),
  CONSTRAINT ck_bike_profiles_brake_type
    CHECK (brake_type IN (
      'unknown', 'rim', 'mechanical_disc', 'hydraulic_disc', 'coaster', 'other'
    ))
);
```

Indexes:

```sql
CREATE INDEX ix_bike_profiles_user_created
  ON bike_profiles (user_id, created_at DESC, id DESC)
  WHERE deleted_at IS NULL;
```

Single-row lookups (`GET`/`PATCH`/`DELETE /v1/bikes/{bikeId}`) resolve via the
primary key on `id`; `user_id` and `deleted_at` are then checked against the
single returned row. No separate `(user_id, id)` lookup index is needed for
that access pattern, so this spec intentionally does not define one.

Delete behavior:

- `DELETE /v1/bikes/{bikeId}` should soft-delete by setting `deleted_at`.
- Existing repair sessions continue to reference the bike row.
- Hard-deleting a user is restricted while bikes exist.

### `repair_sessions`

Application-owned repair workflow state. This is the source of truth for the
public `RepairSession`, not ADK session tables.

```sql
CREATE TABLE repair_sessions (
  id text PRIMARY KEY,
  user_id text NOT NULL,
  bike_id text NOT NULL,
  client_session_id text NULL,
  request_hash text NULL,
  phase text NOT NULL DEFAULT 'diagnostic',
  status text NOT NULL DEFAULT 'created',
  safety_state text NOT NULL DEFAULT 'ok',
  current_input_request jsonb NULL,
  execution_progress jsonb NULL,
  active_safety_flags jsonb NOT NULL DEFAULT '[]'::jsonb,
  latest_event_sequence bigint NOT NULL DEFAULT 0,
  diagnostic_report_id text NULL,
  plan_report_id text NULL,
  execution_report_id text NULL,
  shop_referral_report_id text NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),

  CONSTRAINT fk_repair_sessions_user
    FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE RESTRICT,
  CONSTRAINT fk_repair_sessions_bike
    FOREIGN KEY (bike_id) REFERENCES bike_profiles (id) ON DELETE RESTRICT,
  CONSTRAINT ck_repair_sessions_id_prefix
    CHECK (id LIKE 'rs_%'),
  CONSTRAINT ck_repair_sessions_client_hash_pair
    CHECK (
      (client_session_id IS NULL AND request_hash IS NULL)
      OR (client_session_id IS NOT NULL AND request_hash IS NOT NULL)
    ),
  CONSTRAINT ck_repair_sessions_phase
    CHECK (phase IN (
      'diagnostic', 'planning', 'execution',
      'completed', 'shop_referred', 'cancelled'
    )),
  CONSTRAINT ck_repair_sessions_status
    CHECK (status IN (
      'created', 'running', 'awaiting_user', 'awaiting_decision',
      'blocked_safety', 'completed', 'failed', 'cancelled'
    )),
  CONSTRAINT ck_repair_sessions_safety_state
    CHECK (safety_state IN ('ok', 'caution', 'shop_recommended', 'blocked')),
  CONSTRAINT ck_repair_sessions_current_input_request_object
    CHECK (
      current_input_request IS NULL
      OR jsonb_typeof(current_input_request) = 'object'
    ),
  CONSTRAINT ck_repair_sessions_execution_progress_object
    CHECK (
      execution_progress IS NULL
      OR jsonb_typeof(execution_progress) = 'object'
    ),
  CONSTRAINT ck_repair_sessions_active_safety_flags_array
    CHECK (jsonb_typeof(active_safety_flags) = 'array'),
  CONSTRAINT ck_repair_sessions_latest_event_sequence
    CHECK (latest_event_sequence >= 0)
);
```

After `phase_reports` exists, add denormalized latest-report foreign keys:

```sql
ALTER TABLE repair_sessions
  ADD CONSTRAINT fk_repair_sessions_diagnostic_report
    FOREIGN KEY (diagnostic_report_id)
    REFERENCES phase_reports (id)
    ON DELETE SET NULL
    DEFERRABLE INITIALLY DEFERRED,
  ADD CONSTRAINT fk_repair_sessions_plan_report
    FOREIGN KEY (plan_report_id)
    REFERENCES phase_reports (id)
    ON DELETE SET NULL
    DEFERRABLE INITIALLY DEFERRED,
  ADD CONSTRAINT fk_repair_sessions_execution_report
    FOREIGN KEY (execution_report_id)
    REFERENCES phase_reports (id)
    ON DELETE SET NULL
    DEFERRABLE INITIALLY DEFERRED,
  ADD CONSTRAINT fk_repair_sessions_shop_referral_report
    FOREIGN KEY (shop_referral_report_id)
    REFERENCES phase_reports (id)
    ON DELETE SET NULL
    DEFERRABLE INITIALLY DEFERRED;
```

These foreign keys guarantee a `*_report_id` column points at a real
`phase_reports` row, but not that the row belongs to *this* session — that
invariant is maintained by application code, which must only ever set these
columns to a report it just inserted for the same `repair_session_id` (see
"Persist Phase Report" below). There is no API path that deletes a
`repair_session`, so the mutual `CASCADE`/`SET NULL` relationship between
these two tables is not currently exercised in practice; if a session-delete
capability is ever added, verify the cascade behavior with a real test before
relying on it.

Indexes:

```sql
CREATE UNIQUE INDEX ux_repair_sessions_user_client_session
  ON repair_sessions (user_id, client_session_id)
  WHERE client_session_id IS NOT NULL;

CREATE INDEX ix_repair_sessions_user_created
  ON repair_sessions (user_id, created_at DESC, id DESC);

CREATE INDEX ix_repair_sessions_user_status_created
  ON repair_sessions (user_id, status, created_at DESC, id DESC);

CREATE INDEX ix_repair_sessions_bike_created
  ON repair_sessions (bike_id, created_at DESC, id DESC);

CREATE INDEX ix_repair_sessions_active_safety_flags_gin
  ON repair_sessions USING gin (active_safety_flags);
```

Serialization notes:

- Public `latest_event_id` is `latest_event_sequence::text`.
- Public `latest_reports` is built from the four nullable latest-report ID
  columns.

Delete behavior:

- No repair-session delete API is defined.
- Child turns, events, phase-session mappings, and reports are owned by the
  repair session and use `ON DELETE CASCADE`.

### `repair_phase_sessions`

Mapping from one app repair session and product phase to one opaque ADK
session/archive ID. This keeps app-owned state separate from ADK tables while
allowing all turns in a phase to route to the same ADK session.

```sql
CREATE TABLE repair_phase_sessions (
  id text PRIMARY KEY,
  repair_session_id text NOT NULL,
  phase text NOT NULL,
  adk_session_id text NOT NULL,
  status text NOT NULL DEFAULT 'active',
  created_at timestamptz NOT NULL DEFAULT now(),
  closed_at timestamptz NULL,

  CONSTRAINT fk_repair_phase_sessions_repair_session
    FOREIGN KEY (repair_session_id)
    REFERENCES repair_sessions (id)
    ON DELETE CASCADE,
  CONSTRAINT ck_repair_phase_sessions_id_prefix
    CHECK (id LIKE 'phs_%'),
  CONSTRAINT ck_repair_phase_sessions_phase
    CHECK (phase IN ('diagnostic', 'planning', 'execution')),
  CONSTRAINT ck_repair_phase_sessions_status
    CHECK (status IN ('active', 'closed')),
  CONSTRAINT ck_repair_phase_sessions_closed_at
    CHECK (
      (status = 'active' AND closed_at IS NULL)
      OR (status = 'closed' AND closed_at IS NOT NULL)
    ),
  CONSTRAINT ck_repair_phase_sessions_adk_session_id_not_blank
    CHECK (length(trim(adk_session_id)) > 0)
);
```

Indexes and constraints:

```sql
CREATE UNIQUE INDEX ux_repair_phase_sessions_session_phase
  ON repair_phase_sessions (repair_session_id, phase);

CREATE UNIQUE INDEX ux_repair_phase_sessions_adk_session
  ON repair_phase_sessions (adk_session_id);
```

`DiagnosticReportV1.diagnostic_session_id` should store
`repair_phase_sessions.id`, not a raw ADK table primary key.

A row's lifecycle is: created by "Ensure Phase Session" when the first turn
for that phase arrives, `status = 'active'` while turns are accepted into it,
and closed (`status = 'closed'`, `closed_at` set) when the session transitions
out of that phase — see "Persist Later Events" below.

### `repair_turns`

Accepted user turns submitted to `/v1/repair-sessions/{sessionId}/turns`.

```sql
CREATE TABLE repair_turns (
  id text PRIMARY KEY,
  repair_session_id text NOT NULL,
  repair_phase_session_id text NOT NULL,
  client_turn_id text NOT NULL,
  request_hash text NOT NULL,
  schema_version text NOT NULL DEFAULT 'ai_turn.v1',
  phase text NOT NULL,
  message jsonb NOT NULL,
  responds_to_input_request_id text NULL,
  start_event_sequence bigint NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),

  CONSTRAINT fk_repair_turns_repair_session
    FOREIGN KEY (repair_session_id)
    REFERENCES repair_sessions (id)
    ON DELETE CASCADE,
  CONSTRAINT fk_repair_turns_repair_phase_session
    FOREIGN KEY (repair_phase_session_id)
    REFERENCES repair_phase_sessions (id)
    ON DELETE CASCADE,
  CONSTRAINT ck_repair_turns_id_prefix
    CHECK (id LIKE 'turn_%'),
  CONSTRAINT ck_repair_turns_client_turn_id_not_blank
    CHECK (length(trim(client_turn_id)) > 0),
  CONSTRAINT ck_repair_turns_schema_version
    CHECK (schema_version = 'ai_turn.v1'),
  CONSTRAINT ck_repair_turns_phase
    CHECK (phase IN ('diagnostic', 'planning', 'execution')),
  CONSTRAINT ck_repair_turns_message_object
    CHECK (jsonb_typeof(message) = 'object'),
  CONSTRAINT ck_repair_turns_start_event_sequence
    CHECK (start_event_sequence >= 1)
);
```

Note: `responds_to_input_request_id`, when present, refers to the `id` field
inside the JSONB `repair_sessions.current_input_request` blob, not a row in
any table — there is no input-request table, so this cannot be a foreign key.
The service layer must validate it against the session's current pending
input request before accepting the turn.

Indexes and constraints:

```sql
CREATE UNIQUE INDEX ux_repair_turns_session_client_turn
  ON repair_turns (repair_session_id, client_turn_id);

CREATE UNIQUE INDEX ux_repair_turns_session_start_event
  ON repair_turns (repair_session_id, start_event_sequence);

CREATE INDEX ix_repair_turns_session_created
  ON repair_turns (repair_session_id, created_at ASC, id ASC);

CREATE INDEX ix_repair_turns_phase_session
  ON repair_turns (repair_phase_session_id);
```

Delete behavior: turns are owned by repair sessions and cascade with the
session. There is no turn-delete API.

### `repair_session_events`

Persisted product-level event stream for SSE replay. These are app events, not
raw ADK stream events.

```sql
CREATE TABLE repair_session_events (
  id text PRIMARY KEY,
  repair_session_id text NOT NULL,
  turn_id text NULL,
  sequence bigint NOT NULL,
  type text NOT NULL,
  data jsonb NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),

  CONSTRAINT fk_repair_session_events_repair_session
    FOREIGN KEY (repair_session_id)
    REFERENCES repair_sessions (id)
    ON DELETE CASCADE,
  CONSTRAINT fk_repair_session_events_turn
    FOREIGN KEY (turn_id)
    REFERENCES repair_turns (id)
    ON DELETE SET NULL,
  CONSTRAINT ck_repair_session_events_id_prefix
    CHECK (id LIKE 'evt_%'),
  CONSTRAINT ck_repair_session_events_sequence
    CHECK (sequence >= 1),
  CONSTRAINT ck_repair_session_events_type
    CHECK (type IN (
      'turn.started',
      'assistant.delta',
      'assistant.message.completed',
      'input.requested',
      'artifact.referenced',
      'phase.report.created',
      'phase.transitioned',
      'safety.escalated',
      'execution.step.updated',
      'turn.completed',
      'error',
      'heartbeat'
    )),
  CONSTRAINT ck_repair_session_events_data_object
    CHECK (jsonb_typeof(data) = 'object')
);
```

Indexes and constraints:

```sql
CREATE UNIQUE INDEX ux_repair_session_events_session_sequence
  ON repair_session_events (repair_session_id, sequence);

CREATE INDEX ix_repair_session_events_turn_sequence
  ON repair_session_events (turn_id, sequence)
  WHERE turn_id IS NOT NULL;
```

Note: there is no DB-level constraint guaranteeing that `turn_id`, when set,
belongs to the same `repair_session_id` as the event row itself — enforcing
that would require a unique `(id, repair_session_id)` index on `repair_turns`
plus a composite foreign key here. Skipped as low-value hardening for a
single-writer-per-session access pattern; application code is the source of
truth for this invariant today. Revisit if cross-session event bugs ever
surface.

Serialization notes:

- Public `RepairSessionEvent.id` is `sequence::text`.
- Public `RepairSessionEvent.session_id` is `repair_session_id`.
- SSE frame `id` is also `sequence::text`.
- SSE frame `event` is `type`.
- SSE frame `data` is the full serialized `RepairSessionEvent`.

Delete behavior: events are owned by repair sessions and cascade with the
session. If a turn row were ever removed by an internal maintenance operation,
events remain replayable with `turn_id = null`.

### `artifact_refs`

Metadata and storage reference for uploaded artifacts. The binary object lives
in a storage provider; raw provider paths are never serialized in public API
responses.

```sql
CREATE TABLE artifact_refs (
  id text PRIMARY KEY,
  user_id text NOT NULL,
  repair_session_id text NULL,
  bike_id text NULL,
  client_artifact_id text NULL,
  request_hash text NULL,
  purpose text NOT NULL,
  media_type text NOT NULL,
  mime_type text NOT NULL,
  filename text NOT NULL,
  byte_size bigint NOT NULL,
  width integer NULL,
  height integer NULL,
  duration_seconds numeric(10, 3) NULL,
  status text NOT NULL DEFAULT 'uploaded',
  rejection_reason text NULL,
  content_sha256 text NOT NULL,
  storage_provider text NOT NULL,
  storage_bucket text NULL,
  storage_path text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),

  CONSTRAINT fk_artifact_refs_user
    FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE RESTRICT,
  CONSTRAINT fk_artifact_refs_repair_session
    FOREIGN KEY (repair_session_id)
    REFERENCES repair_sessions (id)
    ON DELETE SET NULL,
  CONSTRAINT fk_artifact_refs_bike
    FOREIGN KEY (bike_id)
    REFERENCES bike_profiles (id)
    ON DELETE SET NULL,
  CONSTRAINT ck_artifact_refs_id_prefix
    CHECK (id LIKE 'art_%'),
  CONSTRAINT ck_artifact_refs_client_hash_pair
    CHECK (
      (client_artifact_id IS NULL AND request_hash IS NULL)
      OR (client_artifact_id IS NOT NULL AND request_hash IS NOT NULL)
    ),
  CONSTRAINT ck_artifact_refs_purpose
    CHECK (purpose IN (
      'diagnostic_photo',
      'verification_photo',
      'bike_profile_photo',
      'repair_reference',
      'other'
    )),
  CONSTRAINT ck_artifact_refs_parent_by_purpose
    CHECK (
      (
        purpose IN ('diagnostic_photo', 'verification_photo')
        AND repair_session_id IS NOT NULL
        AND bike_id IS NULL
      )
      OR (
        purpose = 'bike_profile_photo'
        AND bike_id IS NOT NULL
        AND repair_session_id IS NULL
      )
      OR (
        purpose IN ('repair_reference', 'other')
      )
    ),
  CONSTRAINT ck_artifact_refs_media_type
    CHECK (media_type IN ('image', 'video', 'audio', 'document', 'other')),
  CONSTRAINT ck_artifact_refs_byte_size
    CHECK (byte_size >= 0),
  CONSTRAINT ck_artifact_refs_dimensions
    CHECK (
      (width IS NULL OR width > 0)
      AND (height IS NULL OR height > 0)
    ),
  CONSTRAINT ck_artifact_refs_duration
    CHECK (duration_seconds IS NULL OR duration_seconds >= 0),
  CONSTRAINT ck_artifact_refs_status
    CHECK (status IN ('uploaded', 'processing', 'ready', 'rejected')),
  CONSTRAINT ck_artifact_refs_rejection_reason
    CHECK (
      (status = 'rejected' AND rejection_reason IS NOT NULL)
      OR (status <> 'rejected')
    ),
  CONSTRAINT ck_artifact_refs_filename_not_blank
    CHECK (length(trim(filename)) > 0),
  CONSTRAINT ck_artifact_refs_content_sha256
    CHECK (content_sha256 ~ '^[0-9a-f]{64}$'),
  CONSTRAINT ck_artifact_refs_storage_provider_not_blank
    CHECK (length(trim(storage_provider)) > 0),
  CONSTRAINT ck_artifact_refs_storage_path_not_blank
    CHECK (length(trim(storage_path)) > 0)
);
```

Indexes and constraints:

```sql
CREATE UNIQUE INDEX ux_artifact_refs_user_client_artifact
  ON artifact_refs (user_id, client_artifact_id)
  WHERE client_artifact_id IS NOT NULL;

CREATE INDEX ix_artifact_refs_session_created
  ON artifact_refs (repair_session_id, created_at DESC, id DESC)
  WHERE repair_session_id IS NOT NULL;

CREATE INDEX ix_artifact_refs_bike_created
  ON artifact_refs (bike_id, created_at DESC, id DESC)
  WHERE bike_id IS NOT NULL;

CREATE INDEX ix_artifact_refs_user_created
  ON artifact_refs (user_id, created_at DESC, id DESC);
```

Diagnostic-slice upload behavior:

- `diagnostic_photo` uploads are synchronous from the API perspective.
- A successful upload should commit as `status = 'ready'`.
- A rejected upload may commit as `status = 'rejected'` only when the API still
  returns a structured artifact response. Validation failures that return `422`
  do not need an artifact row.
- `content_sha256` is computed from the uploaded bytes before commit and feeds
  into `request_hash` as described in "Idempotency Hashes" above, so a retried
  `client_artifact_id` with different file content is rejected as a conflict
  rather than silently treated as a duplicate.

Delete behavior:

- Artifacts are owned by the user but may outlive sessions or bikes.
- Deleting a repair session or bike sets the nullable parent reference to
  `NULL`; it does not delete the artifact metadata or storage object.

### `phase_reports`

Structured phase report envelopes returned by `/reports`.

```sql
CREATE TABLE phase_reports (
  id text PRIMARY KEY,
  repair_session_id text NOT NULL,
  repair_phase_session_id text NULL,
  type text NOT NULL,
  schema_version text NOT NULL,
  phase text NOT NULL,
  summary text NOT NULL,
  safety_flags jsonb NOT NULL DEFAULT '[]'::jsonb,
  source_artifact_ids jsonb NOT NULL DEFAULT '[]'::jsonb,
  payload jsonb NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),

  CONSTRAINT fk_phase_reports_repair_session
    FOREIGN KEY (repair_session_id)
    REFERENCES repair_sessions (id)
    ON DELETE CASCADE,
  CONSTRAINT fk_phase_reports_repair_phase_session
    FOREIGN KEY (repair_phase_session_id)
    REFERENCES repair_phase_sessions (id)
    ON DELETE SET NULL,
  CONSTRAINT ck_phase_reports_id_prefix
    CHECK (id LIKE 'rpt_%'),
  CONSTRAINT ck_phase_reports_type
    CHECK (type IN ('diagnostic', 'plan', 'execution', 'shop_referral')),
  CONSTRAINT ck_phase_reports_phase
    CHECK (phase IN (
      'diagnostic', 'planning', 'execution',
      'completed', 'shop_referred', 'cancelled'
    )),
  CONSTRAINT ck_phase_reports_schema_version
    CHECK (schema_version IN (
      'diagnostic_report.v1',
      'plan_report.v1',
      'execution_report.v1',
      'shop_referral_report.v1'
    )),
  CONSTRAINT ck_phase_reports_type_schema_version_pair
    CHECK (
      (type = 'diagnostic' AND schema_version = 'diagnostic_report.v1')
      OR (type = 'plan' AND schema_version = 'plan_report.v1')
      OR (type = 'execution' AND schema_version = 'execution_report.v1')
      OR (type = 'shop_referral' AND schema_version = 'shop_referral_report.v1')
    ),
  CONSTRAINT ck_phase_reports_summary_not_blank
    CHECK (length(trim(summary)) > 0),
  CONSTRAINT ck_phase_reports_safety_flags_array
    CHECK (jsonb_typeof(safety_flags) = 'array'),
  CONSTRAINT ck_phase_reports_source_artifact_ids_array
    CHECK (jsonb_typeof(source_artifact_ids) = 'array'),
  CONSTRAINT ck_phase_reports_payload_object
    CHECK (jsonb_typeof(payload) = 'object')
);
```

Indexes:

```sql
CREATE INDEX ix_phase_reports_session_created
  ON phase_reports (repair_session_id, created_at DESC, id DESC);

CREATE INDEX ix_phase_reports_session_type_created
  ON phase_reports (repair_session_id, type, created_at DESC, id DESC);

CREATE INDEX ix_phase_reports_phase_session_created
  ON phase_reports (repair_phase_session_id, created_at DESC, id DESC)
  WHERE repair_phase_session_id IS NOT NULL;
```

Delete behavior: reports are owned by repair sessions and cascade with the
session.

## Cursor Implementation

### List Pagination

Use opaque keyset cursors for list endpoints. The cursor is base64url-encoded
JSON with the last row's sort keys:

```json
{"created_at":"2026-06-21T17:05:00.123456Z","id":"rpt_..."}
```

Default ordering:

```sql
ORDER BY created_at DESC, id DESC
```

Next-page predicate:

```sql
WHERE (created_at, id) < (:cursor_created_at, :cursor_id)
```

Apply the endpoint's ownership and filter predicates before applying the cursor
predicate. Fetch `limit + 1` rows to decide whether to return `next_cursor`.

Diagnostic-slice list cursors use:

| Endpoint | Table/index |
|---|---|
| `GET /v1/bikes` | `ix_bike_profiles_user_created` |
| `GET /v1/repair-sessions` | `ix_repair_sessions_user_created` or `ix_repair_sessions_user_status_created` |
| `GET /v1/repair-sessions/{sessionId}/reports` | `ix_phase_reports_session_created` |

The cursor must be treated as an opaque API token. Invalid base64, malformed
JSON, missing keys, or invalid timestamp values return `422 Validation Error`.

### SSE Replay Cursor

SSE replay does not use the opaque list cursor format. The public SSE cursor is
the event sequence number encoded as a decimal string:

- `after=0`: replay all retained events for the session
- omitted `after`: start after `repair_sessions.latest_event_sequence`
- `Last-Event-ID`: same format as `after`; ignored when `after` is present

Replay query:

```sql
SELECT *
FROM repair_session_events
WHERE repair_session_id = :repair_session_id
  AND sequence > :after_sequence
ORDER BY sequence ASC;
```

Reject negative numbers, non-integers, and values greater than the session's
current `latest_event_sequence` with `422 Validation Error`.

## Transaction Boundaries

### Create Repair Session

`POST /v1/repair-sessions` runs in one transaction:

1. Resolve the authenticated `users.id`.
2. Select the requested `bike_profiles` row by `(id, user_id)` where
   `deleted_at IS NULL`; return `404` if missing.
3. If `client_session_id` is supplied, compute `request_hash` from
   `bike_id` and the idempotency key.
4. Insert `repair_sessions` with `phase = 'diagnostic'`,
   `status = 'created'`, `safety_state = 'ok'`, and
   `latest_event_sequence = 0`.
5. If the partial unique constraint finds an existing row, compare
   `request_hash` and either return the original `201` response or raise
   `409 idempotency_conflict`.

Do not create an ADK session during repair-session creation. The diagnostic
phase session is created lazily by "Ensure Phase Session" below, when the
first turn starts.

### Ensure Phase Session

Before starting the locked turn-creation transaction, the service ensures a
`repair_phase_sessions` row exists for `(repair_session_id, phase)`. This step
deliberately runs outside any row-locked database transaction, because it may
call out to ADK to create a session — a slow, externally-dependent operation
that must not hold a Postgres lock or an open transaction open underneath it:

1. `SELECT` the `repair_phase_sessions` row for `(repair_session_id, phase)`.
   If found, use it and proceed directly to "Create Turn And Initial Event."
2. If not found, call the internal ADK integration boundary to create a new
   ADK session for this phase and obtain its opaque `adk_session_id`. No
   database transaction is open during this call.
3. Insert the new row:

   ```sql
   INSERT INTO repair_phase_sessions
     (id, repair_session_id, phase, adk_session_id, status)
   VALUES (:id, :repair_session_id, :phase, :adk_session_id, 'active')
   ON CONFLICT (repair_session_id, phase) DO NOTHING
   RETURNING *;
   ```

4. If the insert returns no row, a concurrent request already created the
   phase session first. Close or discard the ADK session created in step 2 —
   it is now an orphan — then re-`SELECT` the winning row from step 1.

Only after this step completes does the service open the turn-creation
transaction below, with a `repair_phase_session_id` already in hand. The
turn-creation transaction itself never calls ADK or any other external
service.

### Create Turn And Initial Event

`POST /v1/repair-sessions/{sessionId}/turns` uses one transaction for turn
acceptance and the initial durable event, run after "Ensure Phase Session"
above has resolved a `repair_phase_session_id`:

1. Resolve the authenticated `users.id`.
2. Select the target `repair_sessions` row by `(id, user_id) FOR UPDATE`.
   This row lock serializes per-session event sequence allocation.
3. Validate the session status and phase allow a user turn.
4. Validate referenced artifact IDs are owned by the same user and attached to
   the same repair session.
5. Compute `request_hash` from the canonical `TurnCreate` body.
6. If `(repair_session_id, client_turn_id)` already exists, compare
   `request_hash` and either return the original `202` response or raise
   `409 idempotency_conflict`.
7. Set `next_sequence = repair_sessions.latest_event_sequence + 1`.
8. Insert `repair_turns` with `repair_phase_session_id` set to the row
   resolved by "Ensure Phase Session" and `start_event_sequence =
   next_sequence`.
9. Insert `repair_session_events` with:
   - `sequence = next_sequence`
   - `type = 'turn.started'`
   - `turn_id = repair_turns.id`
   - `data` matching `TurnStartedEventData`
10. Update `repair_sessions`:
    - `status = 'running'`
    - `current_input_request = NULL`
    - `latest_event_sequence = next_sequence`
11. Commit before notifying any SSE listeners.

This transaction does no external or network calls — only fast, local
database work — so the `FOR UPDATE` lock on `repair_sessions` is held for a
short, predictable duration regardless of ADK latency, and is unaffected by
ADK session creation happening earlier in "Ensure Phase Session."

`TurnAccepted.start_event_id` is `start_event_sequence::text`, and the returned
`event_stream_url` uses that value as `after`.

### Persist Later Events

Each later streamed event is persisted in its own short transaction:

1. Select `repair_sessions` by ID `FOR UPDATE`.
2. Allocate `next_sequence = latest_event_sequence + 1`.
3. Insert `repair_session_events`.
4. Apply any state changes implied by the event:
   - `input.requested`: update `current_input_request`.
   - `execution.step.updated`: update `execution_progress` and possibly
     `current_input_request`.
   - `safety.escalated`: update `safety_state` and `active_safety_flags`.
   - `phase.transitioned`: update `phase` and `status` on `repair_sessions`,
     and close the outgoing phase's `repair_phase_sessions` row
     (`status = 'closed'`, `closed_at = now()`) for
     `(repair_session_id, from_phase)`. The incoming phase's
     `repair_phase_sessions` row is not created here — it is created lazily
     by "Ensure Phase Session" when that phase's first turn arrives.
   - `turn.completed`: update `status` according to the serialized session.
5. Update `latest_event_sequence`.
6. Commit before sending the SSE frame.

Do not use a Postgres sequence object for event IDs. The contract requires
monotonic event IDs within one repair session, not globally.

### Persist Phase Report

Report creation runs in one transaction:

1. Select the `repair_sessions` row `FOR UPDATE`.
2. Insert a `phase_reports` row with envelope fields duplicated in relational
   columns and the full report in `payload`.
3. Update the matching denormalized latest-report column on
   `repair_sessions`.
4. Update `active_safety_flags` and `safety_state` from the report envelope.
5. Allocate and insert `safety.escalated` first if safety state changed.
6. Allocate and insert `phase.report.created`.
7. Allocate and insert `turn.completed` for the producing turn when applicable.
8. Update `latest_event_sequence` to the final inserted sequence.
9. Commit before notifying SSE listeners.

## ADK Table Separation

The app schema owns the product tables in this spec. ADK session storage must
remain separate:

- Use the same Postgres instance if convenient for local deployment.
- Put ADK-owned tables in a separate schema, for example `adk`.
- App Alembic migrations should create and migrate only app-owned tables.
- App services must not join against ADK internal tables for product state.
- `repair_phase_sessions.adk_session_id` is an opaque lookup key passed through
  the internal ADK integration package.

The public API must never expose raw ADK session rows, prompts, runner events,
tool calls, or provider storage paths.

## Alembic Ordering Notes

One migration can create the schema in this order:

1. Install `set_updated_at()` trigger function.
2. Create `users`.
3. Create `bike_profiles` and its `updated_at` trigger.
4. Create `repair_sessions` without latest-report foreign-key constraints and
   attach its `updated_at` trigger.
5. Create `repair_phase_sessions`.
6. Create `repair_turns`.
7. Create `repair_session_events`.
8. Create `artifact_refs` and its `updated_at` trigger.
9. Create `phase_reports`.
10. Add latest-report foreign-key constraints from `repair_sessions` to
    `phase_reports`.
11. Create all indexes listed in this spec.
