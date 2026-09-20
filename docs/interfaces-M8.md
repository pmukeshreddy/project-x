# M8 evaluation and audit interface — independent checkpoint

This interface implements immutable study records, validation, receipt statistics, and authenticated historical audits. The evaluation service consumes the real M7 runner and claimed native-session APIs.

## Frozen study

`FrozenRoster` records every source assignment, repository family, request lineage, relation proof, test source frame, exclusion record, and locked task before execution. `validate_lineage_freeze(roster)` closes fork, backport, copied-code, monorepo, descendant, same-request, shared-family, and shared-request relations through M1 `SplitPlanner`; a component crossing partitions is rejected.

`EvaluationPreregistration` freezes arm protocols, checkpoints, dataset/construction roots in `training_sources`, budgets, trial IDs, task/policy/case seeds, episode indices, metrics, comparisons, limits, harness, checkpoint selection, invalid-trial handling, and locked-test access. `validate_preregistration(roster, preregistration, config)` requires:

- exactly `base` and `feature_grpo`;
- one shared initialization, tools, action format, optimizer family, harness, development budget and evaluation limits;
- a frozen training configuration and resource budget for `feature_grpo`;
- the complete task × arm × policy-seed × episode trial product with one unique result slot; and
- the same explicit grader case seed for paired arms. Pass@1 permits one episode only.


## Receipt statistics

`summarize_trials(roster, preregistration, trials, bootstrap_seed=..., bootstrap_resamples=...)` accepts a complete tuple of actual `TrialResult` receipts. It rejects missing, duplicate, drifted-task, drifted-family, drifted-policy-seed, or drifted-case-seed rows. Pass@1 aggregates one episode per task/policy seed. Best-of-k aggregates the frozen k-episode set once per task/policy seed; a valid-only best-of-k unit requires all k assigned episodes to be valid. It returns:

- assigned, valid, resolved, failed and invalid counts by arm;
- pass@1 rates using every assigned trial, counting invalid/unresolved assignments as unsuccessful;
- separate valid-only rates, with null estimates for empty denominators;
- paired right-minus-left task-weighted and repository-family-weighted differences; and
- deterministic repository-family clustered percentile intervals, with explicit limitations when fewer than two families exist. Policy sampling seeds never remove the explicit limitation that independently trained replicates are absent.

This function aggregates supplied receipts only. It neither executes policies nor establishes current task/policy admission.

## Evaluation execution

`EvaluationService` has two explicit constructors. Production uses `store`, `registry`, inert `NativeSessionFactory`, actual same-store `TaskLifecycle | ReleasedTaskResolver`, `TaskBuilder`, `EnvironmentRuntime`, `GradingService`, `revision`, and `evidence_scope="real_integration"`. A labeled CPU diagnostic may instead supply one actual same-store `AgentRunner` with `evidence_scope="unit_diagnostic"`; that path cannot claim native activation.

`evaluate(config)` JSON-revalidates and freezes the M8 request plus the M7 native-factory configuration before claiming one Registry `evaluate` job. It resolves every roster source through current M6 admission and compares the actual `TaskBundle` partition, family and request lineage. The `feature_grpo` arm must reference the sole selected output of a completed successful M7 train job through `validate_selected_checkpoint`; the request, full `TrainingConfig`, initial policy prompt/harness/sampling controls, algorithm, weights/version, reference checkpoint, consumed tasks, and TRAIN source-frame membership must join exactly. Selected `NativeSettings` retain the training controls, and the evaluator must use the same LoRA configuration. Declared update and RL rollout/token bounds must equal those selected settings; GPU-seconds and USD remain unverified because native accounting does not meter them.

Every arm's tool manifest is the exact public M7 `m7-tool-protocol` payload, and its action format, harness and optimizer family equal the implementation constants. The `feature_grpo` arm carries selected successful M6 construction receipts. Their `BUILT T0` roots must match the roots reached from the checkpoint's admitted `RELEASED Tn` training tasks through their current lifecycle chain. The `base` arm has no training sources.

