# M4 core independent review — round 1

Reviewed implementation `02aee5494ba93b195e0b38f9fbc367f06585df5a` against parent `fa6a1b884365a8f89d37beaf6bc180b945d088fa`. Scope is the commit's M4 grading, submission and closed verifier language, tests and evidence. Generated checker proposal/finalization against M2 is the separately scheduled next slice. This review does not require a successful model-authored feature or GPU experiment.

**Specification verdict: changes required.** The mechanical core implements the intended source-only/controller separation, deterministic complete manifests and typed receipts, but admits an unenforced task resource envelope and can turn invalid trusted baseline material into a measured candidate failure.

**Implementation quality verdict: changes required.** The two narrow diagnostics below reproduce the defects. No P0 finding. Product code, index, branch and commits were not edited.

## Findings

### P1 — Reject task resource limits that the selected runtime cannot enforce

**Location:** `src/feature_rl/verifiers/loader.py:83` and `:110`; execution consumes that result at `src/feature_rl/grading/service.py:97` and `:117`.

The loader compares the verifier output cap and case wall timeout with the contract, but never joins the contract's CPU, memory, PID or disk limits to the environment recipe. M3 runs with its fixed recipe/policy values and receives no per-task override from grading. Its actual `recipe()` validates recipe-to-policy equality (`src/feature_rl/environments/runtime.py:121`), so that upstream validation cannot enforce a smaller contract envelope.

The review diagnostic preserves all M0 task/contract/plan/verifier joins, changes only the declared contract resource values, and calls the real `load_verifier`. It accepts **64 MiB memory, 1 CPU second, 8 PIDs and 8 MiB disk** alongside a recipe allowing **512 MiB, 60 CPU seconds, 64 PIDs and 128 MiB**. These recipe values match the reviewed runtime defaults. A candidate can therefore exceed the task's declared resource allowance without M4 detecting that violation; only the larger recipe limit governs execution. The diagnostic proves the missing preflight gate, not an executed over-limit candidate pass.

The contract requires declared resource limits, and the initial fixed runtime must reject unsupported task policies rather than silently use a larger allowance. Have M4 reject an incompatible resource envelope before opening a workspace, or route the declared limits through a supported enforced runtime interface. Keep solver-only token/tool budgets distinct from worker resource checks.

**Reproduction:** `test_task_resource_limits_below_fixed_runtime_must_reject` in [the review diagnostics](../evidence/M4/review-core-round1/test_core_concerns.py). Recorded failure: `Unsupported resource envelope was admitted by M4`.

### P2 — Validate trusted B outside the candidate-rejection catch

**Location:** `src/feature_rl/submission/service.py:44`, caught at `src/feature_rl/grading/service.py:90`–`:92`.

`resolve()` reads and parses the task's trusted baseline inside the same call whose `SourceRejected` and `ArtifactSizeLimitError` exceptions grading labels as candidate rejection. Consequently a malformed or oversized trusted B is attributed to the candidate, even when the submitted delta is valid and empty. Missing/digest-corrupt storage takes the separate infrastructure path, but a valid CAS object containing invalid baseline archive bytes does not.

The review diagnostic supplies a correctly joined task/recipe with a digest-valid `source-archive` containing `not an archive`, plus a well-formed no-op submission against that exact B. The actual grading preflight returns **`candidate_rejection`, reward `0`, `source submission rejected: malformed source tar`**, with both cases `not_run` and no build evidence. No candidate ran. This violates the required distinction between candidate-caused failures and invalid task/environment measurements and can contaminate failure denominators for every submission to that task.

Validate the trusted baseline separately and preserve a null reward for invalid task/environment material. Restrict measured source rejection to candidate-controlled manifest/delta/deletion violations. This finding does not ask M4 to authenticate lifecycle admission or read H.

**Reproduction:** `test_malformed_trusted_baseline_is_not_a_candidate_zero` in [the review diagnostics](../evidence/M4/review-core-round1/test_core_concerns.py). Recorded failure: `An invalid trusted baseline was attributed to this candidate`.

## Evidence reviewed and execution limits

Read the complete affected M4 source/tests and the relevant actual M0 contract/storage and M3 archive/runtime interfaces. Applied `feature_rl_pipeline.md` §§6, 8–10 and 17, the implementation plan's global rules, M4 brief, review policy, current code-completion decision, M4 interface/report and consolidated evidence. The final source hashes in `docs/evidence/M4/core/summary.json` match the reviewed commit; all six recorded log hashes and byte counts match their files.

Existing owner evidence is retained as recorded evidence: 45 focused passes; the real Click-B diagnostic route's 12 passes/14 grade attempts in 192.71 seconds; eight subsequent archive preflight rejections; and the confirmed build-accounting repair's narrow native pass in 6.72 seconds. The two earlier incorrect build-test assumptions and the confirmed pre-fix failure remain visible. Total recorded diagnostic grade calls are 26. These fixtures exercise ordinary echo preservation and hostile mechanics; they are not model-authored missing-feature tests, B/H qualification or admission evidence.

This reviewer ran only:

```sh
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:tests .venv/bin/python -m pytest -q -s -p no:cacheprovider docs/evidence/M4/review-core-round1/test_core_concerns.py --basetemp=docs/evidence/M4/review-core-round1/pytest-tmp --tb=short
```

Result: **2 failed in 0.13 seconds**, process exit 1, each failing at the intended assertion above. [Raw diagnostic output](../evidence/M4/review-core-round1/diagnostics.log). The resource check exercises the loader only; the baseline check uses an uninitialized M3 object solely for the pre-worker grading path, with no worker methods replaced by fabricated results. Neither is native sandbox evidence. No unchanged suite, Docker/native run, model/tokenizer call, network/download, H/history/private-authoring read or child agent was used.

After owner fixes, rerun these focused reproductions and the directly affected M4 checks. Coordinator integration still must verify the separately due M2 checker finalizer and M5/M6/M7 admission/receipt consumption. Genuine feature qualification, human task acceptance and GPU/training/evaluation results remain execution milestones, not failures of this checkpoint merely because they are unexecuted.
