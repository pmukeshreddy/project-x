# M8 evaluation and audit interface — independent checkpoint

This interface implements immutable study records, validation, receipt statistics, external-corpus adaptation, and authenticated historical audits. The evaluation service consumes the real M7 runner and claimed native-session APIs.

## Frozen study

`FrozenRoster` records every source assignment, repository family, request lineage, relation proof, test source frame, exclusion record, and locked task before execution. `validate_lineage_freeze(roster)` closes fork, backport, copied-code, monorepo, descendant, same-request, shared-family, and shared-request relations through M1 `SplitPlanner`; a component crossing partitions is rejected.

`EvaluationPreregistration` freezes arm protocols, checkpoints, budgets, trial IDs, task/policy/case seeds, episode indices, metrics, comparisons, limits, harness, checkpoint selection, invalid-trial handling, and locked-test access. `validate_preregistration(roster, preregistration, config)` requires:

- exactly A starting, B SFT, C external-task RL, and D factory-task RL;
- one shared initialization, tools, action format, optimizer family, harness, development budget and evaluation limits;
- matched trained-arm resource budgets and exact C/D protocol equality;
- the complete task × arm × policy-seed × episode trial product with one unique result slot; and
- the same explicit grader case seed for paired arms. Pass@1 permits one episode only.

`AdaptationFunnel` retains upstream release/split/local partition/license text/source frame, stage losses with reasons, selection bias, observed costs and disposition. Every stage enforces `entered == accepted + rejected + invalid`, and each next stage must start from the previous accepted count. Assigning an upstream split to controlled local training is representable while keeping the upstream label; it makes no external leaderboard claim.

## Receipt statistics

`summarize_trials(roster, preregistration, trials, bootstrap_seed=..., bootstrap_resamples=...)` accepts a complete tuple of actual `TrialResult` receipts. It rejects missing, duplicate, drifted-task, drifted-family, drifted-policy-seed, or drifted-case-seed rows. Pass@1 aggregates one episode per task/policy seed. Best-of-k aggregates the frozen k-episode set once per task/policy seed; a valid-only best-of-k unit requires all k assigned episodes to be valid. It returns:

- assigned, valid, resolved, failed and invalid counts by arm;
- pass@1 rates using every assigned trial, counting invalid/unresolved assignments as unsuccessful;
- separate valid-only rates, with null estimates for empty denominators;
- paired right-minus-left task-weighted and repository-family-weighted differences; and
- deterministic repository-family clustered percentile intervals, with explicit limitations when fewer than two families exist. Policy sampling seeds never remove the explicit limitation that independently trained replicates are absent.

This function aggregates supplied receipts only. It neither executes policies nor establishes current task/policy admission.

## Evaluation execution

`EvaluationService` has two explicit constructors. Production uses `store`, `registry`, inert `NativeSessionFactory`, actual `TaskLifecycle | ReleasedTaskResolver`, `TaskBuilder`, `EnvironmentRuntime`, `GradingService`, `revision`, and `evidence_scope="real_integration"`. A labeled CPU diagnostic may instead supply one actual same-store `AgentRunner` with `evidence_scope="unit_diagnostic"`; that path cannot claim native activation.

`evaluate(config)` freezes the M8 request and the M7 native-factory configuration before claiming one Registry `evaluate` job. It resolves every roster source through current M6 admission and compares the actual `TaskBundle` partition, family and request lineage. Arms B/C/D must reference the sole selected output of a completed successful M7 train job through `validate_selected_checkpoint`; the request, full `TrainingConfig`, initial policy, algorithm, weights/version, reference checkpoint, consumed tasks, and TRAIN source-frame membership must join exactly.

After the evaluate claim, the service creates the M7 native session under its durable startup intent. For every `(arm, policy_seed)` group it records an activation intent, calls `NativeSession.activate_checkpoint`, and revalidates the live barrier immediately before each assigned trial. Each `AgentRunner.run` receives the frozen nonnegative `case_seed` and deterministic trial invocation; the service validates and consumes the runner's selected rollout/M4 receipt without grading again.

Before native shutdown it freezes an `m8-evaluation-execution` receipt containing every assigned trial, selected run result and activation. Report-publication recovery consumes that receipt without another rollout. A retained failed shutdown is retried before report completion; lost-process cleanup remains unresolved rather than being silently credited. Registry completion binds startup, activation, run, shutdown and report accounting with unknown GPU/USD fields retained.

## Human audit

`AuditService(store, registry, human_verifier, selection_manifest, attestations, revision).audit(run_ids)` uses the actual controller `Registry`, M4 receipts, M5 `SSHHumanVerifier`, and M6 `SourceDisposition` records. It registers every opaque frame, plan, selection, attestation and configuration dependency before creating one durable Registry `audit` job with accounting.

Before enqueueing, it validates the source/patch joins and creates an M6 `historical_audit_configuration` whose subjects are those exact historical verifier/source roots. Current frame, plan, selection, ordinary configuration, attestation envelope, signed payload and signature remain protected. This scope permits the audit to inspect or quarantine historical subjects without granting normal admission or bypassing a notice on current audit instructions.

