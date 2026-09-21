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

The original core handoff consumed already supplied real M0 values. The completed checker/control authoring APIs below now construct those values through reviewed M2 interfaces. Tests remain explicitly hand-authored diagnostic contracts, not a substitute model-authored feature contract or qualification result. See `docs/reports/M4.md` for evidence and remaining semantic/empirical gates.

## Checker and control authoring (code-completion handoff)

Checker and control authoring use the M2 `CodexGenerationProvider` and existing
evidence API, including `control_authoring` and `alternative_authoring`.
Focused tests use explicit test-only Codex subprocess responses through the real
schema, archive, cost and finalizer paths. No model-authored environment or
qualification is claimed by the Codex/Astra refactor.

The default factory authors one `CheckerFragmentProposal` per frozen scenario.
Each bounded response contains only API action bodies, input domains, observation
types, and typed assertions. It contains at most four cases and 32 KiB. Each
Python action body takes `inputs` and returns ordinary observations; the controller
supplies JSON transport, dispatch, IDs, mandatory flags, versions, timeouts and
exact oracle evidence from the frozen scenario. Expected values and input domains
remain private controller artifacts, separate from the worker action bodies.

`build_fragment_request` takes `request_id`, `response_id`, `prompt_id`, `contract`,
`contract_ref`, `plan`, `plan_ref`, `sources`, `limits`, and `scenario_id`.
It binds the selected scenario explicitly, including when multiple scenarios
share the same requirement IDs. `CheckerFragmentService` resolves the exact
stored evidence, validates the complete selected scenario, and retains its
`m4-checker-fragment` and `checker-fragment-authoring-journal` through the existing
provider/archive/cost/recovery path. Action syntax is compiled for validation but
never executed on the host. Worker execution remains in M3.

`Factory.assemble_checker(candidate, inputs=checker_inputs, fragments=refs)`
authenticates each fragment against its completed terminal Factory authoring job,
rejects missing/extra/duplicate scenario coverage, and deterministically assembles
the existing `CheckerProposal`. The unchanged `CheckerFinalizer` validates and
publishes the real `VerifierBundle`. Assembly has its own durable operation and
can be recovered without model calls. Construction history includes both the
original authoring receipts and the assembly record; failed old calls and their
costs are retained.

The controller formats assembled Python source compactly before applying the
unchanged 65,536-byte adapter limit. It requires the complete syntax tree to match
before and after formatting and compiles the result without executing it. Every
probe, literal, identifier and statement remains present; a genuinely oversized
adapter still fails validation.

The monolithic `CheckerProposal` format remains the internal assembly/finalizer
format and the decoder for retained authoring receipts. Default environment
creation does not request that large response from Astra. Controls and H are
absent from checker generation contexts.

`CheckerFinalizationInputs` requires exact `contract`, `scenario_plan`, `baseline`,
`environment`, `output_limit_bytes`, `visibility`, `provenance`, `costs`, and optional
`public_examples=()` and `controls=()`. Empty controls mean qualification coverage
has not been supplied. Examples must be declared public contract checks. A recipe
must bind exactly one actual M3 `sandbox-policy`. Unsupported seeds, resource
caps, types, operands, oracle joins, family coverage or downgraded mandatory cases
reject explicitly. Construction and grading call the same mechanical bundle
validator. This validation does not establish meaningful behavioral coverage;
M5 must challenge the frozen checker.

`CheckerFinalizer(store=..., resolver=...).prepare(proposal, inputs, sources)`
returns `PreparedChecker`; `.publish(store)` returns a real M0 VerifierBundle ref.
It uses a temporary actual M0 CAS under the controller store to validate exact
component identities before durable publication, then retains the bounded source,
input and comparison bytes. It does not fabricate a TaskBundle or require H.

Controls use a separate service and provider stage:

```python
request = build_control_request(
    request_id=..., response_id=..., prompt_id=..., store=controller_store,
    resolver=resolver, inputs=control_inputs, sources=grounded_sources,
    limits=generation_limits,
)
control_service = ControlAuthoringService(
    provider=provider, store=controller_store, resolver=resolver,
    revision=git_revision, evidence_scope="real_integration",
)
control_result = control_service.generate(candidates, control_inputs, grounded_sources)
# control_result.control: actual M0 ControlPatch, patch is an m4-submission
# control_result.record_ref: m4-control-record; record.qualification == "unverified"
```

`ControlFinalizationInputs` fixes M0 `control_id`, `category`, `requirement_ids`,
`expected_valid`, `expected_reason`, plus `baseline`, `contract`, `environment`,
`provenance`, `costs`. Optional `scenario_plan` and reference material belong only
to negative `control_authoring`. Reference material requires `source_pair`,
`reference` and explicit `reference_excerpts`: bounded `ReferenceExcerpt(context_id,
path, line_ranges)` values. The actual M0 SourcePair must bind exact B/H; excerpts
are reconstructed from that private source archive under M3 source bounds. H is
never relabeled as B. This branch was checked only with synthetic reference bytes.

