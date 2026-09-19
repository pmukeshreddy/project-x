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

`summarize_trials(roster, preregistration, trials, bootstrap_seed=..., bootstrap_resamples=...)` accepts a complete tuple of actual `TrialResult` receipts. It rejects missing, duplicate, drifted-task, drifted-family, drifted-policy-seed, or drifted-case-seed rows. It returns:

- assigned, valid, resolved, failed and invalid counts by arm;
- pass@1 rates using every assigned trial, counting invalid/unresolved assignments as unsuccessful;
- separate valid-only rates, with null estimates for empty denominators;
- paired right-minus-left task-weighted and repository-family-weighted differences; and
- deterministic repository-family clustered percentile intervals, with explicit limitations when fewer than two families or training seeds exist.

This function aggregates supplied receipts only. It neither executes policies nor establishes current task/policy admission.

## Human audit

`AuditService(store, registry, human_verifier, selection_manifest, attestations, revision).audit(run_ids)` uses the actual controller `Registry`, M4 receipts and M5 `SSHHumanVerifier`.

For each frozen selected run it requires a completed registry job whose operation is `run`, exactly one `RolloutRecord` with `rollout.run_id == job_id`, and exactly one M4 grade receipt bound to the same task, submission, disposition, reward and sole grader seed. Unknown, incomplete, ambiguous or drifted records reject.

The canonical signed `AuditAdjudication` binds the full selected sample, run, task, verifier, submission, reproducible counterexample, verdict, adjudication text, reviewer and review time. Verification uses the M5 namespace `feature-rl-human-review-v1`, live pinned external enrollment, revocation list, enrolled Ed25519 key and pinned native binary digest. SSHSIG validity alone is not treated as human origin.

`summarize_audits` reports inverse-probability weighted `invalid_among_accepted`, `accepted_among_invalid`, and `rejected_among_valid` estimates. Targeted samples are excluded from population estimates. Empty or unresolved denominators return null estimates. The frozen manifest requires accepted, rejected and rejected-source strata.

An authenticated checker mismatch quarantines the verifier in the real registry, traces descendant rollouts/checkpoints, and records the required regrade plus unaffected-restart-or-contamination-disclosure action. Historical inspection does not grant current task or policy admission.

No real human enrollment, signed adjudication, frozen task roster, learned policy checkpoint, external corpus payload, GPU run or experiment result exists in this checkpoint.
