# Coordinator review of service integration

Independent reviewer: `/root`; implementation remains with the retained module owners. This is the consolidated review for new service checkpoints. Earlier unchanged module reviews and raw evidence remain authoritative; no historical Docker matrix was repeated.

## M3 remaining CPU cap — PASS for the implemented boundary

Reviewed `08467cd67fe661d01180fd3be60bc7c0e4a47fcf`: request validation, session ownership/recovery, cumulative monitor, staging/setup/export classification, last-confirmed source and receipt joins. The override cannot increase the admitted cap. Storage, monitor and cleanup failures retain infrastructure classification. Recovery retains a recorded older cap so a stricter current policy cannot prevent cleanup.

Reuse the owner's 40 focused checks and one actual Docker diagnostic in `docs/evidence/M3/cpu-budget/verification.json`. Requested0.05 CPU seconds, observed0.138736 at detection; source preserved and cleanup verified. This is a sampled watchdog, not an exact kernel CPU-time quota. M7 must still consume the field; no native training claim follows.

## M6 source, construct and historical-audit scope — PASS for these slices

Reviewed source admission `f74182e`, construction `94b7fce`, Registry scope `a315c8e`. Source admission retains exact M0 screening/license/partition and original costs once; unknown attempts cannot rescreen implicitly. Construction invokes the actual selected TaskBuilder, binds the child job/result, replays frozen publication, and retains incomplete repair history instead of asserting zero repairs. The complete model-authoring/global-history workflow remains subsequent M6 work.

The historical-audit wrapper is restricted to operation=audit and explicitly selected historical dependencies. Current configuration, protected refs, scope records and new outputs remain subject to quarantine. Ordinary admission/construct/run/train/evaluate behavior is unchanged. Reserved records have canonical intrinsic dependencies and cannot add undeclared scope. Reused focused evidence: source11 checks; construction39 including affected source/builder; Registry37 including15 new audit checks, actual SQLite-commit/JSONL-projection recovery and unchanged old JobSpec bytes. M8 must explicitly protect current instructions, selection, configuration and detached human-envelope/payload/signature refs when consuming this interface.

## M7 runner/backend — PASS for checkpoint and CPU-cap consumption

Reviewed `d3ee48b`: actual M3 actions and source capture, concrete M6 admission, sole terminal M4 grading, bounded same-submission infrastructure retries, exact Registry run identity, selected controller receipts for null outcomes, frozen publication recovery and native token endpoint. The policy backend binds the bounded local tokenizer bundle and template. Behavior logprobs are accepted only under unit-temperature full-support sampling; greedy/transformed sampling remains nontraining. Unknown/aborted native completions cannot become ordinary candidate failures or replacement samples.

Reused68 focused CPU checks and the actual Docker diagnostic in `docs/reports/M7-runner.md`. The latter deliberately substitutes only M5 verification in an isolated diagnostic Registry and uses a scripted one-token backend. No human approval, native inference, real task-generation success or training result is asserted. Actual unmodified admission denial is checked separately. Follow-up `7c79981` now passes the minimum of remaining measured episode allowance and the admitted M3 cap into every action; the focused two-action regression verifies60 then0.5 seconds. Root reviewed that small delta;20 owner runner checks passed. Full native training/checkpoint/launch service remains implementation work. Existing wall/setup/cleanup allowances stay explicit.

Combined stable CPU milestone at exact detached `7c79981dc58aef78b3632c2ba6a8cdd36817945d`:769 passed,1 skipped in132.92s. The skipped pinned-source symbol check then passed separately in0.14s using byte-identical retained trainer.py copied into that checkout. Tracked src/tests/pyproject hashes match before/after. Evidence: `docs/evidence/integration/runner-milestone/{receipt.json,pytest.log,source-audit.log}`. Explicitly excluded the two Docker suites, whose actual fixture/runner/cap diagnostics are retained. Known M8 interface findings and later service code are outside this passing milestone.

