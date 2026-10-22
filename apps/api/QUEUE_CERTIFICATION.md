# Durable queue certification (#157)

Run from the repository root with local Docker/Compose, `task`, and locked API
dependencies installed (`task sync`):

```bash
task test:queue-certification
```

This is the §17 release gate, independent of Task 4.6's executor removal. It runs
`task format`, then `task check`, then **two complete clean-infrastructure runs**.
Each run executes the existing session/migration, transport/failure-injection,
PostgreSQL persistence and focused fake-only owners, followed by all ten Task
4.4 capacity/privacy scenarios. All infrastructure and credentials are disposable;
only model/provider adapters are fake. No production environment or live provider
credentials are needed. Expect a slow run, particularly on the calibrated Pi.
Each test stage is bounded to 30 minutes; the complete functional infrastructure
phase to 40 minutes; the ten capacity scenarios to two hours (each existing driver
has a 480-second traffic limit plus bounded setup/shutdown). The owners remove
their own containers/volumes in cleanup; the outer runner sends INT before KILL
on timeout so traps can run. An externally killed host still needs Docker cleanup
of its uniquely named test projects; never delete unrelated volumes.

`tests/certification/coverage.json` is the version-controlled manifest. Its 38
unique entries include the exact canonical obligation text, executable function
owners, owning suite/target, evidence type and assertion description. A spec edit,
missing test, missing assertion, unknown evidence reference or omitted suite owner
fails validation. Validate without infrastructure using:

```bash
task test:queue-certification -- --validate-only
```

The pytest observer records **all parameterizations** and setup/call/teardown
outcomes. Skips, expected failures, collection failures, unexecuted tests and
missing evidence fail the gate. Every cited test must pass, including supplemental
lower-boundary evidence; one passing test cannot mask a missing integration test.
Privacy for item 38 requires the Task 4.2 real transport/observation test **and all
ten Task 4.4 scenarios**, whose nonempty log, trace, metric and broker captures
are checked with prohibited sentinels. Historical `capacity-results.json` is never
accepted as current execution evidence.

For development, `--functional-only` runs the functional matrix twice after
format/check but omits capacity. It deliberately writes `passed: false`, labels
missing capacity items, and cannot issue a release certificate. Existing focused
targets (`test:worker`, `test:recovery`, `test:diagnostic-canary`, `test:capacity`)
remain useful for reproducing a failure. They do not replace the complete gate.

## Evidence and failure interpretation

The runner refuses to overwrite its output directory. Default output is
`.certification/run-TIMESTAMP/` (private, ignored by Git and Docker). Set an
explicit fresh directory with `--output /absolute/path`. Each run prints a
separate PASS or MISSING/FAILED result for every §17 item. The root
`certification.json` becomes `passed: true` only after both complete runs pass;
a failed stage or timeout leaves it false. Infrastructure/capacity timeouts still
produce the per-item matrix, marking unavailable evidence as missing or failed
after bounded cleanup. The source fingerprint includes the capacity Compose
configuration, image build inputs, and deployment files; changes during a run
invalidate its certificate. Per-run `matrix.json` lists each
item's evidence booleans and the independent compatibility gate. The pytest
suite reports contain test function names, hashed parameter IDs, finite phase statuses and failure source
locations; exception messages, captured logs, locals and payloads are excluded.
`setup.json` records only the finite infrastructure phase/status, and
`execution.json` names the current or failed pytest suite. They diagnose failures
before test evidence exists without retaining raw dependency output. Parameter
IDs are hashed because pytest can otherwise embed synthetic content in them.
Match a failed evidence key to its manifest owner and rerun that focused target
to inspect its assertions locally.

PostgreSQL success and JetStream settlement are distinct assertions. Persistence
and product tests check state, attempts, tokens/deadlines, inference claims and
profile revisions, session snapshots and exact ordered completion events. Real
transport tests separately check delivery metadata, message IDs, pending/ACK
counts, redelivery availability, termination and final drain. The new replica
matrix combines stale/future/running/early notifications on two connections;
the retention case holds a real terminal notification while publication confirms
a previously requested generation, then proves the row remains resolvable without a new
attempt. The existing canaries cover actual product effects at these boundaries.

`capacity.json` retains only Task 4.4's allowlisted aggregate report: environment
fingerprints/effective settings, PostgreSQL connection maxima and durable-effect
uniqueness, **separate** JetStream consumer observations/final pending/ACK counts,
latencies, per-role capacity, shutdown exit codes and budget gates. Broker drain
alone never certifies product success. Failed scenarios preserve any available allowlisted measurements, force a
failed completion gate, and record only a finite error category and public source
location. A bounded driver-log tail can supply that location; its messages and
content are discarded. A failure before measurements exist records the failed
stage. Raw capacity captures are deleted on both success and failure. For deeper local diagnosis, rerun the named `task test:capacity --
--scenario NAME` command, whose private captures follow [CAPACITY.md](../../deploy/CAPACITY.md).
Never publish raw operational captures. Retained certification reports contain
only bounded test metadata and aggregate observations, not user content or
connection strings. Delete old `.certification` runs according to local disk
budget; they are not an application audit store.

