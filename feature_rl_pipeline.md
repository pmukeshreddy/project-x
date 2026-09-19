# From real feature requests to useful RL training environments

Architecture specification — revised 19 September 2026

Status: researched design. The linked candidate requests and framework documentation were inspected; repositories have not been reconstructed, environments have not been executed, and no model has been trained in this work. Operational targets below are proposed acceptance criteria, not reported results.

## Read this first: the project and the actual construction work

The project remains: **turn real software feature requests into verified RL training environments, train a coding model on them, and measure whether it improves at implementing features in unfamiliar repositories.**

The application code is reused. Our factory constructs a runnable starting state, a clear feature task, executable behavior checks, and evidence that those checks are useful. It also supplies repeatable execution and integration with real model training. We do not implement the requested feature in the solver's starting workspace.

| What exists at intake | What our factory creates |
| --- | --- |
| A historical feature request and its discussion | A grounded, solver-visible requirement contract with ambiguities resolved or rejected |
| Code before and after a completed implementation | A correct baseline, a private positive control, and an evidence-backed link between them |
| Source code, manifests, and perhaps some tests | A reproducible runtime recipe, service setup, reset procedure, and public development workspace |
| Feature requirements and potentially incomplete PR tests | Scenario plans, setup code, externally evaluated behavior checks, and adversarial controls |
| Candidate tasks of unknown quality | Versioned, qualified task bundles, rejection records, audits, and measured construction cost |
| A model able to use development tools | Training trajectories, actual weight updates, and independently measured transfer results |

Runtime recipes can be reused only when their compatibility fingerprints match. Features produce different task instances and sometimes require different runtime recipes. Report unique runtime recipes, repository families, feature tasks, and rollouts separately; do not count each feature as an entirely new application environment.

**Initial input:** a completed real feature request, its repository, and recoverable before/after code. Completed requests let us check a candidate verifier against an existing implementation. Arbitrary unfinished requests without a defensible positive control are outside the first version. The generator must not invent a solution and treat its own agreement with that solution as proof.

**Meaning of verified:** a specific task version has passed recorded construction, behavioral, adversarial, and review gates. It is evidence of quality within the disclosed contract, not a proof that every possible implementation will be graded correctly. The design therefore includes audits and a correction path.

This revision is an architecture design, not an implementation-completion report. Every release or training claim below has an execution gate.

## 1. Goal and testable claims

Build a repeatable system that converts real software feature requests into executable training tasks, validates their rewards, and trains an open coding model that completes more features in repositories excluded from the project's training data.

The primary capability question is whether training on the accepted tasks improves feature completion relative to the same model before those updates, under fixed evaluation conditions. The stronger comparative question is whether this task supply produces more improvement than a credible existing repository-task supply at a matched training budget. These are distinct claims and are reported separately.

The first efficacy study's population is eligible Python CLI/API feature tasks in repository families withheld from project training, including explicitly supported local-service behaviors. Broader application-development claims require expanded task coverage and another reported evaluation.

Three claims must be established separately:

1. Construction: requests can be converted into usable environments with measured automation, cost, and rejection rates.
2. Validity: passing a verifier is credible evidence of satisfying a disclosed feature contract, within the limits of its coverage and audits.
3. Learning: actual model weight updates improve independently evaluated outcomes on withheld repository families.

Long contexts may emerge from exploration and debugging. Record context use and failure patterns, but do not pad prompts, impose a minimum trajectory length, or advertise a long-context capability gain without a separate controlled experiment.

No hardware budget has been supplied. This design commits to a training approach and feasibility gates; it does not assume a particular GPU purchase, cloud booking, training cost, or expected score increase.

## 2. Main decisions

| Decision | Choice and reason |
| --- | --- |
| Initial data | Historical, merged, additive feature requests with reconstructable before/after states. A reference implementation gives an executable positive control. |
| Initial scope | Python public APIs and CLI features; add RQ's local Redis service after clean-reset validation. Expand breadth after the construction loop works. |
| Task specification | A short, explicit feature contract grounded in admissible request material and existing public behavior. |
| Reward | Binary complete-feature success plus required regression preservation. Requirement-level outcomes are diagnostics initially. |
| Grading | Submit source changes, rebuild in a fresh untrusted worker, and let an external trusted controller judge observable behavior. |
| Training | SkyRL–Harbor is the initial integration candidate; synchronous GRPO, with FSDP and vLLM as proposed backends, subject to a pinned compatibility test. |
| Initial sampling | Fixed, diversity-balanced sampling. Adaptive generation and sampling are later, isolated experiments. |
| Primary evaluation | Independently constructed tasks in withheld repository families, with identical inference settings across model comparisons. |
| First deliverable | One fully validated request-to-task conversion; then an engineering pilot across three repositories. |

Three possible data approaches were considered. Historical PR mining is the initial choice because it preserves real demand and a positive control. Feature deletion is a useful separately labeled baseline or later augmentation, but can leave clues and inconsistent callers. Fully synthetic feature requests can broaden coverage later, but require independent evidence that the request is coherent and its oracle is correct.

