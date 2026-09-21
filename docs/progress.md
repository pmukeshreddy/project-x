# Feature-RL progress

## Environment harness follow-up — 2026-09-21

Baseline qualification accepts a working historical runtime whose new-feature probes
fail, provided applicable old compatibility remains intact. Qualification executes
baseline, gold, wrong solutions and reset once, then freezes direct private result
references. Admission and release check immutable task/report identity without
replaying qualification or transition history. Authoring validation retries are
bounded to three attempts with no semantic repair chain. Training is unchanged;
no real environment generation is part of this work.

## Current cleanup — 2026-09-20

The active architecture is feature environments → GRPO/RLVR → held-out evaluation,
with `base` versus `feature_grpo`. SFT and external-RL adaptation have been removed.
Authoring uses local Transformers/PyTorch with explicit immutable model/dependency
manifests. Repository execution requires a pinned runtime profile, explicit platform
and cached prebuilt image. Click-only runtime/discovery and MLX compatibility paths
are removed, including the fallback that installed dependencies for every rollout.

Task admission uses baseline, gold, wrong-implementation and clean-reset execution evidence; signed human task acceptance has been removed.
Independent historical human audits remain separate from task admission. Mixed-file
uncertainty is carried into permitted-source qualification rather than a manual gate.
`construct-feature --github` captures a selected GitHub PR/issue directly; completed
captures are reusable offline and private sources support explicit token selection.
GRPO group size is part of group/checkpoint identity. There is no difficulty warm-up
gate or required count of mixed tasks; zero-advantage groups retain their costs and
positions without an optimizer step.

These are breaking interface changes: old Click policies, recipes lacking runtime
images, MLX requests, signed task admissions and A/D study inputs require reconstruction
under the current contracts. Historical artifacts are not rewritten or silently upgraded.
The cleanup is local and uncommitted. Only syntax, local import and diff checks were
performed; no tests, model calls, image builds or training/evaluation jobs were run.
There is still no real task qualified and released through this updated path, and
no end-to-end training/evaluation result validating it.

## Historical checkpoint before cleanup

The following records describe the cited earlier revisions. Their completed-test
claims and architecture do not validate or prescribe the current implementation.

Read this first after a context reset. Both attachments were fully read: `feature_rl_pipeline.md` is the architecture/execution specification and `codex_multi_agent_implementation_prompt.md` establishes module ownership. Detailed earlier progress is preserved at Git `c7fea17:docs/progress.md`; existing reports and evidence remain unchanged.

## Scope and current milestone

Complete code implementation with CPU/Docker verification. **GPU execution, new task-generation calls, real dataset collection and experimental results are deferred and unverified.** No new model calls, dependency tooling, speculative hardening or old-module review. Use existing owners; one writer per module. Root coordinates interfaces and independently reviews changes.

**Current code scope is complete.** M6–M8 and all required CLI commands are implemented, integrated and reviewed. At exact checkout `72160343356d555e3eaab84246826a4b3363b26c`, the single full CPU suite passed **889 tests in369.43s with no failures or skips**. The single cached Docker check through the final CLI passed in27.07s: baseline reward0, all three cases completed, only F1 failed, C1 preserved, selected Registry result matched and cleanup verified. Tested source hashes and the original Registry stayed unchanged. Final receipt/logs: `docs/evidence/integration/final/`. No verification process remains running.

No implementation work or confirmed defect remains in the authorized code scope. GPU training/native inference, successful real task generation, external human approval and empirical evaluation remain unexecuted. Do not repeat the completed CPU suite or Docker matrix without a new code change or observed failure.

| Module | Retained owner | Code/review status |
| --- | --- | --- |
| M0 | `/root/m0_contracts` | Contracts/CAS/config reviewed; strict run case seed and console entry point60ff9a7,121 focused checks. |
| M1 | `/root/m1_sources` | Intake, historical reconstruction and split planning reviewed; original Click intake retained. |
| M2 | `/root/m2_authoring_recovery` | Validation/recovery6568a11 and control/alternative stages2941264 reviewed;105 focused checks. Failed real attempt retained. Never restore the former M2 writer. |
| M3 | `/root/m3_runtime` | Runtime and sampled remaining-CPU cap08467cd reviewed;40 focused checks and actual Docker cap diagnostic. |
| M4 | `/root/m4_grading` | Core430df6f and checker/control authoring4086ee6 reviewed; actual private-controller grading and bounded worker protocol. |
| M5 | `/root/m5_qualification` | Qualification/acceptance3cc9f11 plus e3d868a fixes reviewed; actual external human gate enforced. |
| M6 | `/root/m6_factory` | Authoring/history117b175 reviewed,35 focused checks. Final grading/CLI4e0b72f reviewed,33 focused checks23.50s. Frozen. |
| M7 | `/root/m7_learning` | Native run/training recovery21339c7 reviewed,19 final focused checks30.54s; earlier affected runner38 checks. Frozen. |
| M8 | `/root/m8_evaluation` | Final matched controls/origins/cleanup7216034 reviewed;11 focused checks54.29s. Frozen. |

