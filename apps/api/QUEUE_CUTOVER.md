# Queue-only production cutover (#158)

This release records a diagnostic job for every new turn and an independent
profile job for eligible images. There are no executor selection settings,
in-process schedulers or automatic fallback. HTTP idempotent replay returns the
original acceptance without backfilling jobs, including historical acceptances.
The public OpenAPI file and event schemas are unchanged.

## Certification gate

Before removing the old executor, Task 4.5 passed against the tuned production
configuration: `task test:queue-certification`, certificate
`.certification/run-20261021-233316/certification.json`, source fingerprint
`2590c53d018c5630b83d5716c6bade85b7a243e71fe78f006f47fc8818758ed6`.
All 38 matrix items, compatibility and ten capacity/privacy scenarios passed in
both clean runs. The fingerprint matched the unchanged checkout before this
cutover was implemented. See [QUEUE_CERTIFICATION.md](QUEUE_CERTIFICATION.md).

Before rollout, run from the root with Docker/Compose and locked dependencies:

```bash
task format
task check
task test:queue-certification -- --output "$PWD/.certification/queue-only-release"
task test:queue-cutover
```

Use a fresh output directory each time. A full certificate must say `passed:
true`, `mode: full`, all stages true, two clean runs, 38 passing items each and
both compatibility gates true. Functional-only evidence cannot authorize release.
No tests use live providers. A changed source fingerprint invalidates the gate;
rerun after code/configuration changes. Keep bounded certificates with release
records; never retain raw credentials, request content or provider traces.

Queue-only revalidation on 2026-10-22 passed the full command with output
`$PWD/.certification/issue-158-final-20261022`. The certificate records
`passed: true`, `mode: full`, all six stages passing, and source fingerprint
`cfe771e4bfdafb4d67e7595dfc1c59b097bd695e884756e210357252468faac1`.
Each clean run passed 10 session/migration, 122 transport, 48 persistence and
173 focused contract tests, all 38 obligations and compatibility, and all ten
capacity/privacy scenarios. Peak PostgreSQL connections were 33 and 32 against
the gate of 52. These measurements cover the tuned local topology and fake
external providers.

## Deployment order

Use the commands and private role credentials in [deploy/README.md](../../deploy/README.md).
Keep ingress closed throughout steps 1–3; the reverse proxy controls admission.

1. Apply additive product/ADK migrations. Deploy compatible diagnostic and profile
   workers first, using the tuned pools, concurrency, deadlines and replica counts
   in [CAPACITY.md](../../deploy/CAPACITY.md). Verify every role's `/ready`, complete
   v1 registry, both consumers, and current worker/maintenance heartbeats. Existing
   APIs must publish/reconcile all kinds. Retain all old definitions through their
   final possible redelivery. API maintenance is the only publication/recovery host.
2. Drain old API processes while ingress is closed, allowing their in-process
   tasks to finish within their shutdown budget. Account for every historical
   accepted turn and eligible profile run that lacks a durable job. Inspect the
   read-only query below and inference-run status/lease/attempt history. Record
   only opaque identities, disposition and bounded evidence in the incident record.
   A nonempty or ambiguous inventory blocks rollout: keep the old process alive
   to finish, or classify lost work and repair only through an approved product
   procedure. Absence of a job or completion alone is never proof of no effects.
   Never manufacture queue jobs for historical work or retry a model blindly.
3. Deploy the queue-only API with maintenance enabled. Remove the obsolete
   `BIKE_DOC_API_DIAGNOSTIC_EXECUTION` and
   `BIKE_DOC_API_PROFILE_INFERENCE_EXECUTION` variables from managed secrets and
   environments; they are no longer settings and cannot change execution.
   No legacy model credentials are needed in the API role. Verify `/ready` and
   fresh maintenance observations before opening ingress.
4. Accept one controlled text turn and one image turn. Inspection must show one
   diagnostic job per turn and one independent profile job for the image turn,
   publication confirmation, bounded attempts and durable terminal product state.
   Replay the same client keys and verify identical `202` bodies and no new jobs.
   Read live SSE and reconnect from its persisted cursor: ordered public events,
   exact terminal errors where appropriate, no internal job/broker/ADK identities.
5. Verify metrics/alerts, both independent consumers, profile backlog isolation,
   connection pressure, publication recovery and polling fallback. Run the finite
   inspection and disposable reconstruction/shutdown checks before expanding
   ingress. PostgreSQL truth and transport state must be inspected separately.

Historical diagnostic inventory (run with read-only database credentials; results
are private opaque identifiers, never public telemetry):

```sql
SELECT t.id, t.repair_session_id
FROM repair_turns AS t
WHERE t.phase = 'diagnostic'
  AND NOT EXISTS (
    SELECT 1 FROM background_jobs AS j
    WHERE j.job_kind = 'diagnostic_turn'
      AND j.input->>'turn_id' = t.id
  )
  AND NOT EXISTS (
    SELECT 1 FROM repair_session_events AS e
    WHERE e.turn_id = t.id AND e.type = 'turn.completed'
  );
```

