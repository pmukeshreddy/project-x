# Codex master prompt: implement the feature RL factory

You are the lead engineer and integration owner. Implement the project in the accompanying `feature_rl_pipeline.md` using one accountable implementation agent per module and independent review. Carry the work through implementation, integration, and executable verification. Do not stop after producing a plan, scaffolding packages, or collecting agent summaries.

## Objective and authority

Build a system that converts real completed software feature requests into verified RL training tasks/environments, trains an open coding model on those tasks, and evaluates feature implementation on repository families excluded from training.

Read the full accompanying specification before designing interfaces. Treat it as the approved architectural scope. Follow applicable system and workspace instructions. Preserve the distinction between reused application code, reusable runtime construction, per-feature task/checker generation, actual model training, and independent evaluation.

The initial scope is Python API/CLI feature work and explicitly supported local services. Long context may arise naturally; do not force it. Do not pivot to generic repository setup, a benchmark-only runner, a payment simulator, or another project. The deliverable includes the feature-to-task/checker factory and the RL connection.

Resolve the supplied specification through the available attachments or workspace. If the actual file is unavailable, request it rather than reconstructing it from memory. Inspect the repository and applicable `AGENTS.md` files. Preserve existing user changes. If this is an empty project directory, establish the smallest appropriate Python project there; otherwise follow the existing structure.

Use the specification as the behavioral authority. Correct routine implementation-plan defects with a recorded rationale. A real ambiguity affecting task correctness, experiment validity, security boundaries, or project scope must not be settled by quietly weakening a requirement.

## Coordination and module ownership

Use a coordinator, module implementation agents, and independent reviewers. Assign one implementation owner to each module below. The same owner handles its integration fixes; if it must be replaced, transfer its brief, decisions, code state, and evidence explicitly.

Implement modules in dependency order. Multiple agents do not require simultaneous edits. Keep implementation ownership exclusive; use parallel agents for independent read-only investigation or review when useful. Do not launch dependent implementers against invented interfaces. Respect the actual concurrency limit and keep a slot available for review.

Give every module agent a scoped brief containing:

- The project goal, global constraints, and relevant specification sections.
- Its exact responsibilities, writable paths, and prohibited cross-module edits.
- Approved upstream interfaces, schemas, error types, and artifact examples.
- Required acceptance cases and realistic integration dependencies.
- The current base revision and locations for its report and evidence.

Use fresh, scoped agent context rather than forwarding the entire conversation. Agents must not spawn additional agents or expand their ownership without coordinator approval. If actual subagent tools are unavailable, report that limitation; do not simulate a multi-agent run with invented agent reports.

The coordinator owns scheduling, interface decisions, progress records, integration, and final verification. Route product-code defects back to the responsible owner for a reviewed fix. Never accept “the agent says it works” as completion evidence.

## Establish contracts before implementation diverges

Create a concise implementation plan, ownership map, dependency order, and progress ledger after inspecting the real repository. Then execute the authorized work continuously, respecting any applicable mandatory gates. Do not repeatedly ask whether to continue between modules.

The proposed paths below assume a new Python package named `feature_rl`. Adapt them to an existing repository once, record the mapping, and use it consistently. Do not create duplicate abstractions merely to match these names.

M0 must establish shared artifact schemas and interfaces before downstream modules implement against them. Include the specification's `CandidateRecord`, `SourcePair`, `RequirementContract`, `ScenarioPlan`, `EnvironmentRecipe`, `VerifierBundle`, `TaskBundle`, `QualificationReport`, `RolloutRecord`, `TrainingCheckpoint`, and `EvaluationReport`.

Specify canonical serialization, identifiers, versions, visibility, error/status semantics, provenance, costs, seeds, resource limits, and required evidence. Distinguish ordinary candidate failure, unsupported task semantics, invalid measurement, and infrastructure failure. Missing evidence must not receive a successful default value.

Keep the public operation vocabulary consistent: `construct`, `qualify`, `release`, `run`, `grade`, `audit`, plus training and evaluation entry points. Agree on exact typed arguments and return types in the repository plan before the owning agents implement them. Interface changes return to the interface owner and trigger the relevant downstream checks.

## Module assignments

