# M6 local registry core

Status: implemented scoped core, independent review pending. No factory/CLI,
task admission, execution, approval or reward generation is performed here.
All APIs below import from `feature_rl.registry`; M0 values import from
`feature_rl.contracts`, and `ArtifactStore` from `feature_rl.artifacts`.

```python
Registry(root: Path, store: ArtifactStore, *, limits: RegistryLimits | None = None)
register(ref: ArtifactRef, *, dependencies: tuple[ArtifactRef, ...] = ()) -> ArtifactRecord
enqueue(spec: JobSpec) -> JobRecord
claim(job_id: str, *, owner: str, claim_key: str) -> Claim
abandon(claim: Claim, *, reason: str, evidence: tuple[ArtifactRef, ...]) -> AttemptRecord
retry(job_id: str, *, reason: str, evidence: tuple[ArtifactRef, ...]) -> JobRecord
reconcile(claim: Claim, observation: CostObservation) -> ObservationRecord
complete(claim: Claim, result: OperationResult, *, observations: tuple[str, ...]) -> JobRecord
recover() -> RecoveryReport
job(job_id: str) -> JobRecord
attempts(job_id: str) -> tuple[AttemptRecord, ...]
accounting(job_id: str) -> AccountingReport
observation(observation_id: str) -> ObservationRecord
events(*, after: int = 0, limit: int = 100) -> tuple[RegistryEvent, ...]
quarantine(ref: ArtifactRef, *, notice_id: str, reason: str, evidence: tuple[ArtifactRef, ...]) -> QuarantineNotice
lift_quarantine(notice_id: str, *, reason: str, evidence: tuple[ArtifactRef, ...]) -> QuarantineNotice
trace(ref: ArtifactRef) -> TraceReport
assert_usable(ref: ArtifactRef) -> None
```

The store must be an actual M0 controller store. The registry root is a private
0700 directory of ordinary 0600 files on a trusted local POSIX filesystem;
ancestor/final symlinks, linked/nonregular files and unexpected directory members
are rejected. Do not mount it or expose its claim capabilities to workers.
The root and its ancestors must remain controlled by trusted host administration;
same-user malicious concurrent filesystem replacement and ACL policy are outside
this application capability boundary. The constructor creates missing state and
recovers existing state. Existing store identity and limits must match exactly.

`JobSpec` requires `operation` (the eight M0 operation names), nonempty ordered
`inputs`, `configuration` reference, exact `implementation` revision, `invocation`
identifier and positive `attempt_limit`. The SHA-256 of its canonical JSON is the
job ID. Input ordering is significant. Configuration must contain the complete
frozen operation configuration, seeds and relevant policy identities. Intentionally
distinct episodes need distinct invocation identities. This core does not discover
missing dependencies or decide which seeds/versions an operation requires.

Every submitted reference is resolved using M0's bounded byte/typed reads.
Typed artifacts automatically contribute every embedded reference, including
provenance/evidence/consumed tasks, to dependency closure. Opaque bytes are never
parsed or executed. Explicit dependencies supplement the intrinsic references;
that declaration is immutable. Registering the same digest with different metadata
or dependencies fails. Cycles and configured closure/graph/read limits fail closed.
Job input/configuration and the selected result's evidence artifacts extend
descendant tracing through its outputs without rewriting artifact declarations.

The job states are queued → running → completed, or running → paused/exhausted
through explicit abandonment. Only a paused job can be explicitly retried, with
diagnosis/change evidence and remaining attempts. There is no expiry-based stealing
or automatic redispatch. `Claim` contains job/attempt IDs, owner, globally unique
claim key and a random token. Exact active-claim replay returns the same claim;
stale or quarantined replay fails. A repeated active claim is **not** permission
to launch the external operation twice. Reconcile supervisor/upstream state first
when dispatch or reply delivery is uncertain. A different execution needs a new
claim after explicit abandonment/retry. Claim owner strings are not authentication
of a person or machine.

## Upstream publication and cost reconciliation

M2 may already have published its call/response/cost archives before the wrapper
regains control. The wrapper supplies:

1. The original durable `Claim`, obtained before dispatch.
2. A stable `source` namespace and `upstream_attempt_id` from the actual upstream
   receipt, with exact already-published M0 `receipts` references.
3. Actual M0 `CostRecord` values, including honest null measurements. The wrapper
   must validate receipt semantics and attribution; resolving bytes does not prove
   their claims, authenticate the provider, or establish that every cost was captured.

`CostObservation(source, upstream_attempt_id, revision, receipts, costs)` is a
cumulative accounting snapshot, with one cost record per category. Revision starts
at 1 and increases contiguously. Replaying the same source/attempt/revision with the
same bytes is idempotent. That upstream attempt cannot be assigned to a different
registry attempt. Later snapshots retain all earlier receipt references and cost
categories; known numeric measurements cannot become null or decrease. Independent
upstream calls use distinct upstream IDs; repeated snapshots are not additive.
Unknown measurements stay null; measured zero stays zero.

`reconcile` is also allowed after abandonment or completion. It never turns an
interrupted attempt into success. `AccountingReport.observations` contains the
latest snapshot for every distinct upstream attempt associated with the job;
`unobserved_attempts` explicitly lists registry attempts lacking any snapshot.
Absence from that list is not proof of complete accounting: the caller remains
responsible for wrapper overhead and every upstream invocation. Historical snapshots
are available through `observation(id)` and the event stream. Never sum all revisions
or add the result's duplicate cost representation to this ledger.

