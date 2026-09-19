# Feature RL Factory Implementation Plan

> **For agentic workers:** Use superpowers:subagent-driven-development. The user explicitly authorized continuous execution with real module owners and independent review. Read your scoped brief and binding specification sections; do not spawn agents.

**Goal:** Convert completed real feature requests into qualified coding environments, collect genuine solver submissions, connect real GRPO updates, and independently evaluate transfer.

**Architecture:** Python library with strict versioned artifacts, content-addressed local storage, append-only events and a SQLite index. Candidate execution is confined to an established isolated worker; trusted controllers hold comparisons and private evidence. Start with Click PR 3228 before broad collection.

**Tech stack:** Python 3.11+; strict Pydantic models; standard-library filesystem/SQLite/HTTP orchestration where sufficient; Docker worker boundary; configured generation provider; SkyRL/Harbor compatibility remains an executable gate. Pin actual verified versions in M0-owned configuration.

**Spec:** `feature_rl_pipeline.md` in full, with execution authority in `codex_multi_agent_implementation_prompt.md`.

## Global constraints

- No production stubs, placeholder success paths, hard-coded outcomes, fabricated tasks or metrics, canned learner answers, or false qualification records.
- No silent fallbacks to another model, backend, checker, reward definition, task scope or weaker execution mode.
- Candidate code must never execute inside the trusted controller.
- A model cannot sign a required human-review record.
- Use at most two repair attempts for any failed construction stage, with at most four repair attempts total for one candidate in the pilot.
- Require at least one targeted semantic negative control per mandatory requirement in the pilot.
- Every accepted pilot task also requires an independently authored alternative positive implementation.
- For each pilot task, repeat a fresh reference run and a reset-after-interruption run at least three times as a smoke gate.
- Private artifacts are not ordinary fields serialized into the solver prompt; references are resolved only by authorized pipeline stages.
- Use the same grading cases within each GRPO group.
- Apply policy loss only to appropriate sampled assistant tokens.
- Never provision paid infrastructure, consume an unspecified paid budget, or change permissions just to force completion.
- Preserve reports, raw command evidence, revisions and findings across context resets. Never delete the evidence workspace.

## Review focus

1. Empty evidence or incomplete probe receipts cannot become success (M0/M4/M5).
2. Hostile paths, links, serialized observations and dependencies cannot cross the grading boundary (M3/M4).
3. Retries after partial publication remain atomic and idempotent, preserving costs and prior failures (M0/M6).
4. Related requests and changed oracle versions invalidate downstream claims (M1/M6/M8).
5. Missing token probabilities, stale policies and incomplete trials cannot yield a learning/comparison claim (M7/M8).

## Ownership and execution

Coordinator `/root` owns this plan, `docs/progress.md`, dispatch briefs, interface decisions, evidence inventory, scheduling and integration verification. Product defects return to their module owner. One product implementer at a time; independent read-only research/review may run concurrently. The empty workspace has no existing branch/code to protect; work in place on `implementation/feature-rl`. Do not alter supplied documents.

| Module | Owner identity when assigned | Exclusive writable product paths | Dependencies |
| --- | --- | --- | --- |
| M0 | `m0_contracts` | `src/feature_rl/contracts`, `src/feature_rl/artifacts`, package root/configuration, `tests/test_contracts*`, `tests/test_artifacts*` | none |
| M1 | `m1_sources` | `src/feature_rl/intake`, `history`, `splits`, corresponding tests | M0 |
| M2 | `m2_authoring_recovery` (explicit capacity-failure transfer; prior `m2_authoring`) | `src/feature_rl/generation`, `requirements`, `scenarios`, corresponding tests | M0/M1, M3 for execution discovery |
| M3 | `m3_runtime` | `src/feature_rl/environments`, corresponding tests | M0/M1/M2 provider slice |
| M4 | `m4_grading` | `src/feature_rl/verifiers`, `submission`, `grading`, corresponding tests | M0/M2/M3 |
| M5 | `m5_qualification` | `src/feature_rl/qualification`, corresponding tests | M0/M4 |
| M6 | `m6_factory` | `src/feature_rl/pipeline`, `registry`, `cli.py`, factory integration tests, README/runbook | M0-M5; later M7/M8 |
| M7 | `m7_learning` | `src/feature_rl/agents`, `training`, corresponding tests | M0/M3/M4/M6 |
| M8 | `m8_evaluation` | `src/feature_rl/evaluation`, `audits`, corresponding tests and experiment reports | M0/M1/M4/M7 |

Each owner may write its own `docs/reports/Mn*.md` and `docs/evidence/Mn/` and relevant scoped interface documentation. Coordinator alone assigns overlapping paths. Each owner reads upstream actual code before implementation. No dependent implementer starts against invented interfaces.

## Shared interface decisions

