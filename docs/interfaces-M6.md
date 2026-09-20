# M6 task construction and lifecycle

BUILT/package slice independently reviewed PASS through `71f0fa2`.
Lifecycle is implemented and available for scoped review. Imports:

```python
from feature_rl.pipeline import (
    BuildInputs, TaskBuilder, BuildRejected,
    BuildRecoveryRequired, BuildPublicationFailed,
)

builder = TaskBuilder(store=controller_store, registry=registry,
                      revision=exact_m6_implementation_revision)
inputs = BuildInputs(
    source_pair=source_pair_ref,
    contract=contract_ref,
    scenario_plan=scenario_plan_ref,
    verifier=verifier_ref,
    environment=prepared_environment,  # actual M3 PreparedEnvironment
    baseline_files=tuple_of_every_explicitly_allowed_B_file_path,
    invocation=construction_invocation_id,
)
result = builder.build(inputs, owner=controller_id, claim_key=dispatch_id)
```

Actual APIs:

```python
TaskBuilder(*, store: ArtifactStore, registry: Registry, revision: str)
build(inputs: BuildInputs, *, owner: str, claim_key: str) -> OperationResult
recover(claim: Claim) -> OperationResult
retry_publication(pending: BuildPublicationFailed) -> OperationResult
solver_package(task_ref: ArtifactRef) -> bytes
```

`solver_package(T0)` is the M5 package validation/inspection boundary. Call it on
the builder configured with the exact M6 revision that produced T0. It resolves
the complete original BUILT root and frozen receipt, rechecks input joins, runtime
policy and quarantine, compares all public component bytes and the complete
inventory, and returns the exact already-frozen uncompressed tar bytes. It does
not assemble a replacement package or approve/qualify/release the task. M5 binds
human review to this exact T0 and reviewed evidence/policy; it must not rewrite
the returned bytes after approval.

T0 is an actual immutable M0 `TaskBundle(state=BUILT, qualification=None)`.
`reference_solution` is the exact raw `SourcePair.reference` H. Source-only H
projection and qualification controls belong to M5. The M6 builder neither changes
H nor runs source/build hooks/checkers. Family, lineage and partition are copied
from the exact resolved CandidateRecord. Changes to input refs, public bytes,
policy or construction snapshot require a different root.

The solver archive has only `instruction.md`, `workspace/<B files>`, numbered
`public_checks/<index>` and `runtime_manifest.json`. The separate public
`m6-solver-inventory` binds the complete archive ref/raw hash and each member's
path/content hash/size/executable bit. The inventory and every SolverView component
are frozen before T0. The private receipt also binds the archive in T0 provenance.
No private task/SourcePair/adapter/oracle/provenance record is serialized into the
public projection. Runtime fields are checked against the reviewed pinned M3
Click recipe; installed target wheels are never packaged. Public check kinds are
currently `public-check` and `public-example`, with PUBLIC visibility and UTF-8
opaque bytes. Declared contract fields/public checks still require semantic review;
this packaging boundary does not authenticate their task meaning.

BuildInputs requires the complete B file allowlist, not an exclusion list. Missing,
extra, duplicate, unsafe, case-colliding, history/cache/build-product paths reject.
M3's bounded inert SourceArchive reader rejects links, traversal and nonregular
members. The resulting workspace has exactly the supplied B file bytes/modes.
No archive is extracted or executed on the controller. The recipe's exact six
dependency identities and raw bytes, policy, B join, setup/reset/environment,
neutral repair and resource values are checked. This is not a live Docker boundary
qualification; actual M3 runtime/qualification gates still apply.

The registry job binds the private serialized request and builder configuration,
allowing a missing requested dependency to receive a recorded failed operation.
An intent observation with unknown costs precedes assembly. Only the winning
claim/intent performs assembly. A frozen receipt with SolverView, inputs, UTC
time and cost snapshot is reconciled before root publication. Post-freeze recovery
reuses this exact snapshot and finishes publication/completion. A process lost
before freezing retains an unknown attempt; `recover` raises BuildRecoveryRequired
and does not repeat assembly. Explicit supervisor reconciliation/abandonment/retry
uses the reviewed registry API. Different attempts remain separately attributable.

Unavailable prerequisites return an M0 `OperationResult` with a failed disposition,
failure receipt and costs; they never produce a BUILT root. Successful construction
means packaging succeeded, not that semantic qualification passed. Replay of a
completed request returns its immutable result. Missing/corrupt CAS and registry
publication failures remain explicit; `BuildRecoveryRequired.claim` identifies the
attempt. `BuildPublicationFailed` additionally retains bounded exact receipt bytes
for `retry_publication`, without reassembly. Keep these private capabilities away
from workers. An orphan CAS object is not a selected completed job.

Construction wall time through freeze is partially measured. A separate storage
cost retains nulls for unmeasured receipt/root/registry/recovery overhead. Frozen
manifest costs stay immutable; later stage accounting belongs in the registry.
This slice does not aggregate upstream authoring receipts, implement a global
stage budget, or expose construct(candidate)/qualification/release/CLI placeholders.
Those integrations remain M6 work against actual reviewed service APIs.