Profile inventory is independent of diagnostic completion:

```sql
SELECT t.id, r.status, r.attempt_count, r.max_attempts, r.started_at, r.completed_at
FROM repair_turns AS t
LEFT JOIN profile_inference_runs AS r ON r.turn_id = t.id
WHERE t.phase = 'diagnostic'
  AND jsonb_array_length(t.message->'artifact_ids') > 0
  AND NOT EXISTS (
    SELECT 1 FROM background_jobs AS j
    WHERE j.job_kind = 'profile_inference'
      AND j.input->>'turn_id' = t.id
  );
```

Review every inference schema/extractor identity separately. Account for terminal
runs and investigate missing/started/retryable runs and their call budgets.
Neither a missing run nor an expired old lease proves no model call occurred.
Completion of diagnostics says nothing about profile completion. Existing
terminal historical turns are not new queue work.
The disposable smoke seeds a pre-cutover turn with no jobs, proves the inventory
finds it, replays without backfilling, simulates the old owner's normal completion,
and proves a fresh acceptance after drain records durable work. Worker-loss
scenarios additionally exercise effect-aware deadline accounting.

## Effect-aware rollback

1. Close ingress. Stop fetching and drain **both** consumers with their bounded
   shutdown budget before replacing workers. Preserve PostgreSQL product rows,
   jobs, inputs, generations, attempts and ADK storage; never restore an older
   database over accepted work or delete jobs to force eligibility.
2. Paginate inspection of all nonterminal jobs, including state, kind/version,
   token, deadline, effect boundary and persisted completion. A running unexpired
   execution remains its sole owner. Let it settle or reach its hard deadline;
   compatible API maintenance classifies loss. Pre-boundary work may retry within
   policy; exhausted work terminalizes; post-boundary diagnostic loss interrupts
   without another ADK run. A committed normal product completion is preserved.
3. Roll back only to a schema-compatible API/worker set that understands all
   retained definitions. Keep compatible consumers available for preserved jobs.
   Replaying HTTP keys always returns the original acceptance and never switches
   owners. Queue-only code has no dormant fallback to enable.
4. An older in-process release may serve only separately admitted work proven
   safe, with ingress isolated from all accepted queue identities and an explicit
   per-work safety record proving no live owner or unsafe effects. If that proof
   is unavailable, pause admission and repair the compatible queue deployment.
   Do not translate accepted jobs or automatically replay post-effect/ambiguous
   historical work. A user retry after a terminal error is a new turn, never a
   mutation of the terminal job.

## Executable final smoke and acceptance mapping

`task test:queue-cutover` runs secured production deployment validation/smoke
then focused real PostgreSQL/JetStream acceptance/recovery tests. All
projects, secrets and volumes are disposable. See [deploy/VERIFICATION.md](../../deploy/VERIFICATION.md).
Reconstruction runs first because later worker loss and drain fixtures
intentionally retain accepted queued jobs. Its bounded publication batches
therefore start from clean state.

On 2026-10-22, `task test:queue-cutover` passed after the full certificate above:
19 deployment-validation tests, synthetic alert checks, the secured production
topology smoke, and all 16 real PostgreSQL/JetStream acceptance/recovery cases.
Run these gates sequentially on the certification host. No assertions, timeout
budgets or capacity gates were relaxed. Disposable containers and volumes were
removed; pre-existing infrastructure was preserved.

| Contract | Executed owner |
| --- | --- |
| Unconditional atomic jobs, text-only eligibility, strict inputs, concurrent acceptance/replay | Turn API/service tests; `test_profile_worker.py` rollback/concurrency tests; `test_queue_only.py` source/configuration guards |
| No scheduling path or selector, including historical replay | Every turn route test rejects `BackgroundTasks.add_task`; source AST guard; obsolete-env test; historical replay API test |
| Broker unavailable at HTTP acceptance, later maintenance/publication and restart | Profile outage/ack-window/broker/worker restart test; capacity outage/broker-restart scenarios |
| Both completion orders, failure independence, committed facts | `test_two_jobs_execute_independently_and_read_only_committed_facts` |
| Worker-first, drain accounting, safe recovery, live SSE and private IDs | Diagnostic live HTTP/subprocess test at normal/pre-/post-boundary loss; new acceptance stays queued after consumer drain; shared-runtime rolling compatibility tests |
| API/worker/broker restart, public behavior, privacy | Capacity worker-loss/broker-restart/wakeup-loss plus cross-process SSE/ADK tests; public contract suite |
| Inspection, metrics, alerts, role isolation, reconstruction and shutdown | `deployment:validate`, secured deployment smoke; cutover smoke includes reconstruction and real shared-runtime settlement tests |
| Complete tuned final gate | Full Task 4.5 certificate, two clean runs with all ten capacity/privacy scenarios each |