After the evaluate claim, the service creates the M7 native session under its durable startup intent. For every `(arm, policy_seed)` group it records an activation intent, calls `NativeSession.activate_checkpoint`, and revalidates the live barrier immediately before each assigned trial. Each `AgentRunner.run` receives the frozen nonnegative `case_seed` and deterministic trial invocation; the service validates and consumes the runner's selected rollout/M4 receipt without grading again.

Before native shutdown it freezes an `m8-evaluation-execution` receipt containing every assigned trial, selected run result and activation. Report-publication recovery consumes that receipt without another rollout. A retained failed shutdown is retried before report completion; lost-process cleanup remains unresolved rather than being silently credited. `close() -> tuple[ArtifactRef, ...]` is cleanup-only: it drains both a published retained handle through the same frozen `:shutdown` key and an unpublished startup through `NativeSessionFactory.close_startup`, without initializing a session. If evaluation and shutdown both fail, the evaluation error remains primary and carries the shutdown exception as `native_cleanup_error`. CLI callers invoke `close()` in `finally` and preserve both failures. Registry completion binds startup, activation, run, shutdown and report accounting with unknown GPU/USD fields retained.

## Human audit

`AuditService(store, registry, human_verifier, selection_manifest, attestations, revision).audit(run_ids)` uses the actual controller `Registry`, M4 receipts, `audits.attestation.SSHHumanVerifier`, and M6 `SourceDisposition` records. It registers every opaque frame, plan, selection, attestation and configuration dependency before creating one durable Registry `audit` job with accounting.

Before enqueueing, it validates the source/patch joins and creates an M6 `historical_audit_configuration` whose subjects are those exact historical verifier/source roots. Current frame, plan, selection, ordinary configuration, attestation envelope, signed payload and signature remain protected. This scope permits the audit to inspect or quarantine historical subjects without granting normal admission or bypassing a notice on current audit instructions.

For each frozen selected run it requires a completed registry job whose operation is `run`, exactly one `RolloutRecord` with `rollout.run_id == job_id`, and exactly one M4 grade receipt bound to the same task, submission, disposition, reward and sole grader seed. Unknown, incomplete, ambiguous or drifted records reject.

`AuditPopulationFrame` contains actual patch runs and true rejected-source construct jobs. `AuditSamplingPlan` stratifies by unit, repository family, actual status and failure category. `derive_selection_manifest` deterministically ranks each frozen stratum from its explicit seed and derives `sample_size / population_size`; the service recomputes the complete selection, validates every population subject against Registry/M4/M6, and refuses a partial `run_ids` list. Missing selected attestations remain explicit unresolved outcomes.

The canonical signed patch payload separately binds patch validity, environment validity and checker assessment to the selected sample, run, task, verifier, submission and reproducible counterexample. An infrastructure outcome cannot establish a checker defect. The separate source payload binds the selected M6 construct job, exact CandidateRecord, M6 disposition/source evidence and counterexample. Source findings never enter patch denominators and quarantine the actual source version rather than a fabricated verifier. Verification uses the audit namespace `feature-rl-human-review-v1`, live pinned external enrollment, revocation list, enrolled Ed25519 key and pinned native binary digest. SSHSIG validity alone is not treated as human origin.

`summarize_audits` reports inverse-probability weighted `invalid_among_accepted` (conditioned on verifier acceptance), `accepted_among_invalid` (conditioned on human-invalid patches), and `rejected_among_valid` (conditioned on human-valid patches). Empty or unresolved denominators return null estimates and unavailable/invalid-environment outcomes have separate accounting. `summarize_source_audits` reports the separately weighted valid-source rate among rejected-source units.

Only an explicit authenticated checker-defect adjudication in a valid environment quarantines the verifier. The service traces descendant rollouts/checkpoints and records the required regrade plus unaffected-restart-or-contamination-disclosure action. Historical inspection does not grant current task or policy admission.

The report freezes exact `QuarantineAction` records before mutation. `retry_publication(pending)` republishes retained report bytes without repeating adjudication, and `recover(claim)` replays idempotent quarantine effects and Registry completion from the selected accounting receipt. An existing conflicting or lifted notice is never silently replaced.

No real human enrollment, signed adjudication, frozen task roster, learned policy checkpoint, GPU run or experiment result exists in this checkpoint.