Generic PR-to-environment conversion is established work: SWE-Bench++ already covers feature requests, environment synthesis, and execution-based quality checks; SWE-Next studies executable PR mining and environment reuse. FeatureBench constructs feature-level tasks through dependency analysis. The contribution here must be measured feature-contract quality, construction efficiency, and downstream training value. [SWE-Bench++](https://arxiv.org/abs/2512.17419), [SWE-Next](https://arxiv.org/abs/2603.20691), [FeatureBench](https://arxiv.org/abs/2602.10975)

## 3. Pipeline and ownership of feedback

~~~mermaid
flowchart TD
    A["Feature request, repository, completed implementation"] --> B["Assign repository-family splits"]
    B --> C["Train and development sources"]
    B --> T["Independent test construction"]
    C --> D["Recover code versions and define requirements"]
    D --> E["Build runtime, scenarios, and checker"]
    E --> F{"Qualification passes?"}
    F -->|No| Q["Diagnose and repair within fixed limits"]
    Q -->|Retry| D
    Q -->|Budget exhausted or unsupported| R["Reject with evidence"]
    F -->|Yes| G["Freeze qualified task bundles"]
    G --> H["Training tasks: agent attempts and trusted rewards"]
    H --> I["GRPO weight updates"]
    I --> J["Choose with development set; freeze model"]
    T --> K["Locked test on withheld repositories"]
    J --> K
~~~

Repairs rerun the failed stage and all gates invalidated by the change. They never silently weaken the requested behavior. Freeze training and development bundles before the main comparison; use audit discoveries to stop or restart an affected experiment, not silently change its data. Development results choose checkpoints under a fixed rule. The locked test supplies no feedback for generator tuning, sampling, checkpoint choice, or training.

| Stage | Main input | Versioned output | Admission gate |
| --- | --- | --- | --- |
| Source discovery | Issue, PR, discussion, historical CI and docs | Candidate record with provenance | Concrete feature, eligible source, recoverable history |
| Partitioning | Repository and request relationships | Repository-family and task-lineage assignments | No related task crosses partitions |
| History reconstruction | Commit graph and integration metadata | Baseline B, reference H, categorized changes | Correct before/after relationship |
| Contract authoring | Request evidence and baseline code | Visible request plus requirement map | Mandatory behavior is disclosed and unambiguous |
| Environment construction | Historical manifests and CI | Pinned image, dependency artifacts, reset recipe | Baseline runs; repeatable reset |
| Scenario planning | Contract and actual project interfaces | Preconditions, actions, observations, expected results | Every requirement has an observable check |
| Oracle construction | Scenario plan and reviewed test candidates | External probes and preserved-regression suite | Assertions trace to the contract; no expected answers in worker |
| Task qualification | B, H, probes, alternative and broken solutions | Validation report and typed disposition | Positive controls pass; negative controls fail correctly |
| Packaging | Qualified artifacts | Immutable task bundle | Solver view excludes privileged material |
| Policy calibration | Training-only tasks and selected model | Solve estimates, failure categories, cost profile | Enough usable trajectories and learning signal |
| RL training | Qualified training bundles | Checkpoints, rollouts, costs, optimizer records | Correct masks, rewards, stopping and resume behavior |
| Development review | Dev tasks and sampled train submissions | Checkpoint selection and quality audits | Fixed evaluation conditions; no test leakage |
| Final evaluation | Frozen model and frozen test tasks | Paired results, uncertainty, limitations | Same task roster and inference limits for all arms |

The dataset partition is assigned before generated contracts, traces, or descendants exist. The reconstruction process then follows commit ancestry before the author reads B. This order prevents both related-task leakage and specifications based on the wrong code version.

## 4. Start with concrete repositories and requests

| Candidate repository | Feature families | Role in the pilot |
| --- | --- | --- |
| [Click](https://github.com/pallets/click) | Command dispatch, option behavior, help and public exception APIs | First construction and black-box CLI grading |
| [Marshmallow](https://github.com/marshmallow-code/marshmallow) | Serialization, validation, interactions among fields and schemas | Structured input/output grading across components |
| [RQ](https://github.com/rq/rq) | Queue metadata, job lifecycle, persistent job state | Add a local Redis instance and worker cleanup after the initial loop works |

These are candidate environments, not validated selections. Historical tasks use their historical dependency versions. RQ depends on Click: pin that dependency and examine shared code and task lineage rather than pretending these repositories are automatically independent evaluation clusters.

An inspected feature candidate is [Click PR 3228](https://github.com/pallets/click/pull/3228), adding suggestions for misspelled commands and a public NoSuchCommand exception. Its companion formatting changes illustrate why the whole PR cannot automatically become an implicit specification. The actual request and supported public behavior must determine what is required; bundled unrelated changes must be excluded or explicitly justified. Its commits and tests have not been executed here.

[Click PR 3473](https://github.com/pallets/click/pull/3473), concerning argument help, is another source candidate. [Marshmallow issue 2787](https://github.com/marshmallow-code/marshmallow/issues/2787), concerning field-level transformations, and RQ's unique-job feature recorded in its [changelog](https://github.com/rq/rq/blob/master/CHANGES.md) are leads whose exact implementation pairs still need to be recovered. They are not accepted tasks.

The first task should be modest enough to diagnose the factory. The larger dataset must also cover features requiring interactions across modules and state, with complexity distributions reported. Success on the initial CLI/library slate supports that scope only; it does not establish full-stack application development.

Publish a capability matrix before task collection: supported public interfaces, lifecycle/state operations, exception observations, callbacks, object identity and external dependencies. Accept a feature only when its important semantics are represented faithfully. Report rejected feature categories so a convenient serialization boundary cannot silently redefine the project's coverage.

Initially exclude performance-only optimization, large refactors, external SaaS dependencies, platform-specific behavior, and ambiguous multi-PR projects. Introduce these as explicit later tracks with their own reset and grading requirements.

## 5. Source intake and historical reconstruction

Archive original source text, available edit history, retrieval date, request/discussion timestamps, repository identity, licensing metadata, CI configuration, PR metadata and commit relationships. A label such as feature is a discovery hint, not an acceptance decision.

Define B as the target repository immediately before the feature's integration and H as the complete integrated reference. Recover these from commit ancestry and the integration diff. Normal merges, squash merges and rebases require different handling; current API base/head values are not sufficient evidence. Reject uncertain or interleaved histories in the pilot. [GitHub merge methods](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/configuring-pull-request-merges/about-merge-methods-on-github)

Separate changes into implementation, public documentation, tests/fixtures, dependency/build changes, and unrelated changes. The reference must include all legitimate support needed for the feature. Manually inspect mixed-purpose files in the pilot.

Use two explicit provenance labels:

- Historical request: the visible contract is recoverable from a declared preimplementation cutoff and existing public obligations.
- Reconstructed specification: part of the contract was recovered from later documentation or implementation evidence and rewritten as an explicit user-facing requirement.

Keep these labels in every result. Reconstructed examples may be useful training data, but cannot substantiate an unqualified claim about untouched historical requests. Current edited issue text is not proof of what was available earlier.

The contract author initially sees admissible request sources and B, without the implementation diff. A separate reviewer can use H to check feasibility and detect omissions. Any necessary extra requirement must be disclosed in the task and its provenance recorded. Generating more tests cannot solve an undefined requirement.

## 6. The task contract

Each task records the following contract:

| Field | Required meaning |
| --- | --- |
| Capability | The new externally observable behavior the user requested |
| Entry points | Public API, CLI, protocol or application interfaces involved |
| Mandatory requirements | Separately identifiable behaviors, each linked to admissible source evidence |
| Existing obligations | Relevant compatibility and regression guarantees |
| Clarifications | Any chosen interpretation the solver must know |
| Allowed changes | Files, interfaces, dependencies and generated artifacts permitted by the task |
| Public checks | Tests and examples the solver may inspect and run |
| Private checks | Contract-grounded cases and expected results held by the grading controller |
| Episode limits | Declared tool, token, time and resource limits |
| Submission | Source changes and explicitly allowed additional artifacts |

Do not require private helper names, human-patch structure, or incidental output formatting unless the contract actually demands them. A valid alternative implementation should pass. This addresses documented issues with underspecified requests and overly restrictive benchmark tests. [SWE-bench Verified](https://openai.com/index/introducing-swe-bench-verified/)

For the Click candidate, requirement candidates include misspelled-command suggestions and preservation of normal command execution. Exact suggestion ordering, error wording, normalization and public exception APIs require separate source grounding. They are not invented merely because they would be useful edge cases.

Every mandatory assertion has a requirement ID. Every mandatory requirement has at least one meaningful behavioral check. This is traceable coverage, not a mathematical guarantee of complete correctness.

### 6.1 How a request becomes a task and checker

This is the central factory component. A generation model proposes structured requirements, scenarios, and checking code. A deterministic pipeline runs that code and verifies its evidence. The training model never grades its own implementation. These are logical stages; they do not require a distributed multi-agent platform.

| Step | What the generator can read | Required output | How the output is checked |
| --- | --- | --- | --- |
| Locate interfaces | Admissible request text, B, public docs and baseline tests | Relevant public entry points, callers, source spans, existing obligations | Import/command discovery and execution in B; an AST map alone is insufficient |
| Draft contract | Located interfaces and admissible evidence | Individually identified requirements, exclusions, and unresolved questions | Each requirement has source evidence or a disclosed clarification; critical ambiguity blocks automatic acceptance |
| Plan scenarios | Frozen draft contract and interface capabilities | Preconditions, action sequence, observations, expected relation, reset needs, requirement IDs | Every mandatory behavior is exercised; assertions describe public outcomes rather than the human implementation's structure |
| Author checker | Contract, scenarios, B and relevant reviewed test candidates | Fixture/setup code, worker adapter, controller assertions and case generators | Parse and schema checks, sandboxed dry runs, bounded output and complete-case accounting |
| Challenge checker | Contract and reference available to separate control-authoring stage | Missing-feature run, reference run, semantic negative controls, alternative positives | Correct positive behavior passes; known violations fail for their intended reasons; unexplained disagreement blocks admission |
| Qualify and package | All stage artifacts and evidence | Immutable task bundle or explicit repair/rejection result | Full qualification in section 9, followed by solver-view leakage inspection |

The first contract draft is produced without H. Only after that draft exists may a privileged reviewer inspect the reference and PR tests to identify support needs, missing information, or contradictions. Any new observable requirement returns to contract review. If it cannot be grounded in admissible request material, either disclose it as a reconstructed clarification or reject the historical-request claim. The checker author never silently promotes a reference implementation detail into a requirement.

The model receives retrieved, attributable code context and can request more files. Do not feed an incomplete snippet and assume it describes the whole feature. Keep retrieval traces and generator model/prompt versions. Long code contexts are supported when needed; their length is not the objective.

Each scenario has this contract:

| Field | Meaning |
| --- | --- |
| `requirement_ids` | Which disclosed obligations this scenario tests |
| `preconditions` | Input data, service state and configuration established before the candidate runs |
| `actions` | Calls or commands through the actual supported interface |
| `observations` | Outputs, exit status, files or service state the external controller can inspect |
| `expected_relation` | Exact value, set membership, ordering, declared tolerance or a justified behavioral relation |
| `oracle_origin` | Requirement evidence, independently derived rule, or justified reference behavior |
| `case_generation` | Valid input domain, seed policy, boundary cases and guaranteed requirement coverage |
| `reset_and_limits` | State restoration, command deadline, output limits and declared resource bounds |

An adapter faithfully invokes the original public interface. It must not reimplement the feature itself or transform the requested task into an easier substitute. The controller's comparison logic is separate from code that imports or executes the submitted application.

### 6.2 Where expected answers come from

The reference is a useful positive control, not an infallible specification. A merged implementation can be buggy, incomplete, or more restrictive than the request.

- Prefer expectations derived directly from the contract: requested values, exact data transformations, explicit order, and observable state transitions.
- Use domain invariants and metamorphic relations when they follow from the request, such as preserving distinct inputs during deduplication. Such relations supplement concrete cases unless they fully identify the required behavior.
- Use reference comparisons only on contract-relevant observables. Compare normalized values when representation is unconstrained; require exact formatting only when the task discloses it.
- Disagreement between requirement evidence and H is a task-authoring problem. Investigate, narrow with explicit disclosure, or reject; do not automatically redefine correctness as whatever H outputs.
- Generation-model confidence and agreement between two prompts are not execution evidence. Human review and executable counterexamples remain necessary for semantic ambiguities and audit samples.

Property-based cases use a recorded seed and always include the mandatory scenario families. Random sampling must not accidentally omit an entire requirement. During a GRPO group, all candidate patches receive the same grading cases so relative rewards do not depend on different test difficulty; policy-sampling seeds still differ. Change grading seeds across groups within the frozen input distribution, and archive them. Final evaluation uses the same frozen case sets for every compared model.

### 6.3 A complete illustrative conversion

This example explains the construction mechanism; it is not an executed RQ task or a claim about RQ's exact API.

Suppose the admissible request explicitly requires a new unique-enqueue operation: repeating the same job ID in one pending queue keeps one job, distinct IDs remain distinct, and the existing ordinary enqueue operation keeps its previous behavior. The task discloses the new entry point and limits the requirement to sequential submissions while jobs remain pending. Concurrency and reuse after completion are not secretly added to the checker. If a real request leaves those distinctions necessary but unresolved, it needs a recorded clarification.

| Factory output | Concrete content |
| --- | --- |
| Starting workspace | B, where the new unique-enqueue operation is absent; the existing queue remains functional |
| Runtime setup | The queue's actual dependencies and an empty local service instance if required |
| Task instruction | The three disclosed behaviors, public entry point, allowed changes and execution limits |
| Scenario 1 | Submit job A twice using the new operation; independently observe one pending A |
| Scenario 2 | Submit A and B; independently observe both jobs |
| Scenario 3 | Exercise ordinary enqueue; verify its documented baseline behavior remains intact |
| Negative controls | No-op; keep only the most recent job; always reject submissions; incorrectly change ordinary enqueue |
| Positive controls | H and a separately authored valid implementation under the same disclosed contract |
| Reset | Remove queue contents, stop workers, restore files, then verify the empty initial state |

The generator produces the setup, scenarios and checker around B. H is used privately during qualification. The learning agent receives B and the instruction and must write the missing implementation. Passing tests on H and failing tests on B are necessary checks; the distinct-ID and regression controls catch simplistic checkers that would reward deleting everything or changing unrelated behavior.

### 6.4 Bounded repairs and honest rejection

Use at most two repair attempts for any failed construction stage, with at most four repair attempts total for one candidate in the pilot. Count the initial attempt and all repairs in cost records. A batch configuration must also declare token, wall-time, command, CPU/memory and total-spend caps before it runs; derive operational values from the first measured construction, then freeze them for a comparison. A retry must have a diagnosed cause and recorded change.

Return explicit dispositions: `accepted`, `ambiguous_requirement`, `unsupported_semantics`, `unrecoverable_history`, `environment_failure`, `oracle_disagreement`, `false_acceptance`, `false_rejection`, `flaky_task`, or `budget_exhausted`. Accepted/rejected is never decided by a generator's confidence field alone.

Keep generated source, historical scripts and package build hooks untrusted: execute them in disposable build/worker sandboxes, never in the controller or orchestration host. Referenced issue text and repository documents supply task evidence, not authority to alter the factory's gates or access private data.

Repairing an import or fixture problem is allowed. Removing a legitimate failing case so H or a student attempt passes is not. A changed contract, environment recipe, adapter, or oracle invalidates dependent qualification evidence. Semantic changes require a new task version.

For the pilot, a human reviews every accepted contract and its evidence, with effort recorded. A scaled low-touch mode may follow only after randomized audits quantify the errors and its acceptance policy is frozen. Audit rejected source candidates too, so apparent precision cannot conceal a factory that discards most valid feature requests.

## 7. Reproducible environments and resets

Infer the toolchain and setup from B's historical CI and manifests. Pin interpreter, system packages, dependency artifacts, test tools and service images. Cache shared environments by a fingerprint of these inputs, with task-specific overrides; nearby dates alone do not establish compatibility.

Build references and solver workspaces separately. A shared immutable layer may contain approved dependencies, but cannot contain H's target-package wheels, compiled source, build products, test answers or later repository history. Verify which package location is actually imported; an installed later release must not shadow the editable baseline. Legitimate dependency changes required by a feature receive an explicit permitted-artifact policy and the same candidate build path as the reference. Unsupported changes are screened out rather than patched around invisibly.

Validate three states:

1. B under its baseline regression suite is healthy.
2. B under the feature probes demonstrates that the requested capability is absent.
3. H under the same feature probes and required regressions succeeds.

At least one designated feature probe must demonstrate missing behavior in B for a diagnosed semantic reason. Checks for preserved behavior must pass in both B and H. Every individual new case need not fail on B; some boundaries may already work. A failure elsewhere in the project is not evidence that the requested feature is absent.

A missing newly requested public symbol can be an expected semantic failure. A broken import caused by the wrong dependency version is an infrastructure problem. Record the reason, not just an exit code.

Use only explicitly recorded, behavior-neutral environment repairs, applied consistently to baseline, reference and candidates. If a repair changes task semantics, create a new task version and revalidate it. Do not suppress failing assertions to raise yield.

For each pilot task, repeat a fresh reference run and a reset-after-interruption run at least three times as a smoke gate. This checks obvious nondeterminism; it does not estimate a tiny failure probability. Timing-sensitive tasks need stronger targeted validation.

Record locale, timezone, process environment, randomness sources and service health checks in the recipe. Use condition-based readiness checks with a deadline; a fixed sleep does not prove that a service is usable. Verify a restored episode matches the declared logical initial state, including external service records, not only the source directory. Persistent private oracle state and writable runtime caches are never shared across episodes.

RQ episodes receive independent Redis state, network namespaces and worker process groups. Reset destroys service state, terminates and reaps workers and schedulers, removes temporary artifacts, and restores the source snapshot. Verify cleanup after success, failure and timeout. Mocking Python time alone does not control Redis expiration, so wall-clock scheduling tasks are deferred initially.

## 8. Grading architecture

Keep three execution roles separate:

| Role | What it contains | What it can decide |
| --- | --- | --- |
| Development sandbox | Baseline source, permitted dependencies, request and public tests | Agent edits and local experiments |
| Candidate build/execution worker | Fresh baseline plus validated submitted source changes | Executes candidate behavior and returns observations |
| Trusted grading controller | Requirement map, hidden cases, expected results and completion ledger | Selects probes, compares results, assigns the reward |

The agent submits source changes. The controller validates paths, links and allowed artifacts; rebuilds from B; and applies them to an untrusted worker. It does not copy the agent's installed packages, test runner, caches, verdict files, or complete runtime state.

Package extraction rejects path traversal, unexpected symlink/hardlink targets, oversized archives and writes outside declared submission roots. Changed or newly written solver tests may be retained as development artifacts, but do not replace the grading suite. Build manifests are handled under the task's declared dependency policy. The original private grading manifest is authoritative for which cases must execute.

Build the solver snapshot from B alone. Remove future Git objects/refs, later documentation, target-package build caches, bytecode or installed wheels containing the completed feature, and task-author conversations. A fresh baseline-only Git root may be provided for normal diff/commit workflows. Preserve legitimate baseline dependencies. Restrict runtime network access to declared local services; fetch approved packages during controlled construction rather than letting the solver download the published reference solution.

Dependency and build changes are allowed when the visible task permits them. Resolve declared dependencies through a controlled, pinned artifact supply, then execute builds without oracle data, credentials, host sockets or unrestricted networking. A blanket prohibition on all dependency changes would make some real feature tasks invalid; tasks beyond the current build policy are out of scope and counted as such.

For initial tasks, use CLI calls or a small serialized API adapter in the untrusted worker. Send inputs, receive bounded ordinary data, and compare externally. Hidden expected values and the comparison code remain in the controller. Avoid importing submitted Python into the controller's interpreter and avoid executable deserialization. Same-process graders can expose answers or be monkey-patched, as observed in published coding evaluations. [METR reward-hacking analysis](https://metr.org/blog/2025-06-05-recent-reward-hacking/)

An adapter must not reduce the actual public interface to a toy imitation. Document which semantics it preserves. If callbacks, object identity or complex side effects cannot be observed faithfully through the adapter, reject that task from this track or add a justified adapter before admission.

Use an established sandbox boundary between worker and controller: isolated PID, mount and network namespaces or equivalent isolation; separate privileges; no ptrace access to controller processes; no shared writable supervisor state; and externally enforced resource limits. The supervisor never loads submitted packages or build hooks. The required boundary is part of the chosen existing sandbox's configuration and validation, not a plan to invent a new isolation runtime.

Public repository tests remain useful diagnostics. Tests that execute alongside candidate code are not independently tamper-resistant proof. Any regression obligation contributing to a trusted pass must have an externally controlled observation; reporting pytest output alone is insufficient. Uncovered native-test guarantees are reported as a limitation, not silently claimed.

The controller records every expected probe's completion. A missing case, forged result, early exit or unexpected skip cannot count as success. Read-only hidden tests are still readable, so they are not mounted in the agent or candidate process.

For a supported stateful task, the controller establishes fixtures and observes service state through an independently controlled connection. The application may mutate its assigned state as part of the task; the worker cannot reach the controller's private expectations or administrative state. If the only available evidence for a required side effect is the candidate reporting `passed=true`, the task lacks an adequate observation adapter and is rejected from this track. Ordinary output can still be hard-coded for known cases, which the case generators and adversarial controls target; isolation does not prove semantic correctness.

The live agent receives public-test feedback only. It has no callable hidden-test endpoint, and hidden expected values, per-case failures and oracle logs do not return to its episode. Terminal reward is delivered to the trainer after submission. Repeated training rewards are still an optimization surface, which is why fresh seeded cases and ongoing audits are needed.

Each episode also starts with a fresh agent conversation and empty task-specific working memory. Persistent authoring sessions, previous solver transcripts or cross-task retrieval caches cannot leak answers into later episodes. Knowledge learned through declared model weight updates is the intended transfer mechanism.

Harbor's separate verifier supports a separate environment and declared artifact transfers. Use this as a building block, with an allowlisted source-artifact transfer and a second untrusted candidate worker under the grading controller. Separate mode by itself does not make executing submitted code inside the verifier safe. The controller, replay and observable-behavior adapters remain project work. [Harbor separate verifier](https://docs.harborframework.com/core-concepts/tasks/separate-verifier)

Inspect and constrain the framework's automatic artifact-transfer paths as well as explicitly declared paths. It is not enough to allowlist a custom patch path if other framework defaults also copy agent-controlled files. A hostile artifact must never overwrite verifier code, setup commands or the reward location. Network policy must be applied to the actual sandbox backend and exercised in qualification; a configuration field alone is not evidence of enforcement. [Harbor network policies](https://docs.harborframework.com/core-concepts/tasks/network-policies)

## 9. Qualifying a task before training

Qualification produces evidence for both rejection of wrong solutions and acceptance of valid alternatives.

| Control | Required observation |
| --- | --- |
| Complete reference | Passes the complete contract and required regressions |
| No-op/baseline | Fails for the missing feature |
| Requirement-specific omission | Fails the omitted behavior while otherwise remaining runnable |
| Plausible incorrect implementation | Fails a meaningful behavioral case |
| Hard-coded public examples | Fails on additional legitimate inputs |
| Regression-inducing implementation | Fails a preserved-obligation check |
| Early-exit or forged-verdict attempt | Cannot obtain a passing controller verdict |
| Alternative valid implementation | Passes despite different internal structure |

Require at least one targeted semantic negative control per mandatory requirement in the pilot. Every accepted pilot task also requires an independently authored alternative positive implementation. Tasks lacking one remain provisional and are excluded from the pilot's qualified-task count and primary training pool. The alternative author sees the visible contract and B, without H or private checker code; human review verifies that its behavior satisfies the contract. This is an additional control, not proof that authoring errors are statistically independent. A later relaxation must be declared as a new qualification policy and evaluated with sampled alternatives and false-rejection audits before its tasks enter a main comparison.

Mutants must fail for the intended behavioral reason. A syntax error is not useful coverage of a permission rule. Exclude equivalent mutants from the mutation-score denominator. Mutation score is a diagnostic, not an estimate of correctness on all future submissions.

Maintain a separate adversarial-patch suite: forged verdict files, evaluator detection, hard-coded known inputs, skipped execution, protocol manipulation, excessive output, dependency shadowing, path/link tricks and retained state from earlier episodes. Run these through the actual submission, rebuild and grading route, not a substitute unit harness. All mandatory controls must be accounted for; any known unresolved false pass blocks that task version's release.

Control authors work from the contract and relevant implementation information, not from a list of private assertions to satisfy or defeat. Diagnose each control's expected validity separately. Test collection counts, declared probe IDs, completion receipts and assertion results are all checked: an empty test collection or a runner that exits successfully before probing the feature must fail qualification.

Audit samples of both accepted and rejected actual model submissions, blinded to training arm when feasible. Resolve disagreements against the contract using execution-based counterexamples. Track unresolved cases separately. Report invalid-among-accepted, accepted-among-invalid and rejected-among-valid; these are different quantities. Include sample sizes and uncertainty.

Use explicit denominators: reward error among rewarded patches is invalid accepted patches divided by all accepted patches; false acceptance is accepted invalid patches divided by all adjudicated invalid patches; false rejection is rejected valid patches divided by all adjudicated valid patches. Use the sampling weights described below for population estimates. Mutant rejection rate is a separate stress-test metric and cannot substitute for any of these quantities. Do not resolve an audit disagreement by asking the same generation model to approve its original checker.

The audit unit is a complete submitted patch with its task and verifier version. Stratify random sampling by repository family, accepted/rejected status and failure category; record each patch's sampling probability. Additional suspicious cases may be oversampled, with weighting used for population estimates. Report patch-validity errors separately from invalid-environment rates. Human adjudication and reproducible counterexamples settle ambiguous cases; an LLM may assist review but is not the final correctness oracle.

After a discovered verifier defect, quarantine the version, identify affected rollouts and checkpoints, and publish the correction. Regrade archived artifacts where possible. Regrading cannot undo weight updates already made; clean comparisons require restarting from an unaffected checkpoint or explicitly disclosing the contamination.

Version requirements and oracle logic during a run. A legitimate randomized test generator may use fresh seeded cases within the frozen contract; archive those seeds. Changes to the contract or generator create a new version and require revalidation.

## 10. Artifact contracts and the minimal platform

Use Python for orchestration, append-only JSONL event records, a small SQLite task/run index, and content-addressed local/object storage for artifacts. A distributed service or user interface is unnecessary for the first vertical slice. Workers can be added behind the same task interface after throughput measurements justify them.

| Artifact | Minimum contents |
| --- | --- |
| CandidateRecord | Source URLs and snapshots, timestamps, license, commit relationships, repository family, request lineage, screening decision |
| SourcePair | Verified B/H digests, integration relationship, changed-file categories and admissible-source cutoff |
| RequirementContract | Visible request, requirements, evidence links, compatibility obligations, allowed changes and ambiguity decisions |
| ScenarioPlan | Requirement-linked preconditions, actions, observations, expected relations, valid input domains, oracle origins and seed policy |
| EnvironmentRecipe | Image/dependency hashes, setup/reset commands, services, resource caps, neutral repair log |
| VerifierBundle | Private cases and comparisons, worker adapters, public examples, case-completion manifest, control patches and permissions |
| TaskBundle | Baseline digest, public solver view, contract/version, adapter version, private-oracle reference, reference-solution reference |
| QualificationReport | Three-state runs, control patches, repeat/reset results, audit decisions and rejection reasons |
| RolloutRecord | Task and model versions, sampled token IDs, masks, actions/observations, seeds, submitted artifact, stopping reason, reward and cost |
| TrainingCheckpoint | Weights, optimizer state, reference checkpoint, data position, policy version, configuration and consumed-task versions |
| EvaluationReport | Frozen task roster, model/harness versions, paired results, uncertainty, audits and cost ledger |

Private artifacts are not ordinary fields serialized into the solver prompt; references are resolved only by authorized pipeline stages. A solver-visible bundle has its own independently checked manifest.

States are: discovered, screened, reconstructed, built, qualified, calibrated, released, quarantined and rejected. A transition requires its recorded gate evidence. Rebuild keys incorporate source, recipe, contract and verifier versions. Retries are idempotent; qualified outputs are immutable.

`calibrated` describes student difficulty and expected rollout cost; it is not evidence that a task is semantically valid. Valid but currently unsolved tasks remain valid. Missing alternative-positive or human pilot-review evidence produces a provisional task that cannot transition to qualified.

### 10.1 Public and private package contents

| Package member | Visibility | Purpose |
| --- | --- | --- |
| `instruction.md` | Solver | Complete feature requirements and allowed behavior |
| `workspace/` | Solver | B plus approved neutral setup changes, with no solution artifacts |
| `public_checks/` | Solver | Examples and existing tests available during development |
| `runtime_manifest.json` | Solver-safe fields and runtime controller | Declared commands, resources and environment identity; private storage paths omitted |
| `task_manifest.json` | Factory and trainer | All immutable artifact IDs, split, versions and qualification status |
| `scenarios.json` and `controller_checks/` | Grading controller | Private inputs, expected outcomes, comparisons and mandatory case IDs |
| `reference/` and `controls/` | Qualification workers only | H, alternative positives and semantic/adversarial negatives |
| `qualification.json` | Factory; sanitized report for release | Evidence and explicit acceptance policy |

These names describe proposed artifacts, not files already implemented. A release builder constructs the solver view from a positive allowlist. It does not copy the private package and attempt to delete sensitive filenames afterward. Hash and inspect the actual image and mounted artifacts; cleaning only the checkout misses image-layer and installed-package leakage.

### 10.2 Minimal component interfaces

Expose a Python library and a thin CLI over the same operations: `construct(candidate)`, `qualify(task_version)`, `release(task_version)`, `run(task_version, policy, limits)`, `grade(task_version, submission, case_seed)`, and `audit(run_ids)`. Operations return artifact references, typed status, evidence and cost; none return a guessed success when a stage fails. A web service and UI are deferred.

Each factory job has a content-derived identity and a manifest of dependencies. Worker retries publish temporary output, then commit one immutable result atomically; duplicate completions cannot mint duplicate tasks or rewards. Artifact changes trigger dependent rebuilds. Keep bounded worker queues and backpressure so the GPU collector cannot create unbounded CPU grading or service-startup work. Log construction time, reset time, execution time and grading time separately.

All train/development/test partitions have separate retrieval indexes, authoring histories, caches and access policies where content could carry task information. Shared public dependency artifacts are permitted; task-specific source, oracle and rollout caches remain partitioned.

## 11. Training loop

### Model and framework selection

Use SkyRL–Harbor as the initial integration candidate, with FSDP training and vLLM rollouts as proposed backends. The official integration description establishes the agent-trajectory-to-trainer connection. The current repository also documents ongoing framework reorganization; therefore old example imports and defaults must not be assumed compatible with current releases. Select and pin a mutually compatible release pair, model/tokenizer revisions and backends through an executable smoke test. This design has not executed that recipe. The older integration documentation URL could not be fetched during this revision, so exact configuration details remain an implementation gate. [Official integration description](https://novasky-ai.notion.site/skyrl-harbor), [SkyRL repository](https://github.com/NovaSky-AI/SkyRL)

A concrete small-model feasibility candidate is [Qwen2.5-Coder-7B-Instruct](https://huggingface.co/Qwen/Qwen2.5-Coder-7B-Instruct). Treat it as a starting probe, not a claim that a 7B model is sufficient for substantial features. Check terminal-action competence, task success, memory use and throughput on training-only pilot tasks. If it cannot produce informative rollouts, use a stronger compatible checkpoint or a simpler genuine feature slice before freezing the experiment. Choose and document the checkpoint before the main comparison; all arms use identical initial weights.

Do not assume one-GPU feasibility from parameter count. Actual resource needs depend on context, sequence lengths, group size, optimizer state and rollout concurrency. Hardware and measured throughput must determine the configuration.

### Initial rollout and update policy

1. Sample training tasks using fixed repository/feature-family balancing.
2. Start four independent episodes per task from the same clean baseline, with recorded seeds.
3. Keep one behavior-policy version fixed for the whole batch.
4. Allow reading, terminal commands, editing, public testing and submission through a fixed harness.
5. Extract and externally grade the final source artifact.
6. Compute group-relative advantages and update only on sampled assistant tokens.
7. Synchronize the updated weights before collecting the next batch.
8. Periodically run the frozen development suite and audit model submissions.

Before collecting a batch, verify rollout workers report the intended policy version and run one fixed-input inference check. Keep the authoring model and its private context entirely outside the learner trajectory. Teacher-generated code is a source for SFT only when explicitly included in the corresponding arm; it is never silently inserted into an RL attempt.

Initial GRPO uses one update epoch per collected batch and a fixed clipping/KL configuration selected on development data. Four rollouts is an initial engineering setting, not an optimality claim. Record the loss normalization and effective valid group sizes. [GRPO mechanics](https://verl.readthedocs.io/en/latest/algo/grpo.html)

The initial reward is one exactly when all mandatory feature behavior and required preservation checks pass, with no grading-protocol violation; otherwise it is zero. Keep per-requirement diagnostics outside the training reward initially. Patch size, number of tests written, attractive explanations and completion claims earn no reward.

Uniform-reward groups have zero group-relative task advantage. Log all-zero/all-one fractions and count their cost. Do not solve sparse rewards by inventing success, unlimited resampling or silently showing only useful groups. First investigate wrong grading, malformed actions, excessive difficulty and student capability. Any curriculum, dense reward or dynamic sampling change becomes a separately logged experimental change.

As a provisional training-feasibility gate, allow at most 64 groups of four rollouts and require at least eight mixed-outcome groups spanning at least four distinct training tasks before a larger update experiment. These are a bounded engineering probe, not a universal threshold or evidence of generalization. Revise the cap from measured cost before the comparison is frozen, never after seeing final evaluation scores. Failure to meet it triggers diagnosis and a new documented pilot rather than an open-ended GPU run.

Use protocol SFT only if the student cannot interact meaningfully with the harness. Warmstart all training arms from the same training-only demonstrations and evaluate that shared checkpoint as the pre-RL baseline. If tool use is valid but every feature attempt fails, protocol SFT alone is not an established remedy.

### Correct token accounting

Apply policy loss, likelihood ratios and KL to sampled assistant tokens, including tool arguments. Mask prompt tokens, tool output, padding and inserted harness text. Store the exact generated token IDs, generation parameters, behavior-policy log probabilities and per-turn contexts. Compute training probabilities using the same conditional context that generated each action. Do not assume every turn can be losslessly flattened into a single sequence: chat templates or replaced history can break the prefix relation. Test this on real multi-turn traces; retokenization or chat-template differences can corrupt training. [Multi-turn masking](https://verl.readthedocs.io/en/latest/sglang_multiturn/multiturn.html)

Keep context limits and any compaction policy identical across arms. Disable automatic summarization for the initial short-to-medium pilot; terminate cleanly within declared limits and grade the available artifact. Add longer tasks or a fixed compaction policy as a new, shared configuration after they are validated. Never truncate a collected trajectory silently while pretending it is the original rollout.

### Failure classification

| Outcome | Treatment |
| --- | --- |
| Wrong implementation or invalid command | Valid agent outcome; grade final artifact |
| Declared action/token/time limit exhausted | Terminate, grade final artifact; zero unless it actually satisfies the full contract |
| Agent starts a nonterminating process | Agent-caused outcome, not a platform outage |
| Container fails before work starts; verified platform outage | Retry under a fixed, arm-blind bound; record unresolved infrastructure failure |
| Candidate build/import failure, malformed response, worker exit or candidate execution limit | Valid failure with zero reward; the candidate cannot remove itself from the denominator |
| Demonstrated controller/platform defect prevents a probe from executing | Invalid measurement; quarantine or repair the task version |
| Unexpected token/context serialization corruption | Invalid trajectory; investigate rather than relabeling as a model failure |

Inspect the pinned integration's timeout and group-masking behavior and implement an explicit stop-status adapter; do not inherit unknown defaults. The official integration discussion identifies error classification and changed agent histories as important training issues. A valid agent failure must not disappear because it is inconvenient. An unresolved infrastructure error must not become an artificial zero in its peers' GRPO baseline. Permit at most two retries after the initial infrastructure-failed attempt, recording all attempts; if unresolved, exclude that invalid trajectory from optimization and report the effective group and lost work. Groups with fewer than two valid outcomes receive no group-relative update. [Integration error and trajectory considerations](https://novasky-ai.notion.site/skyrl-harbor)

If infrastructure fails after a source artifact is saved, retry grading that same artifact with the same cases; do not resample the policy to obtain a better answer. Recollect a rollout only when its trace or final state was genuinely lost, using the same frozen behavior policy and reporting the replacement. Confirm infrastructure defects using supervisor evidence or control executions. A candidate exhausting declared resources or breaking its own imports is not an infrastructure defect. Repeated failures correlated with a policy or task family trigger investigation of selection bias before further comparisons.

Training verification includes nonzero gradients when expected, optimizer steps, changed trainable tensors, saved/reloaded weights, consistent inference after reload, and checkpoint/resume reproduction. A task runner alone is not a completed RL system.

## 12. Evaluation that can support the claim

### Partitions

Assign repository families and feature lineages before generation. Group forks, related monorepo packages, duplicated code, backports, linked requests and generated descendants. Deduplicate against both the chosen external training baseline and evaluation corpus. Split candidate sources before using their text to tune a generator.

Use train for task generation and updates; development for checkpoint and hyperparameter selection; locked test for final comparisons. Independent test contracts and oracles should be authored through a separate process, without optimizing them to the proposed factory's filters. Apply a common minimum validity standard to all evaluated tasks.

Define the test-source sampling frame before adapter-based filtering: eligible repositories, feature families, date range and supported interfaces. Record every exclusion, including dependencies, unsupported object semantics and reconstruction failure. Results generalize to the reported eligible population, not automatically to all software feature requests.

Maintain separately reported temporal transfer on new requests in familiar repositories. That is different from withheld-repository transfer. Public data may have appeared in foundation-model pretraining: the defensible initial wording is excluded from our training pipeline. Record known dates and exposure uncertainty. A fresh or permissioned private test subset strengthens the claim if available.

### Experimental arms

| Arm | Purpose |
| --- | --- |
| A: starting checkpoint, no new updates | Baseline capability |
| B: SFT on verified training-only demonstrations | Whether a simpler training method captures the benefit |
| C: RL on a credible existing repository-task corpus | Main external-data baseline |
| D: RL on this factory's qualified feature tasks | Main proposed system |
| E: later controlled ablation | Isolate extra qualification or curriculum benefits |

SWE-Bench++ or a suitable real-task corpus is a closer primary baseline than only synthetic bug repair; verify dataset availability, licensing, overlap and runnable coverage before choosing C. SWE-smith can be an additional synthetic-data comparison. Equal-count comparisons alone are insufficient because task lengths differ. [SWE-smith](https://swesmith.com/)

Assemble a runnable baseline pilot before funding the main comparison. Existing corpus tests may import candidate code and therefore require adapters too. C and D share the same submission policy, supported interfaces, minimum validity checks and external grading boundary. Publish each corpus's adaptation/rejection funnel and feature/difficulty mix; count all incurred conversion work. If a credible baseline cannot be made runnable without an unrepresentative selection, revise the comparison or narrow the claim before training. A no-training comparison alone cannot demonstrate superiority to an existing task supply.

For all learned arms, keep the starting checkpoint, tools, harness, action format, optimizer family and evaluation limits fixed. Give the baselines a reasonable, reported development-tuning budget. If common protocol SFT is necessary, A becomes that shared warmstarted checkpoint and B denotes additional supervised training.

The task authoring model may differ from the student, but its version is frozen during dataset construction and its full cost is reported. SFT demonstrations must come exclusively from the assigned training partition and pass the same declared validity standard. Use the same student initialization for C and D and identical RL machinery; changing both the task supply and the agent harness would confound the main comparison.

C versus D estimates a difference between task supplies; it does not isolate which quality-control component caused it. To test stronger qualification, use the same candidate pool with a common basic validity floor, then compare ordinary qualification against the additional controls. Distinguish filtering effects from changed reward definitions. Do not train a deliberately broken reward baseline merely to win an ablation.

### Budget and metrics

Run two explicitly named comparisons:

- Learning comparison: matched RL training-resource budget, plus a separately reported task-construction bill.
- End-to-end efficiency comparison: matched total data-construction and training budget, keeping the evaluation inference budget fixed.

In the second comparison, a cheaper baseline may spend its savings on more training data or updates within the same declared rules. If insufficient resources prevent this comparison, make only the narrower learning claim. Do not call equal GPU-hours equal all-in cost when data curation differs.

Use costs actually incurred by someone adopting each accessible alternative: acquisition/licensing where applicable, ingestion, adaptation, qualification, training and the common evaluation. Do not charge an available corpus's hypothetical original research expense to the baseline; it is a sunk cost. Show optional reuse/amortization scenarios separately and apply them consistently.

Primary metric: paired difference in complete-feature resolution on withheld repository families. Report both task-weighted and family-weighted results. Apply the same test roster and fixed per-task inference limits to every model; report realized inference cost alongside success. If costs differ substantially, show the success–cost curve rather than claiming equal realized cost.

Define the unit as one complete feature task. A primary pass@1 trial allows one bounded agent episode per task; internal editing and public testing are allowed, but no best-of-k selection or hidden-verifier-guided retries. If multiple policy seeds are evaluated, pair the task/case sets and report the mean and uncertainty instead of relabeling any-success-over-seeds as pass@1. Publish attempted, resolved, failed and infrastructure-invalid counts for each arm. Preregister a common, arm-blind handling rule for unresolved infrastructure-invalid test instances and show their impact on reported rates.

Supporting metrics: construction yield at every stage; human minutes and compute per usable environment; reset/build failure rates; time per validated rollout; false acceptance/rejection audit estimates; regression rate; informative GRPO-group fraction; and solve rate over all assigned trials as well as valid trials. An infrastructure-heavy system must not look better merely because more failures were excluded.

Use repeated evaluation trials and multiple training seeds where feasible. Estimate uncertainty with repository-family clustering and training variability. A handful of repositories cannot provide a stable broad-transfer estimate regardless of how many tasks are extracted from each. Choose main-study size from pilot variance, clustering and the smallest effect worth the measured cost; freeze stopping and checkpoint selection before final testing. [RL evaluation uncertainty](https://arxiv.org/abs/2108.13264)

Public benchmarks may be secondary comparability checks, with overlap and exposure caveats. Do not use their leaderboard performance to tune the locked feature test set.

## 13. Cost accounting and task selection

Maintain separate ledgers for discovery, model-based authoring, rejected candidates, environment repair, reference and alternate solutions, verifier construction, human review, rollout inference, optimizer compute, CPU/service execution, storage and evaluation. Count retries and idle allocated accelerators. Avoid double-counting CPU or memory already included in a provider's GPU price.

For planning: rollout count is approximately updates × tasks per batch × rollouts per task. Multiply by measured output tokens and environment time for workload estimates; repeated context prefills, service startup and synchronization require measured wall-clock costing.

Report one-time construction cost and amortized cost with the assumed number of uses. Do not hide substantial authoring work inside an unlabeled reusable asset.

At first, task selection is a fixed sampler balanced across repositories and feature families. After the static comparison is interpretable, consider a separate adaptive experiment: classify recurring failures on training/dev tasks, find related real requests, send them through the same gates, and compare against static sampling at the same total budget. Difficulty is model- and checkpoint-dependent; long trajectories, large patches and high failure rates are not rewards for the generator.

## 14. Staged validation milestones

| Milestone | Deliverable | Decision gate |
| --- | --- | --- |
| 1. One complete task | One historical request, B/H reconstruction, contract, clean environment, trusted probes, reference and negative controls | The reference passes, missing/incomplete behavior fails, and reset reproduces results |
| 2. Engineering pilot | Approximately 20 qualified tasks across Click, Marshmallow and RQ, with all rejected candidates and manual work counted | Repeated qualification/reset checks succeed and actual agent outputs receive a validity audit |
| 3. Training feasibility | Student calibration, one complete GRPO update, checkpoint save/reload, token-mask and stop-status checks | Useful within-group reward variation, correct updates, measured memory and throughput |
| 4. Expanded dataset | Broader repository families, independently reviewed dev/test tasks, source and complexity distributions | Enough breadth and statistical precision for the planned claim; frozen evaluation protocol |
| 5. Controlled training | Shared initialization, external-data baseline, factory-data training, cost and learning curves | Held-out results support or reject the stated learning hypothesis |
| 6. Optional adaptations | Feature deletion, synthetic variants, adaptive selection, longer tasks or added languages | Each is compared through a named ablation; original fixed baseline remains reproducible |

Twenty tasks are an engineering target, not a credible final training corpus or a generalization benchmark. If collecting even that many requires extensive bespoke work, the failure is informative: measure the bottleneck before building a larger platform.

The first milestone to implement is Click's command-suggestion candidate through the entire path. This validates request interpretation, source reconstruction, grading and reset before investing in broad scraping or a training cluster.

## 15. Review findings and resulting changes

The initial design received separate reviews for data construction, verifier/evaluation validity and RL integration. This revision adds an inline architecture review of the clarified request-to-task mechanism and its component boundaries. The resulting decisions include:

- Treat the human implementation as a positive control, not the authority defining unspecified requirements.
- Include false rejection and alternative valid implementations, not only mutation-based rejection tests.
- Avoid trusting tests executed in the same interpreter as submitted code.
- Treat model timeouts and infrastructure failures differently, overriding framework defaults where necessary.
- Start with fixed-data training so adaptive task generation cannot obscure the causal comparison.
- Keep matched optimizer cost and matched all-in cost as separate experimental claims.
- Treat pilot task counts as feasibility targets and require more independent repository families for transfer claims.
- Acknowledge public-data exposure and existing PR-to-environment prior work.
- Publish a feature-capability matrix and count unsupported semantics in the rejection report.
- Test malicious submissions separately from ordinary incorrect implementations, and keep hidden diagnostics out of live episodes.
- Specify audit sampling units and a bounded empirical learning-signal probe.
- Count candidate-caused build/protocol failures as failures, while reserving invalid measurements for demonstrated controller or platform defects.
- Require an operational external-corpus baseline and report its adaptation cost and selection bias.
- State the eligible evaluation population and sandbox boundary explicitly.
- Separate reusable runtime construction from per-feature task and checker generation.
- Specify the scenario schema, expected-answer provenance, bounded repair budget and concrete package outputs.
- Require an alternative positive implementation for every accepted pilot task.
- Use identical private grading cases within each GRPO group and across final evaluation arms.
- Retry grading the same saved artifact after infrastructure failure, rather than selecting a different policy answer.
- Inspect automatic framework artifact transfers and enforce an independently built solver-visible package.
- Treat framework versions and exact backend compatibility as an execution gate, not a promise based on an old example.

## 16. What a successful result would contain

A reproducible release would include the task-construction code, source/contract manifests, environment recipes, qualification reports, runnable permitted task assets, trained checkpoint or adapter, baseline configurations, cost ledger, independent evaluation results and representative failure analyses. Release rights and test-set protection determine which private or evaluation artifacts are shared and when.

The final result may show a capability improvement, only an infrastructure improvement, or no improvement. Each is reported according to evidence. A larger task count or a rising training reward alone does not establish that the project worked.

## 17. Component acceptance and failure review

This matrix names the execution evidence required before each component is trusted. It is a design checklist; none of these project-specific executions has been performed in this document revision.

| Component | Failure that would invalidate the result | Required evidence and response |
| --- | --- | --- |
| Intake and partitioning | Train/test share a fork, copied feature, backport or generated descendant | A lineage manifest and overlap scan run before authoring; reassign the whole related group or remove it |
| Source reconstruction | B already contains the feature, or H omits a prerequisite | Ancestry and diff review plus executed baseline/reference checks; reject uncertain histories |
| Requirement authoring | Hidden assertions impose behavior absent from the visible task | Bidirectional requirement-to-assertion mapping and pilot human review; disclose grounded clarification or reject |
| Interface adapter | The adapter implements the feature or loses essential semantics | Contract-to-observation review and real interface execution; add a faithful adapter or reject that feature class |
| Setup builder | Dependency repair silently changes behavior or imports the completed package | Fresh setup logs, resolved import locations, recorded neutral changes and artifact hashes; rebuild and requalify |
| Scenario/checker generator | Empty or shallow tests accept missing or partial features | Probe-completion ledger, no-op and requirement-specific semantic controls; repair the checker or reject |
| Oracle expectations | A flawed reference teaches the checker the wrong rule | Requirement-grounded expectations, reference disagreement review and alternative positives; no automatic reference-wins rule |
| Positive controls | Correct alternative solutions are rejected by implementation-specific checks | Independently authored alternative passes; separately estimate false rejection in audits |
| Reset and services | Files reset but queue/DB/process state survives | Fresh and interrupted-reset executions including service observations; quarantine contaminated runtime recipe |
| Isolation and submission | Candidate code reads answers, writes the reward or replaces grading code | Real submission-path adversarial controls, backend isolation checks and sanitized artifact inventory; block release on known failure |
| Task registry | A mutable checker changes the reward midway through training | Content-derived versions, atomic writes, immutable manifests and dependency invalidation; stop affected runs |
| Failure adapter | Agent timeouts vanish from training or outages become fake zero rewards | Injected agent and platform failure cases through the real runner; verify reward, status, denominator and retry behavior |
| Trajectory adapter | Tool outputs receive policy loss or log probabilities use a different context | Real multi-turn token/mask/log-probability checks; reject corrupted trajectories and fix the adapter |
| RL optimizer | Runner records rewards but weights never learn, or workers use stale weights | A controlled nonuniform-reward update, nonzero gradients, changed tensors, policy-version check and save/reload verification |
| Learning feasibility | Every group has identical reward or setup dominates cost | Bounded student calibration with difficulty and cost breakdown; fix grading/protocol or adjust model/task scope before scaling |
| Final evaluator | Test data influences generator/model selection or uses a weaker reward standard | Independent frozen task/case roster, access separation and preregistered checkpoint rule; freeze a new test set after leakage |
| Experimental comparison | More compute, easier tasks or omitted failures explain the gain | Matched comparisons, common inference limits, adaptation funnels, full denominators and clustered uncertainty |
| Quality audits | Only successes or suspicious cases are inspected | Weighted random samples of both acceptance outcomes and source rejections, plus separately labeled targeted reviews |

These gates deliberately distinguish known defects, which block release, from residual uncertainty, which requires measurement. Passing finite controls cannot establish a zero error rate for all possible features or patches. Report audit uncertainty and the supported task population with every capability claim.

## 18. Build order and decisions before expensive work

1. **One feature, complete path.** Recover one real request and B/H pair, write its contract, build the runtime, generate scenarios, qualify the checker, release its solver view, run a coding agent, and grade the returned source through the actual isolated path. Human intervention is allowed and recorded. This is the first proof that the whole pipeline is coherent.
2. **Turn the manual steps into the factory.** Keep the same artifact interfaces while automating requirement extraction, scenario/checker generation, setup inference and bounded repair. Evaluate against the hand-reviewed task and additional requests; preserve every rejection and cost.
3. **Twenty-task engineering pilot.** Include more than one feature family and the three proposed repository candidates if they qualify. Require the complete pilot controls, fresh/reset runs, and human review. A failed candidate repository can be replaced with a documented reason; there is no quota that overrides validity.
4. **Prove the RL connection.** Select a compatible open checkpoint based on pilot tool use, then complete a small update and reload its weights. Validate reward/status handling, token masks, current policy versions and informative groups. Freeze model/framework/backend revisions only after this works.
5. **Broaden and freeze the study.** Build more independent repository families, make the external-data baseline runnable, independently author evaluation tasks, and select dataset size and budgets from measured cost and variance. Freeze the construction policy, comparison arms, stopping criteria and test access before the main run.
6. **Train, audit and evaluate.** Run the frozen comparisons, audit both successful and failed patches, then evaluate the frozen checkpoints once under the declared final protocol. If a known reward defect is found, apply the quarantine/restart policy before reporting an uncontaminated result.

The remaining empirical decisions are specific: how reliably the chosen feature classes can be verified, how much human review is needed, which student/framework combination produces usable learning signal, what dataset breadth the budget supports, and whether the resulting training actually transfers. None can be established by a design document alone. The next implementation target is step 1, not a large crawler, distributed platform or GPU training run.
