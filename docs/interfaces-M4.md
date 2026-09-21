# M4 mechanical grading core

Implemented core; independent review pending. Depends on reviewed M0 and M3, with no import of M2 requirement/scenario services. This operation can grade a BUILT task for diagnostic/control work. A grade does not authenticate a contract, establish semantic qualification, release a task, or authorize training. M5/M6/M7 must enforce their admission gates. No real model-authored frozen Click contract/scenario exists at this checkpoint.

## Public API

```python
from feature_rl.submission import SubmissionService
from feature_rl.grading import GradingService, read_grade, GradePublicationFailed

submissions = SubmissionService(store=controller_store, policy=runtime.policy)
submission = submissions.create(baseline_ref, delta_tar_bytes, deletions_tuple, allowed_changes)
# To submit an actual M3 last-confirmed snapshot (never a live workspace copy):
submission = submissions.from_saved(baseline_ref, saved_source.artifact, allowed_changes)

grader = GradingService(store=controller_store, runtime=runtime,
                       revision=implementation_revision, max_wall_seconds=600.0)
result = grader.grade(task_version, submission, case_seed)
receipt = read_grade(controller_store, result.artifacts[0])
```

`runtime` is the actual reviewed `EnvironmentRuntime`, using the identical controller store. Its engine must already have passed M3 boundary qualification. The exact M0 transport is `GradeRequest`; `grade(...) -> OperationResult` has `operation='grade'`. Supported seeds are exact nonnegative integers below 2**63, never Boolean/float coercions. Return dispositions: `success` with reward 1; `candidate_rejection` with reward 0; unsupported/invalid/infrastructure outcomes with null reward. Failed artifact publication raises typed replayable recovery because no successful immutable output can be promised while storage is unavailable.

`SubmissionService.create` takes an uncompressed inert tar of changed regular files plus explicit deletions. `from_saved` computes that delta from two bounded M3 source archives, preserving baseline files and executable modes. The frozen contract selects allowed paths and artifact types; the actual runtime profile validates dependency declarations and build inputs before execution. Archive traversal, absolute/backslash/history/cache paths, duplicate members, links, devices, sparse/compressed content, size/file-count overflows, file ancestry collisions, ambiguous trailing slashes, duplicate/missing/contradictory deletions, forbidden paths and package/result file transfers reject. M3 enforces aggregate reconstructed-source bounds again. The host never extracts or imports the source.

`m4-submission` opaque bytes contain canonical JSON validated as `Submission(version='m4-submission-v1', baseline, changes, deletions)`. `changes` references `m4-source-delta` bytes. These use existing M0 `ArtifactRef` fields and storage APIs; no shared artifact registration was added. Grade revalidates the original submission against the task's exact B and `AllowedChanges` before rebuilding. `resolve` first validates the trusted B once, independently of candidate parsing: malformed or oversized B raises `ArtifactIntegrityError` and grading returns infrastructure/null. Candidate-controlled manifest, delta and deletion violations remain candidate zeros. A candidate can store a malformed raw submission, but cannot convert it to a passing grade. Missing/corrupt storage also remains an infrastructure failure rather than a candidate zero.

## Closed checker payloads

Public models in `feature_rl.verifiers` provide exact JSON schemas through `model_json_schema()`. Their payloads are ordinary canonical JSON stored with `put_bytes`, private/evaluation visibility, and strict duplicate-key/type/size validation on reads:

| M0 extension point | M4 kind/model |
| --- | --- |
| `CaseDefinition.inputs` | `m4-case-input` / `InputPlan`: version `m4-input-v1`, scenario ID, requirement IDs, named fields with tagged `constant`, finite `choice`, or inclusive bounded `integer` domains |
| `CaseDefinition.comparison` | `m4-case-comparison` / `CaseComparison`: version `m4-comparison-v1`, scenario/requirement IDs, observation mode/schema, nonempty assertions, bounded positive timeout |
| `WorkerAdapter.code` | `m4-worker-adapter` UTF-8 Python source, at most 65,536 bytes, executed only as `/usr/local/bin/python -c <source>` inside M3's isolated runtime |

The adapter version is `m4-worker-v1`; `supported_observables` includes its declared `json` and/or `process` modes. The complete `permissions.worker_inputs` allowlist must be exactly the adapter code reference. Runtime requests add only the current case's realized inputs and ID; no comparison, expected values, full manifest, H, other cases, controller storage or verdict code is transferred. Any fixture/setup code belongs inside the untrusted worker adapter under existing M3 policy. Arbitrary extra fixtures and stateful resets are unsupported in this initial core.

In `json` mode the worker receives `{"case_id":"...","inputs":{...}}` on stdin and must exit zero with exactly one JSON object `{"case_id":"...","observations":{...}}` on stdout. Every declared field is required; extras, duplicate keys/objects, wrong IDs, unknown fields and verdict fields reject. Scalar types are string, exact integer, Boolean and null; homogeneous bounded lists of strings/integers/Booleans are supported. Floating point, nested object/callback/identity semantics and executable deserialization are unsupported. Stderr is retained and counts against the combined output cap.

In `process` mode the controller selects declared `exit_code`, `stdout`, and/or `stderr` directly from the supervisor result, with strict UTF-8 decoding. A normal nonzero process exit is an observable value in this mode; resource termination is a failure. Completion identity comes from the controller's individually scheduled invocation. Adapter-reported API exits are distinct from supervisor process exits.

Each `Assertion` has an ID, disclosed requirement IDs, an exact scenario `oracle_origin`, actual observation name, and closed `equal`, literal-text `contains`, or scalar `member` operator. Expected operands are tagged literal values, a named input (optionally with literal string prefix/suffix), or a declared observation. Equality preserves type and order; Boolean true never equals integer one. No trimming, sorting, case normalization, regex, eval, imported callback, generated controller comparator or generated controller case-generator code is supported. Static operand compatibility is validated before worker execution.