## M6 qualification and initial CLI — PASS for these slices

Reviewed qualification join `c33c622`: actual Factory lineage supplies M5's exact repair policy, caller substitutions reject, and acceptance/release use the existing frozen human request and both legal lifecycle transitions. Missing history remains provisional. Reused five focused checks.

Reviewed CLI `fc08386`: strict bounded JSON, real service composition, actual Docker boundary qualification for runtime commands, external human verifier configuration, typed status/nonzero failure and retained publication recovery. No executable configuration or fabricated qualification flag. Reused ten focused checks and ran actual subprocesses over a labeled CPU fixture: source success, missing BuildInputs=`blocked_dependency`, and identical replay with no Registry events. Exact commands/results and the corrected diagnostic enum assertion are in `docs/evidence/integration/cli-commands.json`. This initial command slice does not claim that the remaining run/train/evaluate/audit wiring is finished.

## M7 SFT/native service additions — partial review

Reviewed `ebd151e` and `22ea078`: source demonstrations bind actual successful M4 source, exact public harness contexts and tokenizer prefixes; trajectory demonstrations authenticate selected runner records. A root diagnostic reproduced acceptance of a private source-SFT system prompt; `22ea078` now rejects it before rendering. Reused eight SFT/file checks and fifteen data/core checks. The same checkpoint correctly retains authenticated zero-step token/time-budget episodes in group rewards with zero optimization rows. Nontraining arbitrary records remain rejected.

Native session `7e7bb1f` uses the actual pinned SkyRL trainer/dispatch/checkpoint hooks, protected local native directories, actual broadcast plus bounded worker probes and live activation checks. Root inspected the affected pinned constructor, update, worker and checkpoint symbols. GPU execution is unverified.

Native factory/witnesses `e41c98f` and fixes `9c9c328` now retain startup and successful shutdown across publication faults, authenticate cleanup independently of current quarantine, and put outputs under the selected job's namespace. Native updates witness actual trainable and optimizer contents; reload requires exact witnesses and inference consistency. Root ran the publication-fault and BF16 AdamW witness checks:2 passed in1.29s. A root diagnostic then reproduced quarantine preventing shutdown; after the fix its single regression passed in0.65s. Reuse the owner's broader61 focused training checks in14.41s. These changed factory boundaries are PASS.

TrainingService `9c9c328` authenticates released TRN inputs before native startup, freezes actual calls/costs, persists sampler/supervised positions and policy state, gates updates on real signal, and publishes selected checkpoints only after changed tensors/nonzero gradients and reload checks. Ordinary same-claim recovery is CPU-tested. One confirmed gap remains: explicit `resume=...` from an interrupted job is incorrectly routed through the completed-success evaluation validator. M7 is adding a recovery-only path tied to the original confirmed update/claim while preserving unknown subsequent work. The selected-success evaluation helper remains strict. Ordinary native-run orchestration is the other remaining M7 service deliverable.

## M8 audit/statistics and external adaptation — PASS for changed slices

Reviewed audit/statistics `4cf94e0` and adapter `62eec0c`. The six original findings have corresponding code changes: conditional error-rate denominators, distinct source units, frozen authenticated frame/sample, independent environment/checker judgments, task-level best-of-k and explicit training-replicate limitations. Reuse21 focused audit/statistics checks and2 adapter checks; these are diagnostic evidence, not empirical audits.

Confirmed affected-interface defects and their resolutions:

