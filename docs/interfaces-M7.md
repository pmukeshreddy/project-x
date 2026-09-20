# M7 training-core interface — first implementation slice

The training core and actual admission-bound AgentRunner are implemented. The complete native training service/launcher remains subsequent M7 work. GPU execution is unverified. `import feature_rl.training` does not import Torch, SkyRL or vLLM.

## Grouping and admission

`BalancedSampler(list[TaskSlot], seed=...)` round-robins repository family, then feature, then task with deterministic seeded ordering. `next_group(policy_version) -> GroupPlan` produces four distinct episode seeds and one private case seed. The group ID binds roster digest, seed, position and policy. `state_dict/load_state_dict` reject changed roster/seed/cursor. Only the training service may populate the roster after authoritative admission.

`TrainingDataGate(store=..., admit=..., grader_revision=...)` requires an explicit trusted callable `admit(ArtifactRef) -> TaskBundle`. It must invoke M5/M6's actual complete release/quarantine/revocation resolver; there is no default state-string check or provisional-task bypass. `admit_task` checks the returned task is the exact stored artifact and in TRAIN. A test callback is not a production admission.

`prepare_group(plan, records, contexts=..., expected_policy=PolicyConfig, vocab_size=..., max_seq_len=..., normalize_std=False) -> PreparedGroup` requires four actual M0 `RolloutRecord`s with unique run IDs and matching full policy configuration except the distinct seed. `contexts` has one tuple of actual prompt-token tuples per episode. Each measured trajectory needs its unique controller-owned M4 receipt, exact task/submission/disposition/reward/case seed/grader revision, identified tokenizer and exact M0 TokenTrace. Evidence command, revision, publication timestamp, artifact list and scope are checked against that receipt. Authentic pre-execution source-rejection zeros retain `source_inspection`; they are never relabeled as worker execution. Null peers must have INVALID or INFRASTRUCTURE disposition and an equally bound M4 receipt. Unmeasured candidate failures and bare infrastructure labels reject. An optional actual AgentRunner supplies selected controller receipts for pre-grading invalidation; no exclusion bypass exists. Invalid peers retain null rewards and original records/costs. `PreparedGroup.optimization_turns()` excludes invalid peers and skips groups with fewer than two valid outcomes; valid-only advantages are broadcast to each turn.

`group_advantages((reward0,...,reward3), normalize_std=False)` computes a mean over nonnull binary outcomes; optional std is population std with epsilon 1e-6. Singletons and uniform valid rewards have zero task advantage. `SignalGate.observe(group_id, task_id, rewards)` rejects duplicates, caps the probe at 64 groups and reports readiness after eight mixed groups across four tasks. It does not itself authenticate measurements; the eventual service must only pass admitted groups and retain original costs. The full budget/signal/update orchestration is not in this slice.

## Supervised targets and exact contexts

`prepare_supervised_source(task=..., submission=..., grade_ref=..., render_solution=..., encode_context=..., encode_target=..., max_seq_len=...)` admits the task, requires an exact successful M4 source receipt, reads its source archive with M3's bounded inert reader and invokes a **trusted fixed harness renderer**. That renderer receives controller-only task metadata and inert source and returns rendered context/completion pairs; never install a renderer from candidate code. The same renderer/tokenizer/template must be bound in the training configuration across arms. It must not leak private task fields into rendered context.

`tokenize_supervision` uses `encode_context(context)` and `encode_target(context+target)` and requires exact prefix equality. It creates deterministic assistant targets with no behavior logprobs, not an RL TokenTrace. This source-SFT path has no supplied executable renderer yet; the actual agent action protocol will provide it in the runner slice. Per-example source, grade, tokenizer and template provenance must be retained by the full training service.

`validate_trace` checks M0 schema, exact original prompt IDs, policy version, token vocabulary, finite nonpositive logprobs, nonempty assistant mask and sequence limit. Turns are trained separately; no final-conversation retokenization or unsafe prefix flattening occurs.

## Actual tensor updater and checkpoints