For each frozen selected run it requires a completed registry job whose operation is `run`, exactly one `RolloutRecord` with `rollout.run_id == job_id`, and exactly one M4 grade receipt bound to the same task, submission, disposition, reward and sole grader seed. Unknown, incomplete, ambiguous or drifted records reject.

`AuditPopulationFrame` contains actual patch runs and true rejected-source construct jobs. `AuditSamplingPlan` stratifies by unit, repository family, actual status and failure category. `derive_selection_manifest` deterministically ranks each frozen stratum from its explicit seed and derives `sample_size / population_size`; the service recomputes the complete selection, validates every population subject against Registry/M4/M6, and refuses a partial `run_ids` list. Missing selected attestations remain explicit unresolved outcomes.

The canonical signed patch payload separately binds patch validity, environment validity and checker assessment to the selected sample, run, task, verifier, submission and reproducible counterexample. An infrastructure outcome cannot establish a checker defect. The separate source payload binds the selected M6 construct job, exact CandidateRecord, M6 disposition/source evidence and counterexample. Source findings never enter patch denominators and quarantine the actual source version rather than a fabricated verifier. Verification uses the M5 namespace `feature-rl-human-review-v1`, live pinned external enrollment, revocation list, enrolled Ed25519 key and pinned native binary digest. SSHSIG validity alone is not treated as human origin.

`summarize_audits` reports inverse-probability weighted `invalid_among_accepted` (conditioned on verifier acceptance), `accepted_among_invalid` (conditioned on human-invalid patches), and `rejected_among_valid` (conditioned on human-valid patches). Empty or unresolved denominators return null estimates and unavailable/invalid-environment outcomes have separate accounting. `summarize_source_audits` reports the separately weighted valid-source rate among rejected-source units.

Only an explicit authenticated checker-defect adjudication in a valid environment quarantines the verifier. The service traces descendant rollouts/checkpoints and records the required regrade plus unaffected-restart-or-contamination-disclosure action. Historical inspection does not grant current task or policy admission.

The report freezes exact `QuarantineAction` records before mutation. `retry_publication(pending)` republishes retained report bytes without repeating adjudication, and `recover(claim)` replays idempotent quarantine effects and Registry completion from the selected accounting receipt. An existing conflicting or lifted notice is never silently replaced.

No real human enrollment, signed adjudication, frozen task roster, learned policy checkpoint, external corpus payload, GPU run or experiment result exists in this checkpoint.

## External corpus adaptation

`ExternalCorpusAdapter(store, factory, configuration, revision).adapt()` consumes only caller-supplied private row artifacts. It never downloads a row or calls a model. The strict row schema binds the pinned SWE-Bench++ fields `repo`, `instance_id`, `base_commit`, `created_at`, `language`, `task_type`, `repo_type`, `difficulty`, `problem_statement`, `patch`, `test_patch`, `FAIL_TO_PASS`, `PASS_TO_PASS`, and `environment_config` at dataset revision `da364537055b9bb5091783af78a02b6a3bc0e130` and harness revision `f938edd189049806fef7a76fdf01f0da55baa565`.

Every row requires a private `ExternalOriginMapping` to actual M1 `CandidateRecord` and `SourcePair` artifacts. The adapter verifies the common M1 `source-inspection-log`, its Git object-tree IDs and reconstructed patch digest, commits, exact SourcePair archives, family, request lineage, partition, changed paths, repository license state, intended-use classification, and request/test/native-case/environment digests. Git object IDs are retained as such; they are not equated with the archive format's independent SHA-256 tree encoding. The authoring request URL/body/source-response, authoring B archive and authoring license must be the exact M1 refs. The external source family and request lineage must not overlap the `LOCKED_TEST` members of the validated frozen evaluation roster carried by `frame.exclusions`. Missing origin facts, unresolved intended use, unverified rights, unbound exclusion evidence, or digest drift reject before Factory work.

The only solver-facing list contains the explicit PUBLIC/AUTHORING request, fresh B archive and license evidence. The private row, solution patch, test patch, native case names, environment hint, H and private provenance never enter that allowlist. `FAIL_TO_PASS` and `PASS_TO_PASS` are fingerprinted metadata and are never treated as reward evidence.

Rows inside the frozen language/task-type allowlist first consume the selected `Factory.screen_source(candidate)` result, then delegate to `Factory.construct(candidate, inputs=...)`. Each row has an aligned `BuildInputs | None` slot; a missing slot produces M6's real BLOCKED construction result, while supplied M2/M3/M4 refs must bind the mapped `SourcePair` and are validated by the real builder. The metadata → origin → source-screen → construction funnel keeps both selected results and their costs. Construction success still does not imply M5 qualification, M6 release, M7 collection, training, or corpus execution. The published private batch and every opaque input/dependency are registered in the same Registry.