No generated real Click T0 has been built: its bounded generation run exhausted three calls
without a contract/scenario. Factory tests use complete, explicitly synthetic M0
fixtures and inert test-only dependency pins; they are not feature, runtime,
qualification, release or training evidence.

The separate labeled Click runtime fixture at `da26580` did execute 13 real grades
and 39 cases through M3/M4/M5/M6. It remained provisional and has no admission.
See `docs/reports/M6-click-fixture.md`; no repetition is needed for lifecycle code.

## Actual lifecycle and released-task resolver

```python
from feature_rl.pipeline import (
    TaskLifecycle, AdmissionRejected,
    LifecycleRecoveryRequired, LifecyclePublicationFailed,
)

TaskLifecycle(*, store: ArtifactStore, registry: Registry,
              qualification: QualificationService, revision: str)
resolve_released(task_ref: ArtifactRef) -> TaskBundle
qualify(built_task: ArtifactRef, accepted_report: ArtifactRef) -> OperationResult
release(qualified_task: ArtifactRef) -> OperationResult
recover(claim: Claim) -> OperationResult
retry_publication(pending: LifecyclePublicationFailed) -> OperationResult
```

The constructor requires the same concrete controller store, Registry and M5
QualificationService; an arbitrary callback or separate registry is rejected.
Use the exact lifecycle implementation revision and M5 configuration that selected
the transitions. Its opaque configuration explicitly declares the actual M5
configuration and policy dependencies. No other index or transition ledger exists.

`resolve_released` is the actual admission callable for M7/M8. It returns the exact
stored TaskBundle only after resolving `Tn → Q → T0`, calling concrete
`QualificationService.verify_accepted(Tn,Q)` for complete accepted origin, gates,
current human enrollment/revocation and quarantine, and comparing the canonical
Tn/T0 payloads with only `state` and `qualification` excluded. It reads the exact
deterministically keyed completed M6 qualification and release jobs, their frozen
receipts, selected attempts/results and target manifests. Caller-published state
strings, orphan CAS outputs, metadata drift or a different predecessor/configuration
cannot satisfy this chain. The resolver does not enqueue, claim, transition, grade
or execute candidate source. M5 performs its current external trust verification;
the caller must provide that actual configured service, not a test gate.

The only implemented legal sequence is `BUILT → QUALIFIED → RELEASED`. Calibration
is a separate model-specific difficulty measurement and is not a release validity
prerequisite. `TaskLifecycle.qualify(T0,Q)` records the transition **after** M5 has
accepted Q; it does not generate or qualify evidence. A missing genuine human gate
returns a typed provisional operation and no target TaskBundle. Every other T0
payload field, including original provenance/costs and all solver references, stays
identical. New transition evidence/costs are in OperationResult, the opaque frozen
receipt and Registry dependencies. The final solver bytes are never repackaged.

An unknown-cost intent precedes validation. Frozen exact outcome bytes and partial
validation wall costs precede manifest publication. Failed prerequisites receive
selected typed failed results with costs. Before completing a frozen successful
transition, current M5 admission is checked again. Publication loss preserves an
exact private retry capability; recovery does not reassemble a task or reset its
attempt identity. Pre-freeze unknown work requires explicit reconciliation and is
not automatically repeated. A completed historical `recover` result is readback;
current consumption still requires `resolve_released`. Keep claim/pending capabilities
controller-private. Recovery/publication/current-trust overhead remains explicitly
unmeasured rather than being counted as zero or rewriting frozen manifest costs.
# Candidate-specific released-task consumer

```python
from feature_rl.pipeline import ReleasedTaskResolver

resolver = ReleasedTaskResolver(profiles=(actual_task_lifecycle,), revision=git_revision)
released = resolver.resolve_released(task_ref)  # exact stored TaskBundle
```

`profiles` is a tuple of one to 32 actual `TaskLifecycle` instances sharing the same controller store and Registry. Duplicate version profiles reject because their current trust could be ambiguous. Each profile supplies supported lifecycle/M5/grader/runtime/builder revisions and the current actual external human verifier. Candidate-specific qualification policies are resolved from the selected release configuration; they do not choose code, service versions or external trust. Multiple policies can share one profile. Unsupported or ambiguous selected chains raise `AdmissionRejected`.

The resolver exposes `.store`, `.registry`, `.configuration` (private `m6-resolver-configuration` ref with explicit profile dependencies) and `.revision`, matching consumer identity needs. Every call enforces exact Tn→accepted Q→BUILT T0 equality, actual legal Registry transitions and current M5/quarantine/revocation. It dispatches no grade, source, model, transition or Registry job. Existing selected CAS/configuration bytes are reasserted idempotently. Audit historical reads grant no admission. A consumer's one `TaskBuilder` must still match the accepted Q.task construction revision when calling `builder.solver_package(Q.task)`.
# Factory source admission