`TorchUpdater(model, learning_rate=..., kl_coefficient=.001, epsilon_low=.2, epsilon_high=.2, max_grad_norm=1., weight_decay=0., max_seq_len=4096, temperature=1.)` owns a real Torch AdamW optimizer and a frozen deep copy of the initial reference. It disables model dropout when computing probabilities. `update(list[CausalTurn], algorithm='sft'|'grpo') -> UpdateReceipt` performs one full-batch gradient update with token-mean normalization; SFT requires no behavior/advantage fields, GRPO requires both. All context is fed to the causal model; only explicit assistant targets enter loss/ratio/KL. GRPO uses the exact sampled behavior denominator and k3 reference KL; uniform task advantage may still produce KL gradients. Zero-gradient batches skip optimizer/weight decay. Nonfinite gradients reject; failures during optimizer mutation poison the updater until reload.

`UpdateReceipt` contains loss, gradient norm, assistant token count, actual before/after tensor digests and optimizer step count. It is a tensor diagnostic/update receipt, not an M0 experiment checkpoint or proof of feature learning.

`save_checkpoint(path, binding=..., progress=..., policy_version=...) -> manifest_sha256` creates a new private directory atomically containing hashed native tensor/optimizer/reference/Python/Torch/CUDA RNG state and finite-JSON metadata. `binding` must include immutable config/task/reference/model/tokenizer/template joins; `progress` must carry sampler/signal/budgets/consumed-task state supplied by the full service. `load_checkpoint(path, expected_digest=..., binding=...)` verifies bytes/settings/joins, validates model/optimizer copies, restores state and RNG, and rejects changed CUDA topology. These are trusted controller checkpoints; never load candidate checkpoint paths. No scheduler is used by this constant-learning-rate CPU updater.

`PolicyBarrier` requires a `PolicyStamp(version, weights, tokenizer, template)`, a fixed-input inference result and acknowledgments from every configured worker. Changing policy clears readiness. Loading identity from checkpoint also clears acknowledgments: workers must be freshly synchronized and probed. This is a control-channel validation primitive, not endpoint authentication or a claim that a local counter proves remote weights.

## Pinned SkyRL bridge

`feature_rl.training.skyrl_bridge.register_losses()` registers custom loss functions through actual `PolicyLossRegistry.register` and `sync_registries`; call with Ray initialized before building model workers. The GRPO loss uses sampled behavior probabilities and SUM reduction because SkyRL already normalizes advantages. The SFT loss uses normalized unit weights for the same reduction. Their actual Torch gradients are CPU-tested; distributed registry execution is unverified.

`SkyRLUpdateBridge(actual_constructed_RayPPOTrainer)` checks the supported fixed configuration. `initialize_sync()` invokes native `init_weight_sync_state` and awaited `dispatch.save_weights_for_sampler`. `update(UpdateRow[], algorithm=...)` builds exact per-turn GeneratorOutput, validates it, sleeps colocated inference, calls native `convert_to_training_input`, `fwd_logprobs_values_reward`, substitutes explicit valid-group advantages, calls `train_critic_and_policy`, and awaits weight synchronization. `group_rows` adapts `PreparedGroup`s without manufacturing invalid-peer placeholders. `convert_eligible_batch` invokes native conversion through a shallow trainer view with an independent deep-copied config using actual eligible UID count and one-prompt conversion boundaries. It coalesces those native boundaries to the configured policy/critic minibatch sizes, allowing a smaller final minibatch. The original trainer config, full assigned groups/costs and native tensor/padding behavior are preserved; no resampling occurs. The caller must enforce policy barrier, admission, group ownership and costs before this low-level update.

This bridge is usable by the upcoming launcher but has not been imported under the actual pinned CUDA stack. It does not construct the distributed experiment or implement native checkpoint publication/resume; those are explicit next-slice work. Stable source references and incompatibilities remain in `docs/evidence/M7/compatibility.md`. Stock Harbor Trial/ArtifactHandler is never invoked here.

## Actual agent runner checkpoint