Compatibility is explicit in `test_queue_compatibility.py`: actual NATS 2.12.1,
locked nats-py/asyncpg/SQLAlchemy/google-adk, PostgreSQL 16, native async driver,
returned pool checkouts, durable ADK schema/resource closure, and real consumers
with the selected AckWait/MaxAckPending/retention. Existing ADK subprocess resume
and pinned transport tests supplement this check. `deploy/capacity.json` must
match typed defaults; the load harness uses the Task 4.3 production surface with
two API maintenance hosts and two workers per role. Its workload-isolation,
privacy, outage, restart and timing budgets remain unchanged.

## Coverage decisions

No runtime or public API changes are needed for this gate. The focused tests
already prove acknowledgement-window loss, concurrent maintenance claims,
reconciliation cancellation/rollback, broker reconstruction, protected-write
fencing, exact diagnostic exhaustion/interruption, bounded ambiguous profile
replay, process loss, ADK resume, SSE hint/poll recovery and worker-first
version overlap. The added tests close concurrent mixed-delivery, retained
notification/cleanup and explicit locked compatibility gaps. Multi-process
API/worker/broker interruption and tuned workload isolation are independently
required from the existing capacity harness, rather than simulated by another
monolithic integration test. Legacy executor removal remains Task 4.6.

The integrated session run also uses separate application and ADK fixture pools,
including in its resume subprocess. Earlier fixtures shared the ADK pool for
application transactions, which cannot support concurrent product transactions or
nested ensure-and-bind with the tuned single-connection API ADK pool. Matching
production ownership preserves the selected budgets while testing the real
cross-resource contract.

## Issue #157 acceptance evidence

The manifest names the executable owners behind these matrix references. A
passing release certificate requires every referenced owner in both clean runs.

| Acceptance criterion | Required evidence |
| --- | --- |
| Complete numbered coverage, no placeholders | Exact canonical text and unique numbers in `coverage.json`; AST owner validation, executed parameterizations, and fail-closed certification unit tests |
| One bounded real-infrastructure workflow with fake providers | `task test:queue-certification`; ordered disposable functional suites and ten production-topology capacity scenarios per run |
| Publication, maintenance, reconstruction, retention, restart durability | Items 3–6, 30, 32, 35–36; capacity outage, broker restart, worker/API loss, steady and burst scenarios |
| Exact delivery, attempt, deadline and settlement outcomes | Items 7–12, 16–18, 24–25, 28–29; shared-worker replica/retained-delivery tests and duplicate/timing capacity scenarios |
| Diagnostic fencing, exhaustion and no post-boundary replay | Items 9–10, 12–14, 27; protected-write matrix, concurrent/repeated recovery, exact product/event pairs and real terminal delivery settlement |
| Profile replay within one provider-call budget | Item 15; ambiguous-crash and external-process-loss tests, provider-budget contract, profile-failure capacity scenario |
| Cross-process ADK and SSE recovery | Items 14 and 33; session resume subprocesses, cross-process wake-ups, wake-up-loss and worker/API-loss capacity scenarios |
| Worker-first rolling compatibility | Item 37; real-consumer worker-first version overlap, producer rollback, retained duplicate settlement and unchanged durable attempts |
| Separate truth/transport evidence and safe failure artifacts | Per-suite reports plus capacity PostgreSQL/effect and consumer observations; privacy-sentinel, artifact-redaction and stage-timeout tests |
| Pinned dependency, async-resource and topology compatibility | Independent compatibility gate: actual server/client versions, asyncpg/PostgreSQL, returned checkouts, ADK adapter lifecycle, configured consumers and locked defaults |
| Formatting and standard verification before certification | Ordered `format` and `check` stages must pass before either clean infrastructure run; source fingerprint must remain stable |

Item 38 additionally requires the Task 4.2 observability/privacy test and all ten
fresh Task 4.4 privacy results. Legacy-executor removal remains outside this gate
and belongs to Task 4.6.

## Executed verification

`task test:queue-certification` completed successfully with the final source
fingerprint `2590c53d018c5630b83d5716c6bade85b7a243e71fe78f006f47fc8818758ed6`.
The private local certificate is
`.certification/run-20261021-233316/certification.json` and records `passed: true`.

| Evidence | Clean run 1 | Clean run 2 |
| --- | --- | --- |
| Session/migration tests | 13 passed | 13 passed |
| Real PostgreSQL/JetStream transport tests | 129 passed | 129 passed |
| PostgreSQL persistence tests | 48 passed | 48 passed |
| Focused contract tests | 173 passed | 173 passed |
| Capacity/privacy scenarios | 10 passed | 10 passed |
| Numbered matrix items | 38/38 passed | 38/38 passed |
| Independent compatibility gate | Passed | Passed |
| Peak measured PostgreSQL connections | 32 (gate: 52) | 32 (gate: 52) |

The ordered `task format` and `task check` stages passed before both clean runs.
The manifest-only validation and all 23 certification regression tests passed,
including graceful and forced timeout cleanup. Shell syntax and diff whitespace
checks passed. No required check was omitted. Both runs removed their disposable
infrastructure and raw captures; only bounded safe reports remain. Existing
unrelated containers were preserved.

These results certify the tuned local topology with deterministic fake external
providers. Deployment on different hardware and live provider behavior remain
outside this certification's capacity claim.
