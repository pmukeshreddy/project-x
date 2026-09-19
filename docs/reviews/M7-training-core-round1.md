# M7 training-core independent review — round 1

Reviewed commit: `f9f1c2f` (2026-09-19). Scope: its complete `src/feature_rl/training/**`, `tests/test_training*.py`, report and interface changes, against the M7 brief, binding specification §§10–14 and current code-completion scope. The training files still matched that commit when checked. Runner, launcher/service, native checkpoint publication and experiment orchestration are explicitly subsequent work.

**Specification compliance: FAIL for this slice.** Three implemented boundary behaviors contradict valid-outcome accounting and validity-aware batch consumption.

**Implementation quality: FAIL; changes required.** The local tensor design is substantive, but the tests miss the actual M4 evidence-scope distinction and SkyRL's fixed prompt-count contract. These defects are independent of unavailable GPUs.

## Prioritized findings

1. **P1 — Authentic source-rejection zeros cannot enter a training group.** `src/feature_rl/training/data.py:82–85` accepts only `scope == 'real_integration'`. Actual M4 deliberately returns a measured `candidate_rejection`, reward `0`, with `source_inspection` evidence when its source-only submission validator rejects a malformed/forbidden submission before executing a worker (`grading/service.py:92–94,192`). This is a valid agent outcome under §11, not an infrastructure failure or fabricated measurement. A single such response makes `prepare_group` reject the entire otherwise valid batch. Authenticate the actual M4 rejection and retain its zero without falsely relabeling it as worker execution.

   Reproduction: with the existing `m4_fixtures.diagnostic` task changed to TRAIN and an explicit diagnostic admission callback, call the actual `GradingService.grade` on `b'{"version":"forged","reward":1}'`. An `EnvironmentRuntime` instance with only `store`/`policy` set suffices because no execution path is reached. Put the returned evidence and zero into four otherwise aligned diagnostic RolloutRecords and call `prepare_group`. Output:

   ```text
   Actual M4 preflight receipt: candidate_rejection reward= 0 scope= source_inspection
   M4 reason: source submission rejected: invalid source submission manifest
   TrainingDataGate rejected valid zero: Unique real M4 grading receipt required
   ```

2. **P1 — Null candidate outcomes are silently treated as invalid peers.** `src/feature_rl/training/data.py:79–102` validates a grading receipt only when reward is nonnull, then derives eligibility entirely from nullness. M0 permits a not-yet-measured `candidate_failure` / `candidate_rejection` record with `reward=None`, `training_eligible=False`, and no grading evidence. The gate accepts this as an excluded peer and optimizes the others. There is no required invalid/infrastructure disposition or established reason for exclusion. A lost or ungraded candidate failure can therefore disappear from the group baseline instead of being finalized or rejected. Require a validated invalid outcome before exclusion; an incomplete valid candidate trial must not become an infrastructure-invalid peer.

   Reproduction: start with the existing training-data test's shape-only measured-control convention. Change one of four records to `reward=None`, `training_eligible=False`, `steps=[]`, `grading_evidence=[]` and an empty corresponding context list; retain `candidate_failure` / `candidate_rejection`. Output:

   ```text
   Synthetic measured control effective_size: 4
   Ungraded candidate failure accepted: candidate_failure candidate_rejection reward= None
   Accepted effective_size= 3 optimization_turns= 3 rewards= (0, None, 0, 0)
   ```

   This second check uses synthetic scope labels solely to reach the branch, as the existing test does. It is not real trajectory or feature evidence.

3. **P1 — Removing ineligible groups breaks the pinned trainer's batch shape.** `src/feature_rl/training/skyrl_bridge.py:85–90` removes groups with fewer than two valid outcomes, but `:155` calls native conversion with the unchanged configured batch size. Pinned `trainer.py:932–936` passes that size to `compute_prompt_mini_batch_boundaries`, whose `dataset/preprocess.py:279–280` requires exactly that many distinct prompt UIDs and divisibility by the configured minibatch size. With two assigned groups and one singleton, valid rows remain, so the empty-batch skip does not apply; native conversion asserts before any optimizer call. Build native boundaries for the actual eligible groups while preserving all assigned outcomes/costs and excluding singleton optimization. Do not require replacement policy samples to fill the original shape.

   Reproduction: pass one two-valid-outcome group and one singleton to actual `group_rows`, then invoke the exact pinned pure-Python boundary function extracted by AST (no SkyRL import) with `mini_batch_size=1`, `train_batch_size=2`, `is_stepwise=True`, `n_samples_per_prompt=4`. Output:

   ```text
   groups assigned=2; eligible bridge rows= 2 ; uids= ['valid', 'valid']
   Pinned compute_prompt_mini_batch_boundaries: AssertionError(num_prompts == train_batch_size)
   Control with expected eligible count=1: [(0, 2)]
   ```

## Evidence and remaining gates

The two focused reproduction commands used `PYTHONPATH=src[:tests] .venv-training-cpu/bin/python` and exited 0 after reporting the expected defects above. The data check exercised M4's actual pre-execution source rejection and M7 preparation; admissions, tokens and tasks were diagnostics. A first attempt hit the existing artifact store's rejection of macOS `/var` symlink ancestry; resolving the temporary path allowed the check. No product changes, dependency work, model calls/downloads, Docker/GPU runs, child agents or full-suite reruns occurred.

Reviewed recorded evidence: `docs/evidence/M7/training-core/focused-tests.txt` reports **22 passed**. Its actual CPU tests cover sampled-probability clipping, assistant masking, SFT/GRPO parameter changes, frozen reference, zero-gradient optimizer skip, and checkpoint optimizer/RNG/next-update reproduction. I inspected those tests and the implementation; I did not repeat that suite. The balanced sampler, valid-only advantage arithmetic, per-turn context validation and fresh policy-barrier acknowledgments have no additional finding in this review.

Native SkyRL/Ray/FSDP/vLLM imports and execution, distributed loss/KL gradients and synchronization, real worker probes, genuine multi-turn feature traces, useful training signal, and native save/reload/resume remain unverified. The upcoming service must supply authoritative M5/M6 admission/revocation resolution, supervised provenance/rendering, budgets/costs and end-to-end enforcement. Their declared absence is not a defect in this narrowed slice and does not justify a feature-learning claim. Fix and narrowly re-review findings 1–3 before integrating this slice.