Owners remain available only for confirmed integration failures. M7 independently reviewed M6's new run/train/evaluate argument flow, inert construction and primary-error preservation; root reviewed the new grading, authoring/history, recovery and evaluation joins. The final closure in `docs/reviews/service-integration.md` records these reviews; its earlier open paragraphs describe superseded historical checkpoints.

## Implemented connections

The CLI exposes `construct`, `qualify`, `release`, `run`, `grade`, `audit`, `train`, `evaluate`, plus `screen-source`, `author`, `resolve`, `recover` and `retry-publication`. Strict JSON inputs invoke actual same-store services. [Runbook](runbook.md) contains commands and recovery; [native launch](reports/M7-native-launch.md) contains pinned Linux setup and SFT/GRPO/resume requirements. Module interfaces remain in `docs/interfaces-M2.md` through `docs/interfaces-M8.md`.

M6 now retains model resource reservations and bounded attempts per role, then
constructs BUILT plus a receipt. Scenario identities and verifier structure are
controller-owned; no fragment assembly or construction repair history remains.

M5 now runs baseline, gold, three or four wrong implementations once each, and one
same-seed gold reset rerun. Full reward for a wrong implementation rejects the
environment. Exact task/report binding, private gold and verifier data, current
artifact validation and runtime isolation remain required for release.

M7 owns one native parent run and one selected AgentRunner child; the RolloutRecord retains the child's Registry job ID. Actual M3 actions/source capture end in sole M4 grading, with bounded same-submission infrastructure retries. Training authenticates TRN inputs, real signal, token/context/mask/behavior data, tensor/optimizer changes and checkpoint reload. Explicit recovery authenticates the last confirmed checkpoint plus original claim/journal/shutdown, preserves costs and dispatched-data positions, and skips unknown work. An exhausted recovered budget returns BLOCKED before native restart and does not select the old checkpoint as a new success. Terminal close APIs retain pending startup/shutdown recovery.

M8 joins frozen arms to actual selected training requests/settings, the exact M7 public tool/action protocol, and actual external versus Factory construction origins. Released Tn tasks resolve back to BUILT T0; external config/frame/row/mapping inputs are revalidated inertly, with no adaptation dispatch. Trial assignment, case seeds, selected runner receipts, current admission, invalid denominators, family-clustered comparisons and report recovery are implemented. Terminal cleanup drains both unpublished startups and retained handles, preserving primary errors. Audits consume real external attestations and protected historical Registry scope; no human attestation was manufactured.

## Final verification receipt and reproduction

Executed final Docker state: `.feature-rl/research/integration/final-cli-13d0d5f5468b4de4805615adb93f2bcb`. It contains `context.json`, the original TEST `grade-request.json`, and an isolated copy of the original Registry. **The one final Docker CLI command passed; inspect its retained receipt rather than rerunning it.** Use its original CAS, socket, isolated Registry/runtime paths and original builder revision `c7a2edb387409dc3f0555453918419edf15cd275`. Pin Factory/M3/M4 revisions to the final tested checkout; default SandboxPolicy and grade wall600s. No qualification/native settings are needed.

Command: `PYTHONPATH=src .venv/bin/python -m feature_rl --config <derived controller JSON> grade --request <state>/grade-request.json --invocation final-cli-baseline-001`. Observed candidate rejection/reward0 for missing F1, all three cases completed, compatibility C1 retained and cleanup verified. The actual result was retained before assertions. Selected grade job: `233a64dea3c60713f951b7b06881069eee16a23559171b3a4bd692f8176ca581`. Do not replay this command just to recheck idempotence; the focused tests and earlier subprocess evidence cover replay.