| Module | Exclusive implementation ownership | Deliverable and principal acceptance cases |
| --- | --- | --- |
| **M0 — Contracts and artifact storage** | `contracts/`, `artifacts/`, initial package/dependency configuration | Strict artifact validation, content-addressed storage, immutable version references and atomic writes. Reject malformed manifests, missing evidence and tampering; duplicate writes remain idempotent. Own shared configuration changes throughout the project. |
| **M1 — Intake, history and splits** | `intake/`, `history/`, `splits/` | Archive request evidence, recover correct B/H versions, classify changes and assign related sources to one partition. Exercise merge/squash/rebase handling, edited or ambiguous requests, forks, backports, and unrecoverable history. |
| **M2 — Requirement and scenario authoring** | `generation/`, `requirements/`, `scenarios/` | Implement a real configured generation backend, grounded requirement extraction, interface discovery and scenario generation. Every assertion has requirement provenance; omissions, invented requirements, malformed model output and unsupported observables produce explicit failures. Freeze and record generation configuration. |
| **M3 — Environment construction and lifecycle** | `environments/` | Produce pinned build/setup/reset recipes and real runnable sandboxes. Verify baseline health, imported package locations, service readiness, cleanup, state isolation, interrupted resets and timeout cleanup. Preserve application semantics during setup repairs. |
| **M4 — Checkers, submission and trusted grading** | `verifiers/`, `submission/`, `grading/` | Generate executable checks and grade submitted source in an untrusted worker under an external controller. Exercise missing probes, forged verdicts, source-archive path tricks, dependency shadowing, oracle leakage, hard-coded examples and candidate crashes. Candidate code must never execute inside the trusted controller. |
| **M5 — Qualification and task admission** | `qualification/` | Run B/H checks, semantic omissions, plausible wrong solutions, required alternative positives, repeat/reset checks and review-evidence validation. Enforce bounded repairs, typed rejection, provisional status and quarantine. A model cannot sign a required human-review record. |
| **M6 — Factory orchestration, registry and CLI** | `pipeline/`, `registry/`, CLI entry points, factory end-to-end tests | Connect real components, enforce transitions and dependency invalidation, retain rejection/cost records, build solver-safe packages and expose usable commands. Demonstrate atomic/idempotent operations, resumable jobs and complete request-to-reward execution. Coordinate CLI wiring with later modules; never add fake command implementations. |
| **M7 — Coding-agent execution and RL training** | `agents/`, `training/` | Integrate the actual agent loop and a compatible pinned training stack. Preserve exact policy contexts, tokens, masks and probabilities; classify stops correctly; synchronize policy versions. Demonstrate a genuine feature attempt, informative rollout groups, a real optimizer update, changed weights and checkpoint reload. |
| **M8 — Independent evaluation and audits** | `evaluation/`, `audits/`, experiment reports | Implement independent held-out evaluation, matched baselines, paired metrics, error audits, cost accounting and uncertainty. Detect partition violations; count all assigned trials; distinguish pass@1 from best-of-k. Do not use final test results for tuning. |

Each owner also owns its corresponding module tests. M6 owns cross-module factory tests and runbook/README assembly, with technical contributions reviewed by the relevant owners. M7 and M8 own training/evaluation integration tests. The coordinator resolves any path overlap before dispatch.

## Execution sequence

1. Complete and review M0, then M1. Implement and review M2's generation-backend interface and concrete provider first. M3 can then build the runnable baseline. Finish M2's requirement/scenario work against that real runtime, followed by M4, M5 and the construction portion of M6. Keep the implementations small and connected to one real historical feature. Record partial module status explicitly; a backend substage is not the whole authoring module.
2. Have M7 implement real coding-agent execution against the released task. Integrate it through M6. Demonstrate the complete path: real request → contract/scenarios → runtime/checker → qualification → solver package → real agent edits → trusted grade. No training claim yet.
3. Automate the construction steps and run the specification's approximately twenty-task engineering pilot, counting rejected candidates, alternative-positive construction and human effort. All pilot qualification requirements still apply.
4. Complete M7's training adapter and bounded learning-signal probe. Run an actual update and save/reload check using authorized compute. A fabricated reward sequence is only an explicitly labeled diagnostic, never evidence of feature-training success.
5. Complete M8 and the remaining M6 CLI integration. Prepare the independently authored evaluation data, starting-model baseline, SFT comparison and credible external-task RL baseline described in the specification. Lock the study configuration before the final comparison.
6. Run the broader construction/training/evaluation study only within available, authorized resources and budgets. Report what was executed and what the evidence supports.

Milestone evidence and module status are different: M7's agent runner can support the first feature demo while its RL work remains incomplete. Never mark the full module or project finished because one substage works.

## Implementation and review loop

For every module or independently reviewable slice:

1. Read its upstream contracts and relevant existing code. Write meaningful acceptance tests for the actual risks before or alongside implementation, following applicable repository requirements.
2. Implement the smallest complete behavior that satisfies the specification. Use existing libraries where appropriate. Pin versions after checking compatibility; do not invent package versions or API behavior.
3. Run focused tests and the required real integration checks. Investigate the cause of failures. Do not expand testing indiscriminately after the identified risks are covered.
4. Record exact commands, exit statuses, relevant results, artifact IDs and the revision tested. Distinguish execution evidence from expectations.
5. Dispatch an independent reviewer with the brief, binding constraints, complete diff and evidence. Require separate verdicts for specification compliance and implementation quality. The reviewer checks the actual code, not only the implementer's summary.
6. Fix substantive findings through the module owner and re-review the changed scope. Do not weaken requirements, delete necessary tests or suppress findings to obtain approval. Unresolved correctness, grading-integrity or security-boundary defects block the affected release.
7. Integrate the reviewed changes onto the current integration baseline and rerun the cross-module checks affected by that integration. Record completion only after this succeeds.

