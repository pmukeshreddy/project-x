# Coordinator review of service integration

Independent reviewer: `/root`; implementation remains with the retained module owners. This is the consolidated review for new service checkpoints. Earlier unchanged module reviews and raw evidence remain authoritative; no historical Docker matrix was repeated.

## M3 remaining CPU cap — PASS for the implemented boundary

Reviewed `08467cd67fe661d01180fd3be60bc7c0e4a47fcf`: request validation, session ownership/recovery, cumulative monitor, staging/setup/export classification, last-confirmed source and receipt joins. The override cannot increase the admitted cap. Storage, monitor and cleanup failures retain infrastructure classification. Recovery retains a recorded older cap so a stricter current policy cannot prevent cleanup.

Reuse the owner's 40 focused checks and one actual Docker diagnostic in `docs/evidence/M3/cpu-budget/verification.json`. Requested0.05 CPU seconds, observed0.138736 at detection; source preserved and cleanup verified. This is a sampled watchdog, not an exact kernel CPU-time quota. M7 must still consume the field; no native training claim follows.

## M6 source, construct and historical-audit scope — PASS for these slices

Reviewed source admission `f74182e`, construction `94b7fce`, Registry scope `a315c8e`. Source admission retains exact M0 screening/license/partition and original costs once; unknown attempts cannot rescreen implicitly. Construction invokes the actual selected TaskBuilder, binds the child job/result, replays frozen publication, and retains incomplete repair history instead of asserting zero repairs. The complete model-authoring/global-history workflow remains subsequent M6 work.

The historical-audit wrapper is restricted to operation=audit and explicitly selected historical dependencies. Current configuration, protected refs, scope records and new outputs remain subject to quarantine. Ordinary admission/construct/run/train/evaluate behavior is unchanged. Reserved records have canonical intrinsic dependencies and cannot add undeclared scope. Reused focused evidence: source11 checks; construction39 including affected source/builder; Registry37 including15 new audit checks, actual SQLite-commit/JSONL-projection recovery and unchanged old JobSpec bytes. M8 must explicitly protect current instructions, selection, configuration and detached human-envelope/payload/signature refs when consuming this interface.

## M7 runner/backend — PASS for checkpoint; budget integration pending

Reviewed `d3ee48b`: actual M3 actions and source capture, concrete M6 admission, sole terminal M4 grading, bounded same-submission infrastructure retries, exact Registry run identity, selected controller receipts for null outcomes, frozen publication recovery and native token endpoint. The policy backend binds the bounded local tokenizer bundle and template. Behavior logprobs are accepted only under unit-temperature full-support sampling; greedy/transformed sampling remains nontraining. Unknown/aborted native completions cannot become ordinary candidate failures or replacement samples.

Reused68 focused CPU checks and the actual Docker diagnostic in `docs/reports/M7-runner.md`. The latter deliberately substitutes only M5 verification in an isolated diagnostic Registry and uses a scripted one-token backend. No human approval, native inference, real task-generation success or training result is asserted. Actual unmodified admission denial is checked separately. M3 remaining-CPU consumption and full native training/checkpoint/launch service remain implementation work. Existing wall/setup/cleanup allowances must remain explicit.

## M8 audit/statistics and external adaptation — changes required

Reviewed audit/statistics `4cf94e0` and adapter `62eec0c`. The six original findings have corresponding code changes: conditional error-rate denominators, distinct source units, frozen authenticated frame/sample, independent environment/checker judgments, task-level best-of-k and explicit training-replicate limitations. Reuse21 focused audit/statistics checks and2 adapter checks; these are diagnostic evidence, not empirical audits.

Confirmed remaining affected-interface defects:

1. Audit applies source/verifier quarantine before report completion, causing ordinary Registry dependency enforcement to block its own report; already-quarantined historical subjects also cannot start. Reproduced with actual Registry enqueue/claim/quarantine/register/reconcile/complete and a PROVISIONAL test-only report. Consume the new scoped M6 configuration; preserve protected current refs and confirmed effect/report recovery. Current AuditService advertises controller recovery for unfinished claims without an implemented recovery API.
2. `_source_subject` requires the original screening disposition to be rejected. Actual M6 also rejects an ineligible license after successful screening. Reproduced via actual Factory: screening=success, source=candidate_rejection, followed by AuditRejected. Validate the exact M6 decision/selected source receipt instead.
3. External `frame.exclusions` is registered but never enforced, while its funnel claims exclusion from locked evaluation. Enforce the actual frozen family/lineage exclusions. Origin request locator and authoring allowlist identity must join actual source artifacts, and a self-declared patch digest plus changed-path names does not establish the external patch's relation to SourcePair history. Complete those joins when delegating to actual Factory construction; unresolved input relations must remain explicit.

Both root reproductions used temporary resolved paths and clearly labeled diagnostic artifacts, with no human attestations, model calls or Docker reruns. Owners received the concrete findings. Actual EvaluationService and complete construction delegation remain assigned to M8.