Before `complete`, reconcile each actual current-attempt observation. Pass all its
latest observation IDs as a tuple. `OperationResult.costs` must exactly equal those
snapshots' cost records, concatenated in lexicographic observation-ID order. The
operation name must match the job. Every result/evidence artifact is resolved;
initial completion also checks those references against current quarantine.
This selects one immutable result snapshot; identical completion retries return
it, and changed completions fail. Later costs change accounting history, not the
selected result. Earlier abandoned attempts remain in the separate ledger. A
terminal M0 failure disposition can be recorded as a completed bookkeeping job;
it does not become operation success.

Typical real-wrapper sequence, using values supplied by the reviewed upstream
service rather than manufactured observations:

```python
job = registry.enqueue(spec)
claim = registry.claim(job.job_id, owner=controller_id, claim_key=dispatch_id)
# Execute/reconcile the actual reviewed service outside registry transactions.
saved = registry.reconcile(claim, upstream_cost_snapshot)
# actual_result is the validated M0 result with costs bound as described above.
completed = registry.complete(claim, actual_result, observations=(saved.observation_id,))
```

On an I/O exception, keep the claim and upstream receipts. Call `recover`, then
`job`/`attempts`/`accounting` or repeat the same reconciliation/completion identities.
Do not rerun a provider simply because JSONL publication or the wrapper reply failed.
If work ran but no trustworthy receipt survived, retain the running/abandoned
attempt as unobserved. The registry cannot reconstruct unknown billing or execution.

## Durability, bounds and quarantine

SQLite's immutable ordered/hash-linked events are authoritative. The materialized
index is replayed and compared against that history on every operation. Canonical
event bytes, semantic keys, ordering and sizes are checked. Mutations and their new
events commit together. Prospective materialized records must satisfy the same
count, total-byte and per-record byte bounds used by recovery before a new event
is written. A rejected growth mutation preserves the prior readable history.
JSONL publication follows the commit; every public call
first reconciles the verified projection under a local process lock and SQLite
transactions. Full history/index replay favors a small first pipeline over throughput.

Recovery appends missing committed lines, or only the missing suffix of an exact
unacknowledged final-line prefix. It fsyncs even if all lines already exist, then
commits the acknowledgement. Corrupt/unknown tails, missing acknowledged bytes,
event corruption and index drift block without automatic truncation, rewriting,
discard, or reindexing. An orphaned M0 artifact is not a completion. `recover()`
checks storage history; `job`, `accounting`, `observation` and `assert_usable`
add bounded reads of their relevant referenced artifacts.

`RegistryLimits` explicitly caps active/total jobs, attempts, artifacts, closure
count/bytes, edges, event count/bytes, journal/database sizes and lock waiting.
Default attempt maximum is 3, active jobs 32, history 10,000 events/64 MiB,
database 128 MiB, one event 256 KiB, one artifact envelope 8 MiB/decoded bytes
4 MiB, and one closure 256 artifacts/32 MiB. Capacity exhaustion fails closed;
there is no silent pruning or automatic limit increase. Budget enforcement spanning
construction stages/candidates and actual token/time/spend reservations belong to
the later factory wrapper, not this registry's attempt counter. Provision sufficient
ledger capacity before launching work; reaching a hard cap can block late receipt
registration while the durable unobserved attempt remains visible.

Quarantine notices bind exact registered refs and real supplied evidence. Tracing
includes artifact descendants, affected jobs, RolloutRecord refs and TrainingCheckpoint
refs. A later explicit lift appends a resolution and retains the notice and affected
historical trace. Damaged root bytes can be quarantined using separate intact evidence.
Quarantining selected result evidence affects the completed job, its outputs and
their consumers. Identical completion replay and historical readback still return
the saved result; quarantine does not block abandonment or late cost reconciliation.
`assert_usable` checks current storage/dependency quarantine status only. It does not
validate human review or Tn→Q→T0 admission; actual M5/M6 gates must still run. Readback
of an old completed job is historical bookkeeping, not permission to consume it.

Failures are explicit `RegistryError` subclasses: integrity/I/O/conflict/limit,
backpressure, attempt limit, claim conflict, stale claim, quarantine and unknown
identity. Invalid models raise Pydantic validation errors; M0 artifact read failures
remain M0 errors. `RegistryIOError` means the transaction may already have committed.
The implementation does not claim cross-file/service atomicity, external exactly-once
execution, power-loss durability, automatic corruption repair, backup/migration,
remote workers or untrusted-controller protection.
# Historical audit quarantine scope

```python
audit_configuration = registry.historical_audit_configuration(
    original_audit_configuration_ref,
    subjects=(validated_historical_verifier_or_candidate_ref, ...),
    protected=(current_instruction_ref, selection_ref, envelope_ref,
               payload_ref, signature_ref, ...),
)
# Use audit_configuration in the actual operation='audit' JobSpec.
```

`historical_audit_configuration(configuration: ArtifactRef, *, subjects: tuple[ArtifactRef, ...], protected: tuple[ArtifactRef, ...]) -> ArtifactRef` verifies and freezes private `registry-historical-audit-policy` bytes plus exact subject/protected reference groups. Both tuples must be nonempty, distinct and within the Registry closure limit. The original configuration and scope/group refs are automatically protected. Callers must explicitly list current audit instruction/selection/envelope/payload/signature refs; subjects come from validated historical source/patch joins.

Only an actual `audit` job using this configuration ignores notices rooted within the historical subjects' dependency closure, excluding protected roots. The same rule applies at enqueue, claim, retry and completion. Protected current-input notices and newly quarantined output roots still block. A historical audit result remains quarantined for ordinary consumers; this scope grants no release, reward, collection or training admission. `Registry.assert_usable` and all normal operation checks are unchanged. Integrity and human-trust validation remain required.

Existing JobSpec/event/model bytes are unchanged. Wrap configuration before creating a new audit job; existing immutable jobs are not retrofitted. M8 must separately confirm and record any actual applied quarantine before claiming a finding succeeded, then recover its exact report if publication fails.
