# M8 evaluation and audit interface — independent checkpoint

This checkpoint implements immutable study records, validation, offline receipt statistics, external-corpus adaptation accounting, and authenticated historical audits. It does not execute an evaluation trial. `EvaluationService.evaluate(config)` remains intentionally unwired until M7 publishes the agreed real `AgentRunner.run(task, policy, limits, *, case_seed=...)` API.

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

## Human audit

`AuditService(store, registry, human_verifier, selection_manifest, attestations, revision).audit(run_ids)` uses the actual controller `Registry`, M4 receipts, M5 `SSHHumanVerifier`, and M6 `SourceDisposition` records. It registers every opaque frame, plan, selection, attestation and configuration dependency before creating one durable Registry `audit` job with accounting.

For each frozen selected run it requires a completed registry job whose operation is `run`, exactly one `RolloutRecord` with `rollout.run_id == job_id`, and exactly one M4 grade receipt bound to the same task, submission, disposition, reward and sole grader seed. Unknown, incomplete, ambiguous or drifted records reject.

`AuditPopulationFrame` contains actual patch runs and true rejected-source construct jobs. `AuditSamplingPlan` stratifies by unit, repository family, actual status and failure category. `derive_selection_manifest` deterministically ranks each frozen stratum from its explicit seed and derives `sample_size / population_size`; the service recomputes the complete selection, validates every population subject against Registry/M4/M6, and refuses a partial `run_ids` list. Missing selected attestations remain explicit unresolved outcomes.

The canonical signed patch payload separately binds patch validity, environment validity and checker assessment to the selected sample, run, task, verifier, submission and reproducible counterexample. An infrastructure outcome cannot establish a checker defect. The separate source payload binds the selected M6 construct job, exact CandidateRecord, M6 disposition/source evidence and counterexample. Source findings never enter patch denominators and quarantine the actual source version rather than a fabricated verifier. Verification uses the M5 namespace `feature-rl-human-review-v1`, live pinned external enrollment, revocation list, enrolled Ed25519 key and pinned native binary digest. SSHSIG validity alone is not treated as human origin.

`summarize_audits` reports inverse-probability weighted `invalid_among_accepted` (conditioned on verifier acceptance), `accepted_among_invalid` (conditioned on human-invalid patches), and `rejected_among_valid` (conditioned on human-valid patches). Empty or unresolved denominators return null estimates and unavailable/invalid-environment outcomes have separate accounting. `summarize_source_audits` reports the separately weighted valid-source rate among rejected-source units.

Only an explicit authenticated checker-defect adjudication in a valid environment quarantines the verifier. The service traces descendant rollouts/checkpoints and records the required regrade plus unaffected-restart-or-contamination-disclosure action. Historical inspection does not grant current task or policy admission.

No real human enrollment, signed adjudication, frozen task roster, learned policy checkpoint, external corpus payload, GPU run or experiment result exists in this checkpoint.
