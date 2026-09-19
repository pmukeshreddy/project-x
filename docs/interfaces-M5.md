# M5 qualification and external review boundary

Implementation submitted for independent review. No externally approved
qualification has been executed. The explicitly synthetic Click fixture completed
actual M3/M4/M5/M6 execution and remained provisional, as required.

```python
from feature_rl.qualification import (
    QualificationService, QualificationPolicy, ControlDiagnosis,
    RepairHistory, RepairAttempt, SSHHumanVerifier,
    QualificationRejected, QualificationRecoveryRequired,
    QualificationPublicationFailed,
)

service = QualificationService(
    store=controller_store, registry=registry,
    grader=actual_m4_grading_service, builder=actual_m6_task_builder,
    revision=exact_m5_revision, policy=QualificationPolicy(...),
    attestation_verifier=None,
)
result = service.qualify(built_t0_ref)
```

Actual callable APIs:

```python
QualificationService(*, store, registry, grader, builder, revision,
                     policy=None, attestation_verifier=None)
qualify(task_version: ArtifactRef) -> OperationResult
accept(review_request: ArtifactRef, attestation: ArtifactRef) -> OperationResult
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
executes eligible actual grades despite missing independent review/history gates,
and freezes its report and summary. The first result artifact is the private M0
QualificationReport. Every RunBinding joins exact task, H projection, submission,
seed, selected Registry grade job, M4 receipt and distinct M3 operation/workspace
identities. Completed retry returns the selected immutable result without workers.

H remains exact full `SourcePair.reference` in T0. `derive_reference(store,T0,
sandbox_policy)` compares bounded B/H archives, accounts for every changed path
against M1 classification, and passes permitted implementation changes through
actual M4 SubmissionService. Recorded documentation/test exclusions are allowed;
mixed, unrelated, build/dependency and out-of-policy changes reject.

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
not satisfy semantic omission. Alternative author inputs bind B and visible
contract/public context; independence evidence is required separately and is part
of the human-reviewed frozen package. Same-author diagnostics cannot claim it.

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
runs. Accepted Q adds authenticated human minutes and verification/publication
costs to P; the acceptance OperationResult reports only those additions. These
parent aggregates must not be summed again with the same child Registry records.

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
frozen bytes, challenges, timestamps, costs and parent continuation through CAS,
M4 receipt or Registry acknowledgement failures. Retain the private exception
capability. A process lost before recoverable publication retains unknown attempt
costs and requires reconciliation. Recovered human-verification audit results are
followed by the same `accept(request,attestation)` call; they are not admission.
Current trust/quarantine is checked again before accepted publication is retried.

Human admission uses an exact concrete `SSHHumanVerifier`, never an arbitrary
callback or identity string. It accepts `enrollment_path` and
`expected_enrollment_sha256`. Enrollment is independently administered, absolute,
root-owned, non-writable by the controller (including access checks), and read
without symlinks. A root controller cannot establish that separation and is
rejected. M5 exposes no key-generation, signing or enrollment-writing API.
Enrollment names exact Ed25519 keys/fingerprints, identities, validity and current
revocations. Its `binary_sha256` pins the platform's trusted `/usr/bin/ssh-keygen`;
Linux administrators supply their actual system binary digest. The Mac mechanism
was measured locally; no Linux human verification is claimed. Native verification
uses bounded input/output/time, a clean environment and fixed production namespace
`feature-rl-human-review-v1`. Crypto-only `verify_sshsig` does not establish human
origin and can be reused for other explicitly distinct payloads/namespaces.

Only a fully eligible execution/control/history package receives ReviewRequest:
exact T0, provisional report P, frozen policy, random challenge and expiry.
DetachedAttestation refers to exact canonical ReviewPayload bytes and detached
SSHSIG bytes. Payload binds every full request/task/report/policy reference,
challenge, human identity, approved decision, required review statement and actual
human-reported minutes. The smallest future human action is to review that frozen
package and independently sign those canonical bytes with their externally
enrolled key. No actionable request exists for the incomplete TEST fixture.

`accept` revalidates complete selected execution evidence before external native
verification. At most three distinct signature-verification audit attempts are
allowed per request. Failed signatures do not consume admission. The selected
acceptance job is keyed by request, not signature: identical successful retry is
idempotent; another attestation cannot replace the consumption. Accepted Q may add
only human review, disposition/rejection changes, provenance and costs to P; all
execution/control/reset fields remain identical. `verify_accepted` revalidates
selected origin, frozen evidence, current enrollment/revocation and quarantine.
Its trusted original consumption time permits same-subject reuse after challenge
expiry without permitting a different subject/package/policy or revoked admission.

Missing genuine human enrollment/review, independent alternative authorship,
complete control coverage and authentic upstream history remain enforced in the
retained fixture. Native diagnostic keys/messages are explicitly model-origin
`NO_TASK_APPROVAL`; private keys stay ignored under `.feature-rl/research/M5/`.
