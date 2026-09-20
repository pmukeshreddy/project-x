# M5 automated qualification and admission

New qualification succeeds when all automated execution, control, history and
package checks pass. It does not require SSH enrollment or a human signature and
creates no human-review claim. `verify_accepted` authenticates the selected M5
job, complete execution ledger and current quarantine before release or training.
The retained Click fixture is historical diagnostic evidence, not a newly
qualified real task.

```python
from feature_rl.qualification import (
    QualificationService, QualificationPolicy, ControlDiagnosis,
    RepairHistory, RepairAttempt,
    QualificationRejected, QualificationRecoveryRequired,
    QualificationPublicationFailed,
)

service = QualificationService(
    store=controller_store, registry=registry,
    grader=actual_m4_grading_service, builder=actual_m6_task_builder,
    revision=exact_m5_revision, policy=QualificationPolicy(...),
)
result = service.qualify(built_t0_ref)
```

Actual callable APIs:

```python
QualificationService(*, store, registry, grader, builder, revision,
                     policy=None)
qualify(task_version: ArtifactRef) -> OperationResult
verify_accepted(task_ref: ArtifactRef, report_ref: ArtifactRef) -> QualificationReport
recover(claim: Registry.Claim) -> OperationResult
retry_publication(error: QualificationPublicationFailed) -> OperationResult
affected_versions(task_version: ArtifactRef) -> Registry.TraceReport
```

The actual controller ArtifactStore, Registry, M4 GradingService, M3 runtime and
M6 TaskBuilder share one store. The builder is configured with T0's exact
construction revision. M5 invokes `TaskBuilder.solver_package(T0)`; it does not
build or rewrite solver bytes. `Q.task` is exact unqualified BUILT T0. For later
qualified/calibrated/released Tn, `verify_accepted(Tn,Q)` compares every validated
canonical payload field except `state` and `qualification` to T0 and requires
`Tn.qualification == Q`. M6 separately verifies legal Registry transition records.
A state string alone never satisfies that lifecycle gate.

`qualify` freezes a private policy, derives the explicit source-only H submission,
executes actual grades, diagnoses controls without supplied diagnoses from their
retained Factory authorship and observed outcomes, and freezes its report and summary. The first result artifact is the private M0
QualificationReport. Every RunBinding joins exact task, H projection, submission,
seed, selected Registry grade job, M4 receipt and distinct M3 operation/workspace
identities. A complete passing report has `disposition="success"`, no rejection
reasons. It can be passed directly to
`Factory.release(..., accepted_report=report_ref)`. There is no signed task-admission API.
Completed retry returns the selected immutable result without workers.

H remains exact full `SourcePair.reference` in T0. `derive_reference(store,T0,
sandbox_policy)` compares bounded B/H archives, accounts for every changed path
against M1 classification, and passes permitted implementation changes through
actual M4 SubmissionService. Mixed labels no longer require manual scope approval:
M5 independently classifies paths and includes only source changes allowed by the
frozen contract/runtime policy. Documentation, tests, build/dependency files and
out-of-policy changes retain their B versions, with every exclusion and B/H hash
recorded in the projection. Contradictory non-mixed classifications still reject.
The projected source must contain an implementation delta and pass all actual
feature/compatibility/control gates; a feature that depends on an excluded change
cannot qualify merely because its source history was screened successfully.

The baseline must execute every case, preserve mandatory compatibility assertions,
and fail exactly the designated missing mandatory feature requirements. At least
three fresh and three interrupted/reset reference executions are required. Reset
saves a permitted canary mutation, observes an actual fixed bounded timeout with
cleanup, restores exact initial saved source, advances generation and rejoins the
restored source to the exact H submission before fresh regrading. Validation reads
the actual dirty saved source and timeout receipt. Duplicate grade, case, workspace
or interruption identities cannot manufacture independent runs. M5 recomputes M4's
closed comparisons from exact recorded command output and private-input hashes.

Controls are actual `m4-submission` refs in frozen VerifierBundle. A ControlDiagnosis
binds ID, validity, expected failure mode and exact requirement targets to evidence.
Required categories are omission, plausible_wrong, hardcoded, regression,
adversarial and alternative_positive. Each mandatory requirement needs a runnable
targeted omission; diagnosed equivalent exclusions do not count. All nine attacks
are required: forged_verdict, evaluator_detection, hardcoded_inputs,
skipped_execution, protocol_manipulation, excessive_output, dependency_shadowing,
path_link and retained_state. Syntax/import/protocol/infrastructure failure does
not satisfy semantic omission. For targeted semantic coverage, every process-mode
case must have actual clean adapter evidence with normal exit zero; M4 may still
correctly classify nonzero process output as completed comparisons for ordinary
grading or other control modes. A nonzero process comparison alone cannot establish
a runnable omission and is not relabeled infrastructure. A legitimate missing new
public symbol must be handled exactly by the fixed adapter and emitted as an
explicit compared observation with normal completion (for example, `present=false`
for that exact absent module). Blanket exception handling is not that distinction.
`assess_outcome(..., store=controller_store)` reads process evidence at this gate;
missing process evidence fails closed. Alternative author inputs bind B and visible
contract/public context; independence evidence is required separately and is part
of the frozen qualification package. Imported or synthetic author metadata alone
cannot establish it.