```python
from feature_rl.agents import (
    AgentRunner, PolicyBackend, SkyRLTokenBackend, Completion, HARNESS,
    RunRecoveryRequired, RunSubmissionPending, RunGradePending,
    RunFreezePending, RunPublicationFailed,
)

runner = AgentRunner(
    store=controller_store, registry=registry,
    lifecycle=released_task_resolver,  # exactly TaskLifecycle or ReleasedTaskResolver
    builder=actual_builder,           # exact construction revision of supported T0
    runtime=actual_m3_runtime, grader=actual_m4_grader,
    backend=trusted_policy_backend, revision=exact_runner_revision,
    owner="feature_rl.agents", evidence_scope="real_integration",
)
result = runner.run(task_ref, policy, limits, case_seed=frozen_case_seed,
                    invocation=stable_assigned_trial_id)
```

`run(task, policy, limits, *, case_seed=None, invocation=None) -> OperationResult`. Omitted case_seed defaults to policy.seed only for ordinary runs. GRPO/evaluation supply the frozen case seed. Omitted invocation gets a new UUID; explicit identical invocation/input/configuration replays a completed result without generation, worker execution or grading. Existing unfinished jobs raise recovery, never implicit retry. `result.artifacts` contains exactly one RolloutRecord and one `m7-frozen-run`. The record's `run_id` is its actual selected Registry run job ID; no second index exists. `runner.validate_record(record)` validates the selected job/attempt/request/record/controller outcome and quarantine. Current task admission still uses the concrete resolver.

Normal collection invokes M4 once after closing the workspace and publishing source-only submission. At most two additional grades are allowed only for confirmed infrastructure outcomes, always the same submission and seed. `record.grading_evidence` contains one selected terminal M4 evidence record and the runner controller evidence; earlier infra receipts remain in the controller outcome and Registry phase accounting. Evaluators consume that grade, not another routine call. A pre-grading invalid/infrastructure outcome has a bound runner controller receipt and no manufactured M4 grade.

`recover(claim)` selects durably frozen work or reports unknown work requiring reconciliation. `retry_publication(pending)` handles the exact retained submission, M4 grade, controller outcome or frozen run capabilities. It never calls the policy again. Keep pending objects and Registry claim capabilities controller-private; a process lost before a frozen outcome does not authorize automatic sampling replacement.

`TrainingDataGate(..., runner=actual_same_store_AgentRunner)` additionally validates selected run identity for each record and accepts authenticated runner null outcomes. Without runner, the existing M4-only rule still applies. Its authoritative admission callable in the training service must be the actual resolver's bound method. No production release-state or arbitrary outcome-label bypass is provided.

`SkyRLTokenBackend(*, tokenizer_directory: Path, tokenizer_sha256: str, endpoint: str, model_name: str, barrier: PolicyBarrier, max_seq_len: int, response_bytes=4194304)` loads only a trusted local tokenizer with `local_files_only=True`, `trust_remote_code=False`. Its SHA-256 is over canonical JSON mapping every regular relative file path in the bounded dedicated tokenizer bundle to its raw file SHA-256. Model weights should not be placed in that tokenizer-only bundle. `template_digest` binds the actual string chat template. Native policy provider must be `skyrl`, model name must match, weights must be identified, harness must equal `HARNESS == 'feature-rl-source-v1'`, and the barrier must acknowledge the exact version/weight/tokenizer/template stamp. The upcoming native service owns real synchronization/probes; constructing a barrier is not proof of remote weights.

The native token endpoint is pinned-source-compatible but unexecuted locally. Required training probabilities permit only temperature 1/top_p 1; top_k is explicitly -1. Other sampling options can be used only without a training trace. `Completion` keeps exact context IDs, sampled IDs, optional actual behavior probabilities, policy version, decoded action text and native finish reason. No greedy/raw-score substitution is accepted as sampled behavior.

Runner CPU/record limits and the sampled aggregate CPU boundary are detailed in `docs/reports/M7-runner.md`. The single retained Docker check uses an explicitly non-model, nontraining one-token scripted backend to respect the unchanged fixture contract; it is not evidence for native token sampling or task admission. The full native SFT/GRPO service remains subsequent work.