M0 publishes exact validated model fields and examples in `docs/interfaces.md` before any downstream implementation. Required artifact names: CandidateRecord, SourcePair, RequirementContract, ScenarioPlan, EnvironmentRecipe, VerifierBundle, TaskBundle, QualificationReport, RolloutRecord, TrainingCheckpoint, EvaluationReport. Strict extra-field rejection and canonical JSON are mandatory. SHA-256 references bind kind, visibility, schema version and exact serialized bytes. A result explicitly distinguishes success, candidate rejection, unsupported semantics, invalid measurement, infrastructure failure, and blocked dependency, with evidence and cost; absent evidence is never inferred.

Public operation vocabulary and semantic signatures (M0 owns their concrete request/result models):

```python
construct(candidate: ArtifactRef) -> OperationResult
qualify(task_version: ArtifactRef) -> OperationResult
release(task_version: ArtifactRef) -> OperationResult
run(task_version: ArtifactRef, policy: PolicyConfig, limits: ResourceLimits) -> OperationResult
grade(task_version: ArtifactRef, submission: ArtifactRef, case_seed: int) -> OperationResult
audit(run_ids: tuple[str, ...]) -> OperationResult
train(config: TrainingConfig) -> OperationResult
evaluate(config: EvaluationConfig) -> OperationResult
```

These are instance methods on configured services, not globals with hidden state. No fake CLI commands: wire only implemented operations. M0 defines shared configs; owning modules may propose reviewed extensions through M0. Required evidence records identify command/producer, UTC time, exit status, artifact references, tested revision and whether real integration or unit diagnostic. Human attestation is a separate non-model gate; agent code review does not substitute for task human review.

### Task 1: M0 contracts and storage

- [x] Write risk-based acceptance tests for all mandatory artifacts, malformed fields, nonfinite numbers, missing evidence, cross-reference mistakes, atomic duplicate writes and tampering.
- [x] Implement strict models, canonical content identities, role/visibility access and atomic immutable artifact storage; no arbitrary deserialization. Use concrete required values and examples, not empty dictionaries standing in for schemas.
- [x] Run tests, preserve commands/results, commit only owned paths, publish exact `docs/interfaces.md` with downstream examples.
- [x] Independent spec/quality review, owner fixes, scoped re-review, coordinator integration check. Original completion `cb53f99`; approved provenance extension `e16493b`; evidence and independent reviews in `docs/progress.md`.

### Task 2: M1 intake, reconstruction and partitioning

- [x] Archive actual Click PR 3228/linked request metadata, licensing, timestamps and available edit-history status. Publish capability matrix before collection.
- [x] Tests exercise merge parents, squash proof, linear/rebase spans, ambiguous/interleaved/unrecoverable history, edited request labels, fork/backport/descendant split closure.
- [x] Implement Git graph reconstruction, file classification, immutable source archives and source evidence; no historical imports/build hooks on host. Demonstrate the actual B/H pair with logs. Keep privileged H separate from authoring view.
- [x] Independent review and integrated validation. Scoped review PASS,155 coordinator tests and current-revision repeated Click intake at `c2bbd3d`; conservative supported topology and provisional task status remain explicit.

### Task 3: M2 configured provider slice

- [x] Implement a real explicitly configured provider with pinned model/prompt/options, request/response/cost archive, strict structured output and bounded failures. Reviewed provider producte14f679; current native integration passed; see docs/progress.md.
- [x] Test malformed output, provenance, truncation, configuration and budgets; reviewed/integrated299-test suite plus one protocolv3 native smoke450/92tokens,25checks PASS. Larger envelopes unqualified.
- [x] Publish provider interfaces for M3; discovery remains Task5 and M2 partial.
- [x] Independent provider code review: round5 specification/quality PASS; root current-source full/native gate passed (299tests/one450-input92-output native call).

### Task 4: M3 construction and lifecycle

- [x] Review/integrate M0 bounded artifact-read extension bf065ec; M3 must now consume explicit envelope/decoded caps and enforce aggregate staging limits in its product.
- [ ] Implement pinned Docker recipes and controlled dependency acquisition; refuse unpinned images/artifacts. Historical build hooks execute inside disposable workers.
- [ ] Implement actual isolated build/run/reset with PID/network/filesystem/resource boundaries, bounded output, deadlines, process cleanup, imported-package location and service readiness evidence.
- [ ] Test host denial and malformed recipes; execute baseline health, timeout cleanup, network enforcement and fresh/interrupted reset checks through Docker if available. A missing daemon blocks these gates, never selects host execution.
- [ ] Independent review; preserve exact blocked command if boundary unavailable.

### Task 5: M2 authoring/scenarios against baseline