`alternative_positive` selects `ALTERNATIVE_AUTHORING`, requires
`expected_valid=True` and no omission targets, and rejects reference/scenario
inputs. Its actual prompt includes only B excerpts, the visible frozen contract
and admitted public checks. Runtime discovery and request retrieval are validated
internally but omitted from this prompt. `author_provenance.inputs` records
exactly the consumed source refs, matching M5's admitted alternative input set.
Expected validity is a controller-selected hypothesis, not a finding.

`ControlProposal(files, deletions, rationale)` accepts exact edits to existing files
(`SourceEdit(path, replacements=(TextReplacement(before, after), ...))`) or explicit
new files (`SourceCreation(path, source, executable)`). Each anchor must occur exactly
once, including overlapping occurrences. Edits preserve unseen source and existing
executable modes; creation cannot overwrite a baseline file. The resulting bounded
delta passes the existing SubmissionService policy. Path aliases, conflicting changes
and unauthorized roots reject without executing source. The proposal cannot author verdicts, provenance,
costs or independence evidence. `ControlFinalizer.prepare(...)` returns
`PreparedControl`; `.publish(store)` publishes the private control record and its
submission. `ControlFinalizer.import_submission(submission, inputs, sources,
rationale=...)` supports explicitly supplied controls, including intentionally
malformed archive attacks: the outer submission/B join is checked, while M5 must
measure and diagnose the declared source/protocol/semantic failure. Such imports
are not automatically model-origin or independent.

After controls have been authored, `CheckerFinalizer.attach_controls(verifier_ref,
control_record_refs, baseline=..., environment=...)` returns a `PreparedChecker`
with a new VerifierBundle, preserving case/component refs and binding both the
prior verifier and exact control records in provenance. It requires exact
B/contract/environment joins and rejects duplicate control IDs. This path makes
no generation or worker call. Controls can instead be supplied at initial checker
finalization. M6 must collect the required categories/targeted omissions/attacks
and pass separately grounded M5 `ControlDiagnosis` values; absent controls or
independence remain missing gates.

For future native alternative calls, the evidence seam is concrete:

- `ControlAuthoringResult.record_ref` is the private `m4-control-record`, containing
  exact B/contract/environment, M0 control, `author_contexts`,
  `generation_provenance`, costs and explicit `qualification="unverified"`.
- `result.generation.record` identifies the actual attempt/request/response and
  timestamp. Its `archives` map retains `generation-attempt`, `request`, `retrieval`,
  `schema`, `options`, `provenance`, `events`, `status`, `usage`, `cost`, `response`.
  The service verifies this archive closure against the exact request, output
  schema, accepted result, usage and cost on fresh and recovered outcomes.
- `result.record.generation_provenance.evidence[-1]` records producer, command
  `("generate", request_id)`, time, revision, explicit evidence scope and those
  archive refs. Provider event/provenance archives preserve the Codex turn, configured Astra
  model, reported usage, and CLI version. M6 should use distinct
  request/response/prompt IDs for each actual alternative call, retain the complete
  record, and bind these existing refs in M5 diagnosis/independence evidence.

These artifacts establish what the selected provider recorded; they do not prove
semantic validity, human review or independent origin by themselves. Test-double
records are `unit_diagnostic`, with no native/model authorship claim. Future
native calls require the configured Codex/Astra provider and separately assessed
independence; M5 owns that assessment and human approval.

Each service permits one initial candidate and at most two diagnosed repairs.
Changing IDs alone is not a repair. Prior rejected journals must match exact stage,
input binding and sequence. M6 owns global candidate/stage accounting across
checker and control jobs; this local limit cannot replace that aggregate budget.
Generation failure, rejected-output cost and successful generation receipts remain
in their immutable journals. Successful artifact costs include the exact provider
cost once; control record costs and component refs remain traceable instead of
being silently folded repeatedly into the parent verifier.

Recovery APIs preserve work rather than calling the model again:

- M2 `GenerationProviderError.replay_result(store.put_bytes)` / `.replay_error(...)`
  recover provider publication. Pass that outcome as `recovered_result` or
  `recovered_error` with exactly one matching candidate; complete archives are
  validated before consumption.
- M2 `AuthoringJournalPublicationPending.replay(store)` publishes the retained
  rejected journal; resume with a diagnosed changed candidate and returned refs.
- M4 `AuthoringPreparationPending.replay()` retries inert preparation using the
  retained authenticated generation response and original costs.
- `CheckerPublicationPending.replay(store)` and
  `ControlPublicationPending.replay(store)` publish exact retained component,
  journal and final artifact bytes; they return their normal authoring result.
  Repeated publication is idempotent and does not execute source or generation.

Retain these private exception capabilities until publication succeeds. A lost
process without a retained outcome requires upstream provider/Registry
reconciliation, not an assumed free retry.