1. Audit applies source/verifier quarantine before report completion, causing ordinary Registry dependency enforcement to block its own report; already-quarantined historical subjects also cannot start. Reproduced with actual Registry enqueue/claim/quarantine/register/reconcile/complete and a PROVISIONAL test-only report. Consume the new scoped M6 configuration; preserve protected current refs and confirmed effect/report recovery. Current AuditService advertises controller recovery for unfinished claims without an implemented recovery API.
2. `_source_subject` requires the original screening disposition to be rejected. Actual M6 also rejects an ineligible license after successful screening. Reproduced via actual Factory: screening=success, source=candidate_rejection, followed by AuditRejected. Validate the exact M6 decision/selected source receipt instead.
3. External `frame.exclusions` is registered but never enforced, while its funnel claims exclusion from locked evaluation. Enforce the actual frozen family/lineage exclusions. Origin request locator and authoring allowlist identity must join actual source artifacts, and a self-declared patch digest plus changed-path names does not establish the external patch's relation to SourcePair history. Complete those joins when delegating to actual Factory construction; unresolved input relations must remain explicit.

All three findings now have reviewed fixes. Audit `5a71dbb`, `389e039` and `35e8c13` consume the protected historical scope, preserve license rejection, freeze effects before application, recover exact publication, distinguish findings in notice IDs, authenticate recovery against the current service configuration and retain failure-report dependencies. Reports without a human adjudication use source-inspection scope. Reused seventeen focused checks; no real human approval was created.

External adaptation `3217cb0`, `591f330` and `4fcd89a` validate the actual M1 source-inspection/request/history joins and frozen locked-test exclusions, invoke Factory construction and include source, parent and selected TaskBuilder child costs once. Root caught an intermediate Git-tree-ID/archive-hash mismatch; the fix uses distinct identities and a real local Git→M1→adapter diagnostic. Reused eight focused checks. These prove local adapter behavior, not successful acquisition or qualification of a real external corpus task.

Root reproductions used temporary resolved paths and labeled diagnostics with no human attestations, model calls or Docker reruns. Actual EvaluationService is the remaining M8 implementation/review scope; unchanged statistics and upstream modules remain closed.

## M8 evaluation service — remaining protocol joins

Reviewed the new `6b4dc6b` service: selected claimed native startup, actual released task partition/family/lineage, selected M7 checkpoint request/config/weights, per-arm activation and live barrier, explicit case seeds, sole selected runner records, frozen execution before shutdown, cleanup recovery and all-assigned reporting. Four owner CPU checks passed in28.19s. Diagnostic native boundaries are substituted explicitly; no real study ran.

Remaining confirmed interface finding: matching declarative optimizer/tools/budget/method labels do not yet bind C/D to matching actual training settings or actual external-versus-factory dataset origins. M8 is adding those joins against selected M7 requests and existing Factory/adapter evidence. This review does not reopen unchanged statistics, audits or adaptation.

## M0 command transport — PASS

Reviewed `60ff9a7`: optional strict nonnegative `RunRequest.case_seed` defaults to None, preserving ordinary runner behavior, and `feature-rl` points to the real `feature_rl.cli:main`. Reuse121 focused contract checks, actual module help and metadata resolution. No dependency installation was needed.

## M6 authoring and repair history — PASS

Reviewed `117b175`: one selected Registry attempt per actual M2/M4 call, frozen candidate/batch reservations, shared stage/candidate repair limits, semantic control slots, exact provider archive/cost closure and publication-only recovery. The confirmed retained-receipt cost substitution and previous-journal findings are fixed and covered by focused mutation checks. Null USD remains explicitly unknown; finite monetary caps reject without a meter.

Construction V2 freezes history before TaskBuilder execution and preserves original V1 readback. The complete branch joins the actual terminal contract, scenario, M4 control record and final checker; historical imports and external artifacts retain incomplete history. Reuse35 focused CPU checks in145.48s with unchanged tested source hashes, including the four-stage TEST sequence and affected construction/qualification joins. This does not establish human approval or successful real generation.

The retained Click import selects the three original failed journals and their measured costs without provider execution or changes to the original stores. Source remains provisional, contract rejected and contract repairs exhausted. Detailed receipt and scope are in `docs/evidence/M6/authoring/` and `docs/reports/M6-authoring.md`. Remaining M6 review is grade/CLI composition.