- [ ] Discover real public interfaces using M3, then generate contract and scenarios from admissible evidence and B only. Ground every requirement and observable; reject critical ambiguity/unsupported semantics.
- [ ] Freeze generator configuration/retrieval traces. Author visible Click task with explicit provenance classification and requirement IDs. Any reconstructed clarification is disclosed.
- [ ] Exercise meaningful coverage, invented assertions, incomplete case accounting, and unsupported callback/identity behavior. No hard-coded feature outcome as generic generation.
- [ ] Independent review, preserving any provider/runtime execution blocker.

### Task 6: M4 trusted checkers and submission

- [ ] Generate/validate executable worker adapters and external comparisons from scenarios. Controller never imports candidate code or accepts candidate verdict fields as authority.
- [ ] Source-only positive allowlist with archive traversal/link/size/dependency controls; fresh B rebuild for every candidate. Private checks never enter worker view.
- [ ] Tests and actual worker cases cover missing/duplicate probes, forged verdict, early exit, invalid JSON, excessive output, crash, timeout, dependency shadowing, hard-coded examples and source path tricks.
- [ ] Independent spec/security/quality review. Unexecuted boundary remains unverified.

### Task 7: M5 qualification

- [ ] Validate B health, semantic feature absence, H success; targeted omission per requirement, plausible wrong, hard-coded, regression and malicious controls.
- [ ] Require blind alternative positive and real human attestation plus three fresh and interrupted reset repetitions. Missing evidence yields provisional/blocked, never qualified.
- [ ] Enforce exact repair budgets, semantic reason matching, typed rejections, evidence version binding and quarantine propagation. Test all gate bypasses.
- [ ] Independent review; prepare a concrete human review package when actual contract/control evidence exists.

### Task 8: M6 first feature orchestration

- [ ] Connect actual modules with SQLite transitions, append-only cost/event journal, idempotent jobs, dependency fingerprints and bounded queues.
- [ ] Build solver view from B with positive allowlist and inventory/leakage checks. Expose usable construct/qualify/release/grade commands over the same service methods.
- [ ] Execute Click request-to-reward route to every available gate, preserve actual failure records. No independent test harness may substitute for isolated grading.
- [ ] Independent review and coordinator cross-module run.

### Task 9: M7 genuine coding episode

- [ ] Implement actual policy/tool loop consuming only released solver artifacts; fresh conversation/state, public feedback, bounded commands and source submission.
- [ ] Preserve exact per-turn token IDs/contexts/log probabilities/masks and policy identity when backend supports training; reject missing training evidence rather than fabricate it.
- [ ] Run a real solver attempt and external grade on the released task when qualification is satisfied. Integrate M6 wiring through its owner.
- [ ] Independent review. Mark M7 partial until training gates.

### Task 10: Pilot and M7 training connection

- [ ] Automate first construction and intake approximately twenty real candidates only after the first path is usable; count all rejection/human work. Do not broaden collection to evade blocked first-task gates.
- [ ] Inspect and pin executable SkyRL/Harbor/model/backend compatibility, exact tokens, mask and stop behavior, same-case four-rollout groups and policy synchronization.
- [ ] Implement real GRPO loss/update/checkpoint/resume verification and bounded 64-group signal probe requiring eight mixed groups/four training tasks; diagnostic tensor tests are labeled diagnostic.
- [ ] With authorized compatible compute and data, run genuine optimizer update, nonzero gradients/changed tensors, reload/inference/resume and record resource costs. If blocked, finish independent code/checks and supply precise unblock command.
- [ ] Independent review.

### Task 11: M8 evaluation and audits; final M6 integration

- [ ] Implement frozen independent train/dev/test lineage checks, full assigned-trial accounting, paired task/family metrics, clustered uncertainty, weighted validity audits, costs and quarantine/restart tracing.
- [ ] Define and validate A/B/C/D arms, initialization/harness/budget comparability, credible licensed external corpus adaptation funnel, pass@1 versus best-of-k, locked-test access and checkpoint rule.
- [ ] Tests catch missing trials, denominator manipulation, mismatched seeds/cases/budgets, leaked families, missing arm/model evidence and zero audit sample evidence.
- [ ] Execute independent real evaluation only with qualified held-out data and real checkpoints; report unverified empirical gates without invented results.
- [ ] M6 owner wires implemented training/evaluation/audit entry points, runbook/setup commands. Independent whole-integrated-change review and coordinator fresh verification.

## Evidence and stopping rule

`docs/progress.md` is the durable recovery entry point. `docs/reports/` holds implementation/review reports; `docs/evidence/` holds command output and concrete gate records. Local private artifact store is excluded from solver packages and Git. Commit coherent checkpoints on the integration branch. Continue independent authorized work when a dependency is missing. Full project completion requires all real construction, human/alternative qualification, genuine solver, actual weight-update/reload and independent evaluation gates; code/tests alone do not satisfy it.