Missing `QualificationPolicy.controls` entries no longer skip execution. M5 runs
those controls once within the existing grade-call/wall budget. Their RunBindings
freeze `mode=null` before execution; the resulting `ControlDiagnosis` entries are
stored in `QualificationSummary.control_diagnoses`, without changing the frozen
input policy. Supplied diagnoses, including unresolved/equivalent declarations,
retain their existing behavior.

Automatic diagnoses require the exact successful M6 control-authoring operation
selected in the complete construction history, its ControlRecord, and its validated
real generation archive. Attack names come from that frozen authoring request,
not the control ID. Semantic negatives must run cleanly and fail exactly their
original targets. Adversarial rejection must match the named attack's failure
mechanism. Passing negatives are unresolved, never automatically equivalent.
Alternatives must pass all mandatory comparisons and their actual authoring
contexts must contain only B, the visible contract and public checks. Missing
origin evidence, diagnostic-only generation, unrelated failures and unverifiable
independence remain unresolved and cannot satisfy coverage.

The diagnoses describe behavior on the frozen cases, not a proof of correctness
on unseen inputs. Release/admission recomputes them from the exact selected M4
results and M6 archives, including input-independence evidence. Summary dependency
links retain quarantine propagation. Recovery reuses completed grade jobs and
reconstructs the same diagnoses; it makes no authoring-model call.

M6 owns global candidate/stage accounting. Policy fields `repair_history`,
`repair_history_job` and `factory_revision` identify a complete `m5-repair-history`
selected by a completed M6 `construct` Registry operation with exact candidate in
its inputs/trace. RepairHistory contains candidate, initial evidence, ordered
diagnosed RepairAttempts with before/after versions and costs, and authentic
journal refs. Known M3 recipe repairs appear exactly once. At most two repairs
per stage and four per candidate are allowed. M6's authentic orchestration must
supply this seam; M5 does not infer unknown upstream usage or add a global ledger.
An incomplete history remains provisional. M0's required integer repair field
cannot represent unknown: the summary has `repair_count: null` and an explicit
missing gate, which acceptance rejects.

Registry execution intents expose unknown attempted costs before dispatch. Wall
budgets include recovery downtime using the original intent time. Original cost
records remain in `m5-operation-costs`; per-category reconciliation preserves
unknown channels as null. Qualification report costs aggregate its selected child
runs. Automated admission adds no human minutes or signature-verification costs.
Parent aggregates must not be summed again with the same child Registry records.

Missing evidence is provisional; observed semantic defects, invalid evidence,
unsupported semantics, environment failure and exhausted budgets retain typed
M0 dispositions and machine-readable reason prefixes. Known false acceptance,
false rejection, disagreement and flakiness quarantine the exact task through
Registry after preserving its selected failed result. `affected_versions(T0)`
returns active notices and affected artifacts/jobs/runs/checkpoints. Every accepted
consumer consults current quarantine, including dependencies and later manifests.

`recover(claim)` reuses selected completed subjobs. An unobserved running worker
or reset remains an explicit QualificationRecoveryRequired with its claim; it is
not redispatched. Supervisor reconciliation/abandonment/retry uses the existing
Registry API and its attempt caps. `retry_publication(error)` preserves exact
frozen bytes, timestamps, costs and parent continuation through CAS,
M4 receipt or Registry acknowledgement failures. Retain the private exception
capability. A process lost before recoverable publication retains unknown attempt
costs and requires reconciliation. Execution evidence and quarantine are checked again before successful publication
is retried.

New `m5-run-configuration-v2` records explicitly contain `source_dependencies`:
the exact B and the inner `m4-source-delta` that M4 consumes from a valid submission.
M5 registers/checks those leaves before grade-job enqueue and checks them again on
reuse/admission. Invalid envelopes that M4 rejects before consuming a changes blob
remain eligible for source-rejection controls. These records require the current M5 implementation revision and requalification
of packages constructed through retired interfaces.