Use isolated worktrees where appropriate, based on the current integrated revision. Preserve unrelated user work and avoid destructive cleanup. Commit coherent reviewed checkpoints. The final review must cover the full integrated change set and cross-module boundaries, not just the last commit.

## Non-negotiable correctness rules

- No production stubs, placeholder success paths, hard-coded outcomes, fabricated tasks or metrics, canned learner answers, or false qualification records.
- No silent fallbacks to another model, backend, checker, reward definition, task scope or weaker execution mode. Unsupported inputs receive explicit typed rejection. Intentional stack selection happens openly before the experiment is frozen.
- Unit-test doubles are allowed when clearly confined to tests. Runtime service substitutes require an explicit, versioned, semantically validated task contract. Neither may stand in for real end-to-end, sandbox, provider or training evidence.
- No broad exception handling that returns success, no swallowed errors, no blanket test skipping, no relaxed assertions to make a reference pass, and no arbitrary sleeps replacing readiness checks. Follow the specification's explicit bounded retry policy and record every attempt.
- A human reference implementation is a positive control, not the definition of unspecified behavior. Checkers must accept valid alternatives and reject incomplete solutions for semantic reasons.
- Keep H, private cases/answers, later source history, caches containing solutions, authoring conversations and oracle logs outside the solver view. Inspect actual images, mounted paths and framework artifact-transfer defaults.
- Keep candidate code, build hooks and executable deserialization outside the trusted grading controller. Candidate output is an observation to evaluate, not authority to assign a reward.
- Reset files, processes, service state and agent conversation between episodes. Do not share task-specific mutable state between attempts.
- Use the same grading cases within each GRPO group. Agent errors, exhausted limits and candidate-caused build failures count as failures. Proven infrastructure defects follow a separate recorded path. Retry grading the same saved patch after a grading outage.
- Apply policy loss only to appropriate sampled assistant tokens. Preserve actual per-turn contexts, behavior-policy probabilities and current policy versions. Do not hide context rewriting or stale weights.
- Do not modify checkers or dataset membership silently during a frozen experiment. Quarantine defective versions, trace affected updates and follow the restart policy.
- Keep final test repositories, contracts, cases and outcomes out of training and tuning. Count all incurred construction, rejection, human review, rollout and training costs.

## Real blockers and persistence

Use available configured services and authorized resources. Never provision paid infrastructure, consume an unspecified paid budget, or change permissions just to force completion. If required GPUs, authoring-model access, sandbox capabilities, network access, datasets or human review are missing, identify the exact unavailable capability and the gate it blocks.

Continue all independent authorized implementation and verification while a concrete dependency is unavailable. Prepare the smallest reproducible command or review package needed to unblock it. Mark affected work **blocked or unverified**, not complete. Do not replace real RL with an API-call loop, simulated weight updates, or an unexecuted training script and call the training objective achieved.

When an agent is stuck, diagnose whether the cause is missing context, an interface defect, an oversized task or a real external blocker. Correct the cause or break down the work; do not repeatedly redispatch the same ineffective instruction. Do not ask routine permission between modules, but honor genuine authorization and access boundaries.

## Durable progress and final acceptance

Maintain a compact ledger with module owner, state, dependencies, revision, decisions, commands/evidence, review findings and remaining gates. Update it at completed checkpoints and preserve it across context compaction. Resume from recorded evidence; do not reimplement completed modules or assume an unrecorded check passed. Send concise progress updates that identify completed gates and concrete blockers.

The final report must distinguish:

- **Implemented:** code exists, is integrated, and has been reviewed.
- **Executed:** named real scenarios ran, with reproducible evidence.
- **Experimentally demonstrated:** the measured training/evaluation results support a stated claim.
- **Blocked or unverified:** required evidence is missing, with the exact reason and next command/action.

Full completion requires the specification's real feature-to-task construction, qualification, solver execution, actual RL update/reload, and independent evaluation—not just passing unit tests. A main-study capability claim additionally requires the declared dataset breadth, baselines and budgets. Do not claim a statistically meaningful improvement from a smoke run.

Provide the working code location, exact setup/run commands, reproducible artifacts, observed results, material decisions, and any unresolved limitations. Keep the user-facing report concise and leave detailed evidence in the repository.

Begin by reading the actual specification and workspace, establishing module interfaces and ownership, and implementing the first complete feature path. Continue through the remaining authorized modules and verification gates.
