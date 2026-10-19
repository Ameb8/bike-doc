# Queue reconstruction and retention

PostgreSQL is authoritative. This procedure restores notifications after loss of
JetStream data; it cannot restore lost PostgreSQL inputs or product records.
Use the configured pinned NATS 2.12.1 server and persistent volume. Never run two
servers against one data directory. Retain independent PostgreSQL backups.

## Reconstruction

1. Restore PostgreSQL and broker availability. Deploy workers supporting both
   registered v1 definitions and keep API maintenance enabled. Use the same
   topology and timing settings on every role and on the operator host.
2. From the repository root with the intended `.env`, run
   `task jobs:reconstruct`. This is a finite command, not another publisher or
   maintenance daemon. Its exit status is nonzero on failure. It logs bounded
   page counts, completed advances, or `job_reconstruction_failed`, without
   dependency exception text or connection URLs.
3. The command creates missing stream/consumers and refuses incompatible
   definitions, including a different message lifetime. Investigate a refusal;
   it does not silently reconfigure or delete a live stream. Existing streams
   from before finite MaxAge was introduced need an explicit operator migration
   of MaxAge to the configured value (or disposable topology recreation).
   Preserve PostgreSQL and reconstruct after any intentional broker deletion.
4. Observe the existing API publisher confirming pending generations in
   PostgreSQL. Successful command completion means intent was restored, not
   that workers have completed work. PostgreSQL terminal states and persisted
   diagnostic events remain the outcome evidence.

Reconstruction validates topology before changing jobs, then scans bounded
keyset pages over rows accepted before operation start. Each page commits
independently. It only advances eligible queued/retrying definitions
whose registered policy allows replay and whose attempt budget remains.
Unclaimed pending intent coalesces without another increment. An in-flight
generation is superseded so an old broker ACK cannot confirm restored intent
after data loss. Publication confirmation always stops at its claimed generation.
It never publishes messages, consumes attempts, changes `eligible_at`, recovers
running jobs, or edits terminal outcomes. Running deadlines and diagnostic
boundaries are handled solely by the existing API reconciler. Broker contents
and maximum-delivery/termination advisories do not authorize transitions.

Ctrl-C or failure rolls back the current page; previously committed pages remain
safe. Rerun from the beginning. Concurrent invocations and maintenance use locked
conditional generation transitions. Repeating after publication may create a
fresh wake-up epoch for still-eligible work; duplicates remain attempt-free until
one authoritative execution claim wins. The sole publisher uses deterministic
`job_id:generation` message IDs; its exact-generation confirmation cannot erase
newer intent. No manual reset of generations, attempts, or tokens is needed.

## Retention and bounds

All variables below use the `BIKE_DOC_API_` prefix and are documented in
[.env.example](../../.env.example). Defaults are:

| Setting | Default | Purpose |
| --- | --- | --- |
| `NATS_WORK_MAX_AGE_SECONDS` | 86400 | Finite stream message lifetime, validated by every topology client |
| `JOB_TERMINAL_RETENTION_SECONDS` | 604800 | Complete terminal job row retention |
| `JOB_AUDIT_HORIZON_SECONDS` | 172800 | Required operational audit horizon |
| `JOB_RECOVERY_BACKOFF_SECONDS` | 30 | Minimum retry delay after expired execution, combined with kind retry delay |
| `JOB_CLEANUP_BATCH_LIMIT` | 100 | Maximum rows deleted per cycle |
| `JOB_CLEANUP_POLL_SECONDS` | 3600 | API cleanup cadence |
| `JOB_RECONSTRUCTION_PAGE_LIMIT` | 100 | Maximum rows per reconstruction transaction |

Retention must strictly exceed MaxAge, audit horizon, no-progress plus transport
backoff, and each configured kind's entire attempt budget multiplied by hard
execution duration plus retry delay, recovery backoff and reconciliation cadence.
Settings reject equality, shorter durations, nonfinite values and unbounded
batches. Increasing timing/attempt budgets may therefore require longer
retention. Retain old settings' larger horizons through a configuration change;
settings cannot infer historical broker or audit policies.

Cleanup refuses all nonterminal rows, pending generations, publication claims,
and terminal timestamps or last updates newer than the retention cutoff. A late
terminal publication confirmation restarts the last-update horizon, ensuring
resolvability for the entire broker lifetime after publication. It uses bounded
`FOR UPDATE SKIP LOCKED` scans and rechecks under the row lock before deletion;
no broker connection is required for correctness. Cancellation rolls back the
transaction and releases its locks. Publication claims are separate short
leases (30 seconds by default); abandoned claims naturally expire. Every API
cycle scans at most two reconciliation batches and one independent cleanup
batch at its own cadence. Recovery invalidates old tokens in the same locked
transaction before future eligibility; protected late writes remain rejected.

## Verification and acceptance evidence

Run `task format`, `task check`, then `task test:recovery`. The last target creates
disposable migrated PostgreSQL 16 and pinned JetStream, uses fake model/providers,
and removes its containers and volumes on exit. It requires local Docker access,
not production credentials.

| #153 acceptance criterion | Implementation and evidence |
| --- | --- |
| 1. All expired execution classes use one bounded path | Shared repository and complete `recovery_policies`; real profile replay/exhaustion and diagnostic pre/post-boundary recovery tests |
| 2. Invalidate stale tokens before recovery | Locked `recover_expired` transition; diagnostic protected-write replacement/expiry matrix and profile late-provider rejection |
| 3. Attempt-free, policy-safe no-progress repair | Repository scan and publication CAS; real eligibility/deadline/attempt/boundary omission matrix |
| 4. Multi-replica recovery and generation safety | Competing maintenance/diagnostic terminalization tests, generation confirmation races, reconstruction across an old broker ACK |
| 5. Cleanup refusal and concurrency | Locked retention scan/deletion; real exact-age edge, nonterminal/pending/claimed/recent-update refusals, competing cleanup, late confirmation and cancellation lock-release tests |
| 6. Retention exceeds every configured horizon | Typed `Settings` timing relationships and focused invalid/equal-horizon unit tests; finite MaxAge topology validation |
| 7. Rebuild topology and safe intent from PostgreSQL | Real stream loss/recreation and incompatible topology tests; reconstruction sends no messages, then existing publication and real delivery execute safe work |
| 8. Interrupted/repeated/overlapping reconstruction | Cancellation after a committed page, concurrent operations and maintenance, repeated pending-intent coalescing, and real diagnostic delivery proving reserved work cannot invoke the model |
| 9. Advisories stay observational | Pinned NATS transport/advisory contract tests; reconstruction and cleanup have no advisory transition input |
| 10. Focused unit and real infrastructure coverage | `test_job_reconstruction.py`, maintenance lifecycle unit tests, real maintenance/diagnostic/profile/NATS suites and independent-session persistence races |
| 11. Required verification sequence | `task format`, `task check`, then `task test:recovery` using disposable PostgreSQL/JetStream |

Inspection UX, dashboards, production deployment hardening, capacity tuning and
final legacy executor removal belong to subsequent tasks.

For the secured single-host topology, use the containerized finite command,
volume backup/restore ordering and rollback procedure in the
[production runbook](../../deploy/README.md). Worker credentials intentionally
cannot provision/reconstruct the other workload; use the operator's API identity.