## Manifests and joins

`load_verifier(store, task_version)` resolves actual M0 task/contract/plan/verifier/recipe objects using bounded CAS reads. It enforces exact contract references, task/recipe B equality, adapter version, submission permissions, private transfers, supported resource/seed policy, duplicate-free/known IDs, exact mandatory feature-plus-compatibility ID equality, all scenario families represented, and complete requirement-to-assertion coverage. The fixed recipe's wall, CPU, memory, PID, disk and output limits must each be no greater than the contract grants; otherwise loading rejects and grading returns `unsupported_semantics` with null reward before opening a worker. Solver token/tool budgets are distinct and are not compared to worker recipe budgets. Cases cannot downgrade a mandatory obligation. Assertion origins must exactly match the corresponding scenario origin. This is mechanical consistency; source-text grounding and semantic sufficiency still require the final authoring/qualification gates.

`materialize_manifest(loaded, seed)` returns `CaseManifest`. The supported `ScenarioPlan.seed_policy.algorithm` is exactly **`m4-sha256-v1`**, with `same_cases_within_group=True`. For each field, SHA-256 of canonical JSON `[algorithm, seed, case_id, field_name]` supplies an unsigned big-endian integer; modulo selection chooses a declared finite option or inclusive integer-domain value. Case IDs/counts stay fixed. Every declared family is included on every seed; sampling cannot remove a family. This is deterministic bounded sampling, not a claim of unbiased cryptographic random sampling.

Before execution, the controller publishes `m4-case-manifest`, binding the exact task, verifier, seed, realized inputs, case IDs, requirement IDs, input-plan/comparison refs and mandatory flags. Its identity is identical across different submissions for the same task/seed. The frozen verifier is never modified to select cases. M3 rebuilds the reconstructed source from clean B, then runs each probe in a separate fresh installed-wheel container. It does not import candidate code or build hooks on the controller.

## Receipt, limits and failure recovery

`m4-grade-receipt` contains the exact `GradeReceipt` schema: version, task/submission/verifier, case seed/manifest, reconstructed source, disposition/reward/reason, expected case IDs, complete ordered case ledger, build/runtime evidence refs, cleanup verification, implementation revision and UTC time. Every expected case has a record, including `not_run` after a terminal build/infrastructure/budget failure. Completed cases carry every assertion ID, requirement IDs and comparison result. The controller alone derives these results. Missing/duplicate/malformed output, forged verdicts, crashes and resource termination cannot produce reward one. Optional comparison failures do not replace required checks; any protocol violation prevents one.

`read_grade` performs bounded strict receipt parsing, not task admission or independent reexecution. Consumers must bind its task/submission/seed/manifest to their assigned operation and trusted produced artifact reference. SourcePair/H are deliberately not resolved by mechanical grading; upstream admission owns historical/source-lineage authenticity.

M3's reviewed per-operation resource and cleanup limits apply. The additional controller wall budget (default 600 seconds, configurable positive float up to 3,600) is checked between probes; an already-running M3 operation can finish within its separate operation/cleanup allowance. This is not a hard aggregate supervisor deadline. Per-case output is capped by verifier/task/recipe limits. At most 256 cases and 8 MiB aggregate parsed checker payloads are admitted; individual input/comparison payloads are capped at 256 KiB. This core has no automatic CAS retention pruning.

Measured candidate build/import/crash/limit failures produce zero; M3's infrastructure/unresolved classification and uncertain cleanup produce null. Failed build calls retain measured external wall time and available cgroup CPU. Controller wall excludes runtime call intervals; known worker costs remain separate, and unknown GPU/currency/human costs remain null. Raw M3 receipts remain private in the same CAS.

`GradePublicationFailed` retains the exact receipt, costs, evidence scope and any M3 pending publication objects. `grader.retry_publication(error)` replays only missing retained bytes and the receipt, never source execution or inference. A recovered runtime publication still leaves that interrupted grading result unmeasured; retry grading with the same stored submission and seed/manifest. M3 pending-publication propagation is implemented using its reviewed replay method; this checkpoint separately exercised M4 receipt-publication recovery diagnostically, not a native injected M3 storage failure.

## Behavioral verifier generation

`CheckerAuthoringService.generate` consumes the frozen contract, baseline evidence,
controller-built ScenarioPlan and runtime recipe. Astra returns one
`BehavioralSpecification`: ordered groups of cases with API action bodies,
bounded input domains and expected observations. It receives no gold source.

The controller assigns scenario, case and assertion IDs, oracle evidence,
mandatory flags, observation types and resource bounds. It infers types from the
expected values and uses exact typed equality. Expected values may be literals
or realized input values with a string prefix/suffix. Generated action bodies
only invoke APIs and return observations. The controller supplies hardened JSON
transport and dispatch, then freezes private case inputs/comparisons and the
VerifierBundle. It never executes generated source on the host.

`ControlAuthoringService` authors three wrong implementations: partial,
happy-path-only and hardcoded. It adds one regression implementation when the
contract has compatibility obligations. Each proposal supplies paths and exact
text replacements. Existing files are edited while preserving their executable
mode; missing files are created from a single empty anchor. The controller
checks legal paths, anchors and source limits before publication.

Model calls retain strict schemas, private provider records, resource caps and
at most three attempts per role. A validation failure may retry without diagnosis,
changed-input tracking or a predecessor journal. There are no checker fragments,
assembly jobs, alternative positives, isolation targets or qualification repair histories.