The CPU suite uses `.venv-training-cpu/bin/python -m pytest -q --ignore=tests/test_environments_docker.py --ignore=tests/test_grading_docker.py`. Only the two Docker suites are excluded; retained real Docker evidence plus the final command cover that boundary. All pinned-source CPU checks can read the existing local SkyRL checkout. No dependency installation or SkyRL/CUDA imports were needed.

## Evidence to reuse

- [M6 fixture](reports/M6-click-fixture.md), `docs/evidence/M6/fixture-runtime/receipt.json`: TEST prefix helper, actual M3/M4/M5/M6. B0, correct implementation1, deliberate omission0, resetB0; qualification then ran nine grades. Total13 real grades/39 completed cases. Exact reset/cleanup and replay verified. Q remains PROVISIONAL; no human approval, independent alternative or complete original qualification controls/history was fabricated. T0 `259b16c9faf122474ecd548e342987098aa4ba5ddd7c217c6fd33986f109b875`.
- [M7 runner](reports/M7-runner.md): actual Docker actions/grade on the same TEST fixture with a disclosed scripted non-model backend and isolated diagnostic admission substitution, after actual M5 denial. One token-budget-stopped episode graded1; original Registry unchanged. This is integration evidence, not policy competence or training.
- [M3 CPU cap](reports/M3-cpu-budget.md): requested0.05 CPU seconds, observed0.138736 at sampled detection; source/cleanup preserved. Sampling overshoot is explicit.
- Prior stable CPU milestone7c79981:769 passed,1 source-audit skip, then that exact source check passed separately using the retained pinned trainer. `docs/evidence/integration/runner-milestone/`. Earlier fixture milestone517 passed. Reuse; do not rerun these old milestones.
- M6 authoring117b175:35 focused checks145.48s with tested files unchanged; exact original Click failed-journal import in `docs/evidence/M6/authoring/`, with zero new provider execution.

Fixture success is integration evidence, not successful generation of a real feature. No real human-approved/released task, native GPU update, held-out evaluation result or completed empirical audit exists.

## Retained real attempt and execution limits

Real Click PR3228/issue3107: B-only discovery succeeded; initial contract plus two repairs failed. Final completed output had duplicate JSON keys/IDs and ungrounded evidence. No valid contract/scenario/checker or real constructed feature resulted. Current M1 source also remains provisional for mixed-purpose scope review. Known repairs3/4 include environment1 and contract2/2 exhausted. No salvage, handwritten production replacement or new task-generation calls.

Final original journal `af7cee9c0af50eeddcf1a21f23b0e875964796648d20a34659e68a460806ace6`, original store `.feature-rl/research/M2/authoring-production/store`; [M2 report](reports/M2-authoring.md). The isolated M6 import preserves all three rejected calls:20,808 input tokens,6,636 output tokens,324.656 wall seconds,122.702 CPU seconds. GPU/USD/overhead remain unknown. Earlier authoring metadata exposure is disclosed in the original report; do not claim full author blindness.

This Mac has CPython3.13.7 and CPU Torch2.11.0 in the retained CPU venv. GPU native execution requires the frozen Linux x86_64/Python3.12/CUDA stack, actual protected model/reference/tokenizer directories, enrolled external trust and released tasks. SkyRL is pinned to `f5bc3b78dfddfb352870d5d7430cd226e5785838`; Harbor to `3de07a0e01f3368921766437fc7afece3ddec23d`. Existing source and dependency decisions are retained; no provisioning/download/reinstallation. Unknown currency remains null; finite native currency caps reject without a meter.

The currently implemented Docker profile is pinned Click8.3.3 with no services. Image `feature-rl-m3-less@sha256:b28d3b1eeaa6251e82354fa61dc28a29afead9e2d7179436e77ca8ba612032fa`, six original wheels in `.feature-rl/research/M3/dependencies`. Reuse caches, keep fresh candidate builds, and retain nonroot/read-only/no-network/no-host-secret isolation. Broader runtime profiles and GPU worker-socket forwarding are unverified.

Workspace: `/Users/mukeshreddypochamreddy/Desktop/project x`, branch `implementation/feature-rl`. All agents share the repo/index: explicit owned paths and `git commit --only`; no broad staging. Preserve unrelated `.DS_Store`, `.superpowers/` and old untracked diagnostic evidence. Approval policy never: omit sandbox_permissions. Existing skills/spec design are already applied; no repeated approval/design cycle. After a reset, read the completed receipt and preserve this verified state; no automatic rerun is needed.