```python
from feature_rl.pipeline import Factory, SourceDisposition, read_source_disposition

factory = Factory(store=controller_store, registry=registry, revision=git_revision)
result = factory.screen_source(candidate_ref)  # actual complete M0 CandidateRecord
source = read_source_disposition(controller_store, result.artifacts[0])
job = registry.job(source.claim.job_id)
```

The selected source job has `operation='construct'`, `inputs=(candidate_ref,)`, private `m6-source-policy` configuration, the actual Factory implementation revision, and `invocation='m6-source-admission'`. The result contains one private `m6-source-disposition` byte record. `SourceDisposition` binds `claim`, `candidate`, `repository_family`, `request_lineage`, exact M0 `screening` and `license`, `partition`, `source_status` (`eligible`, `rejected`, `unresolved`), `disposition`, `reason`, `original_costs`, reconciled `costs`, `revision` and `recorded_at`. `version` is `m6-source-disposition-v1`.

Rejected-source audits require the exact completed job/result/claim and record, equality with the retained CandidateRecord, `source_status='rejected'`, and `disposition='candidate_rejection'`. That outcome derives only from actual rejected screening or an ineligible license. Unsupported/infrastructure/provisional/unresolved prerequisites are not semantic source rejections. This route creates no TaskBundle or RolloutRecord. Reading the record is historical, not current admission.

`Factory.recover(claim)` recovers a durably frozen source outcome; an unknown pre-freeze attempt raises `FactoryRecoveryRequired`. `Factory.retry_publication(FactoryPublicationFailed)` retries retained exact bytes and costs without source execution. These capabilities stay private. Source aggregate costs are imported once per exact immutable candidate, including reuse from another Factory revision under the same source protocol. The original selected result/revision is preserved; downstream construction references it instead of adding those costs again.
# Factory construction from complete artifacts

```python
factory = Factory(store=store, registry=registry, revision=factory_revision,
                  builder=actual_task_builder)  # default: actual builder at factory_revision
result = factory.construct(candidate_ref, inputs=build_inputs)
# success artifacts: (BUILT_T0, m5_repair_history_ref, m6_construction_result_ref)
```

`construct(candidate: ArtifactRef, *, inputs: BuildInputs | None = None) -> OperationResult` consumes the selected source prerequisite before actual TaskBuilder assembly. It returns that exact rejected/unresolved source result without entering the builder. Eligible source with missing inputs returns a selected `blocked_dependency` construct result; it manufactures no contract/scenario/verifier/root. Supplied inputs must bind the exact `SourcePair.candidate`. The Registry parent job has `operation='construct'`, `invocation='m6-construct'` and inputs `(candidate_ref, source_disposition_ref, construction_request_ref)`. Its opaque request/configuration/result refs explicitly declare consumed dependencies.

The selected parent result contains the actual builder task and an evidenced M5 `RepairHistory`; currently this retains known M3 neutral repairs and remains incomplete until authentic generation-attempt history is imported. Parent costs represent incremental controller/publication work; source and builder costs remain in their own selected jobs. `m6-construction-result` binds the parent claim, request, selected child job/result, history, exact costs, disposition, reason and revision.

`TaskBuilder.job_spec(inputs) -> JobSpec` freezes the builder's actual existing input/policy CAS identities without enqueueing or assembly. Factory uses this exact public method for deterministic child recovery. `Factory.recover(parent_claim)` reuses a completed/frozen child, preserves unknown builder attempts, and never fabricates execution results. `FactoryUpstreamPending` retains an actual `BuildPublicationFailed`; `Factory.retry_publication(pending)` delegates to the actual builder replay before completing the parent. Parent `FactoryPublicationFailed` retains its own exact result bytes. Historical completion readback is distinct from current consumer admission.

# Factory qualification, acceptance and release

```python
Factory(*, store, registry, revision, builder=None, qualification=None)
factory.qualify(task_ref, *, policy: QualificationPolicy | None = None) -> OperationResult
factory.accept(review_request_ref, attestation_ref) -> OperationResult
factory.release(task_ref, *, accepted_report=None) -> OperationResult
```

`qualification` is the actual same-store/Registry M5 `QualificationService`, supplying
its actual grader, package validator, implementation revision and current external
human verifier. Missing configuration rejects explicitly. Qualification selects the
original BUILT root's actual completed Factory construction history, selected child
result and parent revision. It binds those values into M5 policy; an inconsistent
caller history rejects. Unconstructed roots retain missing history and remain
subject to all actual M5 gates. Actual M5 dispositions are preserved.

Acceptance resolves the review request's exact frozen M5 policy and delegates to
actual M5 acceptance. Release accepts BUILT plus accepted Q, or the exact QUALIFIED
predecessor with its existing Q. It resolves the report's actual producer/policy and
uses both reviewed Registry lifecycle transitions. Missing current human trust or
other validity gates cannot create a released root. M5 and lifecycle retained
publication/recovery capabilities keep their concrete upstream APIs.
