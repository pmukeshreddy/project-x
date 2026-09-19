# Exact pinned source excerpts

Full raw text and SHA-256 mapping are in the ignored research directory and `source-inventory.jsonl`. These excerpts are evidence, not executed code.

## skyrl/examples/train_integrations/harbor/harbor_generator.py

[Exact source](https://raw.githubusercontent.com/NovaSky-AI/SkyRL/f5bc3b78dfddfb352870d5d7430cd226e5785838/examples/train_integrations/harbor/harbor_generator.py) · SHA-256 `e4be4ebdb884d92dbee46417bd047c04cab60fe716490121e93417ed89dd80a6`

```text
  32 # We have N retries for each trial, if one of the rollout (out of n_samples_per_prompt) fails
  33 # after N attemptes, we skip this prompt altogether.
  34 MAX_NUM_RETRIES_PER_TRIAL = 2
```

```text
  71     # 1. Identify failed instances. If any rollout for prompt P failed, mask all rollouts for P (conservative).
  72     timeout_instance_ids = set()
  73     error_instance_ids = set()
  74     all_instance_ids = set()
  75     num_timeout_trajectories = 0
  76     num_error_trajectories = 0
  77     for traj in trajectory_outputs:
  78         instance_id = traj.trajectory_id.instance_id
  79         all_instance_ids.add(instance_id)
  80         if traj.stop_reason == "agent_timeout":
  81             num_timeout_trajectories += 1
  82             timeout_instance_ids.add(instance_id)
  83         elif traj.stop_reason == "error" or traj.rollout_details is None:
  84             num_error_trajectories += 1
  85             error_instance_ids.add(instance_id)
```

```text
 110         # 2.1. For failed trajectories, set loss mask to [0] and stop reason to "error".
 111         if tid.instance_id in masked_instance_ids:
 112             prompt_token_ids.append([0])
 113             response_ids.append([0])
 114             rewards.append(0.0)
 115             loss_masks.append([0])
 116             stop_reasons.append("error")
 117             is_last_step_list.append(True)
 118             out_trajectory_ids.append(tid)
 119             rollout_logprobs_list.append([0.0])
 120             out_trajectory_generation_times.append(traj.e2e_time)
```

```text
 129         assert len(traj.rollout_details) == 1, f"Expected exactly one rollout segment, got {len(traj.rollout_details)}."
 130         rollout_detail = traj.rollout_details[0]
 131         prompt_token_ids_per_turn = rollout_detail["prompt_token_ids"]
 132         completion_token_ids_per_turn = rollout_detail["completion_token_ids"]
 133         logprobs_per_turn = rollout_detail["logprobs"]
 134         n_turns = len(completion_token_ids_per_turn)
 135         assert len(prompt_token_ids_per_turn) == n_turns and len(logprobs_per_turn) == n_turns, (
 136             f"Malformed rollout_details (prompts={len(prompt_token_ids_per_turn)}, completions={n_turns}, "
 137             f"logprobs={len(logprobs_per_turn)})."
 138         )
 139 
 140         # 2.4. Emit one entry per step, following SkyRL's step-wise convention.
 141         for t in range(n_turns):
 142             comp_ids = completion_token_ids_per_turn[t]
 143             p_ids = prompt_token_ids_per_turn[t]
 144             lp = logprobs_per_turn[t]
 145             assert len(lp) == len(comp_ids), "logprobs and completion token ids must have the same length."
 146 
 147             # Record actual reward in last turn, and zeros for all other turns.
 148             is_last = t == n_turns - 1
 149             reward = traj.reward if is_last else 0.0
 150 
 151             # Loss mask.
 152             step_loss_mask = [1] * len(comp_ids)
```

```text
 306     async def generate(self, input_batch: GeneratorInput, disable_tqdm: bool = False) -> GeneratorOutput:
 307         prompts = input_batch["prompts"]
 308         trajectory_ids = input_batch["trajectory_ids"]
 309 
 310         if trajectory_ids is None:
 311             raise ValueError("`trajectory_ids` is required in the input batch")
 312         if len(prompts) != len(trajectory_ids):
 313             raise ValueError(
 314                 f"Prompt count ({len(prompts)}) doesn't match trajectory_ids count ({len(trajectory_ids)})"
 315             )
 316 
 317         # Captured once so every trajectory shares the policy version at the start of the batch.
 318         cache_salt = self._compute_cache_salt()
```

```text
 363         for i in range(MAX_NUM_RETRIES_PER_TRIAL):
 364             prefix = f"Trajectory {trajectory_id} attempt {i+1}/{MAX_NUM_RETRIES_PER_TRIAL}"
 365             results = None
 366             # Each attempt is a distinct router session; track it so it can be
 367             # released on completion/error/cancellation.
 368             session_id = uuid4().hex
 369             try:
 370                 # Create a fresh Trial each attempt so agent state is clean on retry.
 371                 config = deepcopy(self._harbor_trial_config_template)
 372                 config["task"] = {"path": prompt}
 373                 config["agent"]["kwargs"]["session_id"] = session_id
 374                 # Forward the salt via llm_kwargs.extra_body -> LiteLLM -> the vLLM request's top-level
 375                 # `cache_salt` field. vLLM rejects an empty salt, so attach only when set.
 376                 if cache_salt is not None:
 377                     llm_kwargs = config["agent"]["kwargs"].setdefault("llm_kwargs", {})
 378                     extra_body = llm_kwargs.setdefault("extra_body", {})
 379                     if not isinstance(extra_body, dict):
 380                         raise TypeError("harbor_trial_config.agent.kwargs.llm_kwargs.extra_body must be a mapping")
 381                     extra_body["cache_salt"] = cache_salt
 382                 trial_config = TrialConfig.model_validate(config)
 383                 trial = await Trial.create(trial_config)
 384 
 385                 async with self._rate_limiter:
 386                     results = await trial.run()
 387 
```

```text
 391                 is_agent_timeout_error = exc_type == "AgentTimeoutError"
 392 
 393                 # Determine reward.
 394                 if is_agent_timeout_error:
 395                     # AgentTimeoutError: not successful, no retry, loss-masked
 396                     logger.debug(f"{prefix} hit AgentTimeoutError (no retry). Results: {results}")
 397                     break
 398                 elif is_context_length_error:
 399                     # ContextLengthExceededError: always train with reward=0.
 400                     logger.debug(f"{prefix} hit ContextLengthExceededError, setting reward=0. Results: {results}")
 401                     reward = 0.0
 402                 elif not results.verifier_result:
 403                     # Does not have a verifier result, so it's not successful, will retry
 404                     logger.warning(f"{prefix} failed: Exception info: {results.exception_info}. Results: {results}")
 405                     continue
 406                 else:
 407                     reward = float(results.verifier_result.rewards["reward"])
 408 
 409                 # Extract rollout details and check for success
 410                 rollout_details = results.agent_result.rollout_details
 411                 num_turns = results.agent_result.metadata["n_episodes"]
 412 
 413                 if (
 414                     rollout_details
 415                     and len(rollout_details) >= 1
 416                     and len(rollout_details[0].get("completion_token_ids", [])) > 0
```

## harbor/src/harbor/trial/artifact_handler.py

[Exact source](https://raw.githubusercontent.com/harbor-framework/harbor/3de07a0e01f3368921766437fc7afece3ddec23d/src/harbor/trial/artifact_handler.py) · SHA-256 `4be3e191f93cd5d615fb2956ebc9c8c0becd3c74c7ca3d18d3ff727a87034d79`

```text
  94             target_source = self._upload_target_source(
  95                 artifact.source,
  96                 source_convention=source_convention,
  97                 target_convention=target_convention,
  98             )
  99             if host_path.is_dir():
 100                 await target_env.empty_dirs([target_source], chmod=True)
 101                 await target_env.upload_dir(
 102                     source_dir=host_path,
 103                     target_dir=target_source,
 104                 )
 105                 continue
 106 
 107             await target_env.upload_file(
 108                 source_path=host_path,
 109                 target_path=target_source,
```

```text
 112     def _normalized_artifacts(
 113         self,
 114         artifacts: Sequence[str | ArtifactConfig] | None,
 115         convention_source: str,
 116     ) -> list[ArtifactConfig]:
 117         artifact_values: list[str | ArtifactConfig] = [
 118             *self.artifacts,
 119             *(artifacts or []),
 120         ]
 121         normalized = [
 122             ArtifactConfig(source=artifact) if isinstance(artifact, str) else artifact
 123             for artifact in artifact_values
 124         ]
 125 
 126         if not self._has_artifact_source(normalized, convention_source):
 127             normalized.insert(
 128                 0,
 129                 ArtifactConfig(
 130                     source=convention_source,
 131                     destination=convention_source,
 132                 ),
 133             )
 134         return normalized
```

```text
 249         destination = artifact.destination or PurePosixPath(artifact.source).name
 250         return artifacts_dir / self._relative_host_destination(destination)
 251 
 252     @staticmethod
 253     def _relative_host_destination(destination: str) -> Path:
 254         destination_path = PurePosixPath(destination)
 255         parts = [part for part in destination_path.parts if part not in ("", "/")]
 256         return Path(*parts) if parts else Path(".")
```

```text
 265     def _upload_target_source(
 266         self,
 267         source: str,
 268         *,
 269         source_convention: str,
 270         target_convention: str,
 271     ) -> str:
 272         if self._is_environment_artifacts_dir(source, source_convention):
 273             return target_convention
 274         return source
```

## harbor/src/harbor/models/trial/paths.py

[Exact source](https://raw.githubusercontent.com/harbor-framework/harbor/3de07a0e01f3368921766437fc7afece3ddec23d/src/harbor/models/trial/paths.py) · SHA-256 `39441cc9380fee3f5f0c563ebe3df1ee97c11724e0eb52fb6c56e509774e1c0c`

```text
  35     logs_dir: PurePosixPath = PurePosixPath("/logs")
  36     agent_dir: PurePosixPath = logs_dir / "agent"
  37     verifier_dir: PurePosixPath = logs_dir / "verifier"
  38     artifacts_dir: PurePosixPath = logs_dir / "artifacts"
```

## harbor/src/harbor/trial/trial.py

[Exact source](https://raw.githubusercontent.com/harbor-framework/harbor/3de07a0e01f3368921766437fc7afece3ddec23d/src/harbor/trial/trial.py) · SHA-256 `413b0e8d8d91d76c6382a05ac4ba18d64b98d42d15ec60353cd690a26662ec99`

```text
 483                 await self._artifact_handler.upload_artifacts(
 484                     target_env,
 485                     artifacts_dir=artifacts_dir,
 486                     source_artifacts_dir=self.agent_env_paths.artifacts_dir,
 487                     target_artifacts_dir=env_paths.artifacts_dir,
 488                     artifacts=artifacts,
 489                 )
 490 
 491                 verifier = VerifierFactory.create_verifier_from_config(
 492                     self.config.verifier,
 493                     task=self.task,
 494                     trial_paths=self.paths,
 495                     environment=target_env,
 496                     override_env=self.config.verifier.env or None,
 497                     logger=self.logger,
 498                     verifier_env=env,
 499                     step_name=step_cfg.name if step_cfg is not None else None,
 500                     skip_tests_upload=True,
 501                 )
```

```text
 575     ) -> Path:
 576         if step_cfg is not None:
 577             step_tests_dir = self.task.paths.step_tests_dir(step_cfg.name)
 578             if step_tests_dir.exists():
 579                 return step_tests_dir
 580         return self.task.paths.tests_dir
```

```text
 675     def _init_artifact_handler(self) -> None:
 676         self._artifact_handler = ArtifactHandler(
 677             artifacts=[*self.task.config.artifacts, *self.config.artifacts],
 678             logger=self.logger,
 679         )
 680 
```

## skyrl/skyrl/train/generators/base.py

[Exact source](https://raw.githubusercontent.com/NovaSky-AI/SkyRL/f5bc3b78dfddfb352870d5d7430cd226e5785838/skyrl/train/generators/base.py) · SHA-256 `1ee8341b4a78ac23e91e8fbd344e498f59de60172bc3c5f9f7be6ad82222da04`

```text
  12 @dataclass
  13 class TrajectoryID:
  14     instance_id: str  # Unique identifier for the instance in the dataset
  15     repetition_id: int  # Which sample/repetition for this UID (0, 1, 2... for GRPO)
  16 
  17     def to_string(self) -> str:
  18         return f"{self.instance_id}_{self.repetition_id}"
  19 
  20 
  21 @dataclass
  22 class BatchMetadata:
  23     global_step: int
  24     training_phase: TrainingPhase
  25 
  26 
  27 class GeneratorInput(TypedDict):
  28     prompts: List[ConversationType]
  29     env_classes: List[str]
  30     env_extras: Optional[List[Dict[str, Any]]]
  31     sampling_params: Optional[Dict[str, Any]]
  32     trajectory_ids: Optional[List[TrajectoryID]]
  33     batch_metadata: Optional[BatchMetadata]
  34 
  35 
  36 class GeneratorOutput(TypedDict):
  37     prompt_token_ids: List[List[int]]
  38     response_ids: List[List[int]]
  39     rewards: Union[List[float], List[List[float]]]
  40     loss_masks: List[List[int]]
  41     stop_reasons: Optional[List[str]]
  42     rollout_metrics: Optional[Dict[str, Any]]
  43     rollout_logprobs: Optional[List[List[float]]]
  44     trajectory_ids: Optional[List[TrajectoryID]]
  45     # Wall-clock generation time (seconds) for each trajectory, with one entry per
  46     # trajectory in the input batch (i.e. per ``agent_loop`` call). Used by the fully
  47     # async trainer to compute per-group / intra-group completion-time metrics.
  48     trajectory_generation_times: Optional[List[float]]
  49     rollout_expert_indices: Optional[List[List[List[List[int]]]]]  # [batch_size, seq_len, layer_num, topk]
```

## harbor/src/harbor/llms/lite_llm.py

[Exact source](https://raw.githubusercontent.com/harbor-framework/harbor/3de07a0e01f3368921766437fc7afece3ddec23d/src/harbor/llms/lite_llm.py) · SHA-256 `7b35119873f5421cef5d8c958e8b738f35edf1b6a0b1cbc9610948799b47da71`

```text
 310             # Add logprobs and return_token_ids if rollout details collection is enabled
 311             if self._collect_rollout_details:
 312                 completion_kwargs["logprobs"] = True
 313                 # Request token IDs from provider (supported by vLLM)
 314                 # Note: Some providers (e.g., OpenAI) will reject this parameter, but we'll catch and retry without it
 315                 if "extra_body" not in completion_kwargs:
 316                     completion_kwargs["extra_body"] = {}
 317                 extra_body: dict[str, Any] = completion_kwargs["extra_body"]  # type: ignore[assignment]
 318                 extra_body["return_token_ids"] = True
```

```text
 384                             f"Provider {self._model_name} rejected extra_body parameters: {', '.join(rejected_params)}. "
 385                             f"Retrying without them. Token IDs will not be available."
 386                         )
 387                         extra_body_val = completion_kwargs.get("extra_body")
 388                         if extra_body_val and isinstance(extra_body_val, dict):
 389                             extra_body: dict[str, Any] = extra_body_val
 390                             for param in rejected_params:
 391                                 if param in extra_body:
 392                                     del extra_body[param]
 393 
 394                             if not extra_body:
 395                                 del completion_kwargs["extra_body"]
 396 
 397                         response = await litellm.acompletion(**completion_kwargs)
```

## skyrl/skyrl/train/dataset/preprocess.py

[Exact source](https://raw.githubusercontent.com/NovaSky-AI/SkyRL/f5bc3b78dfddfb352870d5d7430cd226e5785838/skyrl/train/dataset/preprocess.py) · SHA-256 `9f7667890b9d789e6cc9875dca666627f493ceb3db55fc97599c244c6f25e205`

```text
 110     if max_seq_len is not None and max_total > max_seq_len:
 111         logger.warning(
 112             f"Max sequence length in batch ({max_total}) exceeds max_seq_len ({max_seq_len}). "
 113             f"No truncation is performed; consider checking generator settings."
 114         )
 115 
```

## skyrl/skyrl/backends/skyrl_train/utils/ppo_utils.py

[Exact source](https://raw.githubusercontent.com/NovaSky-AI/SkyRL/f5bc3b78dfddfb352870d5d7430cd226e5785838/skyrl/backends/skyrl_train/utils/ppo_utils.py) · SHA-256 `0d7ae59af59b16cc3d0b6593251bd3ab5dbfd421d9e3ae1bfde00783ba6323d7`

```text
 564     assert config.policy_loss_type in ["regular", "dual_clip"], "loss_type must be either 'regular' or 'dual_clip'"
 565 
 566     ratio = safe_exp_delta(log_probs - old_log_probs, clip=20.0, out_dtype=log_probs.dtype)
 567     surr1 = ratio * advantages
 568     surr2 = ratio.clamp(1 - config.eps_clip_low, 1 + config.eps_clip_high) * advantages
 569     loss = -torch.min(surr1, surr2)
 570     clip_ratio = masked_mean((-surr2 > -surr1).float(), loss_mask).mean().detach().item()
 571     clip_pg_losses1 = loss
```

```text
 789     assert rollout_logprobs is not None, "rollout_logprobs are required for rollout_is"
 790 
 791     ratio = safe_exp_delta(log_probs - rollout_logprobs, clip=20.0, out_dtype=log_probs.dtype)
 792 
 793     in_range = (ratio > 1 - config.eps_clip_low) & (ratio < 1 + config.eps_clip_high)
 794     calibrated_ratio = torch.where(in_range, ratio, torch.zeros_like(ratio))
 795 
 796     loss = -(calibrated_ratio.detach() * advantages * log_probs)
 797     clip_ratio = masked_mean((~in_range).float(), loss_mask).mean().detach().item()
 798 
 799     loss_metrics: dict[str, float] = {"clip_ratio": clip_ratio}
 800     loss, loss_mask, off_policy_metrics = apply_off_policy_correction(
 801         loss, old_log_probs, rollout_logprobs, loss_mask, config.off_policy_correction
```

```text
1316     with torch.no_grad():
1317         bsz = scores.shape[0]
1318         for i in range(bsz):
1319             id2score[index[i]].append(scores[i])
1320         for idx in id2score:
1321             if len(id2score[idx]) == 1:
1322                 id2mean[idx] = torch.tensor(0.0)
1323                 id2std[idx] = torch.tensor(1.0)
1324             elif len(id2score[idx]) > 1:
1325                 id2mean[idx] = torch.mean(torch.tensor(id2score[idx]))
1326                 id2std[idx] = torch.std(torch.tensor([id2score[idx]]))
1327             else:
1328                 raise ValueError(f"no score in prompt index: {idx}")
1329         for i in range(bsz):
1330             if grpo_norm_by_std:
1331                 scores[i] = (scores[i] - id2mean[index[i]]) / (id2std[index[i]] + epsilon)
1332             else:
1333                 scores[i] = scores[i] - id2mean[index[i]]
1334         scores = scores.unsqueeze(-1) * response_mask
1335 
1336     return scores, scores
1337 
```

## skyrl/skyrl/backends/skyrl_train/workers/worker_dispatch.py

[Exact source](https://raw.githubusercontent.com/NovaSky-AI/SkyRL/f5bc3b78dfddfb352870d5d7430cd226e5785838/skyrl/backends/skyrl_train/workers/worker_dispatch.py) · SHA-256 `e2da7e5d351699b2e38f2d326b905af2b7bedf0c04e6799c3eed260db6ca45aa`

```text
 641                 # Non-colocated single tenant: pause generation to prevent in-flight requests from
 642                 # reading partially-updated weights during the NCCL broadcast.
 643                 await self._inference_engine_client.pause_generation()
 644                 try:
 645                     self._broadcast_to_inference_engines(self._inference_engine_client, model_id=model_id)
 646                     self._finish_weight_sync()
 647                 finally:
 648                     await self._inference_engine_client.resume_generation()
 649 
 650         # Advance the policy version so prefix-cache salting isolates blocks from the previous weights
 651         # (see `GeneratorConfig.use_cache_salt`).
 652         self._inference_engine_client.increment_weight_version()
```

## skyrl/skyrl/train/trainer.py

[Exact source](https://raw.githubusercontent.com/NovaSky-AI/SkyRL/f5bc3b78dfddfb352870d5d7430cd226e5785838/skyrl/train/trainer.py) · SHA-256 `73c584dc6ce9a247b6708c1284aecbf38ffb0b1c85f7b1277e12679eefb4acf0`

```text
1144             #       (batch_size, seqlen):        per-step response mask
1145             last_step_response_mask = data["response_mask"][is_last_step]
1146             last_step_advantages, last_step_returns = ppo_utils.compute_advantages_and_returns(
1147                 token_level_rewards=token_level_rewards[is_last_step],
1148                 response_mask=torch.ones_like(last_step_response_mask, dtype=torch.float),
1149                 index=index[is_last_step.cpu().numpy()],
1150                 adv_estimator=self.cfg.trainer.algorithm.advantage_estimator,
1151                 values=values[is_last_step] if values is not None else None,
1152                 config=self.cfg.trainer.algorithm,
1153                 gamma=self.cfg.trainer.algorithm.gamma,
1154                 lambd=self.cfg.trainer.algorithm.lambd,
1155                 grpo_norm_by_std=self.cfg.trainer.algorithm.grpo_norm_by_std,
1156             )
1157             traj_ids = (
```

```text
1648         # Save dataloader state
1649         dataloader_save_path = os.path.join(global_step_folder, "data.pt")
1650         try:
1651             dataloader_state_dict = self.train_dataloader.state_dict()
1652             with io.open_file(dataloader_save_path, "wb") as f:
1653                 torch.save(dataloader_state_dict, f)
1654             logger.info(f"Saved dataloader state to {dataloader_save_path}")
1655         except Exception as e:
1656             logger.warning(f"Failed to save dataloader state: {e}")
1657 
1658         # Save additional trainer state
1659         trainer_state = {
1660             "global_step": self.global_step,
1661             "config": asdict(self.cfg),
1662         }
1663         trainer_state_path = os.path.join(global_step_folder, "trainer_state.pt")
1664         with io.open_file(trainer_state_path, "wb") as f:
1665             torch.save(trainer_state, f)
1666         logger.info(f"Saved trainer state to {trainer_state_path}")
1667 
1668         # Atomic tracking - write this last after all saves succeed
```

```text
1764         saved_global_step = trainer_state.get("global_step", global_step)
1765         logger.info("Successfully loaded trainer state")
1766         if saved_global_step != global_step:
1767             logger.warning(f"Global step mismatch: path={global_step}, saved={saved_global_step}. Using path value.")
1768 
1769         # 2. Load dataloader state if available
1770         if io.exists(dataloader_state_path):
1771             try:
1772                 with io.open_file(dataloader_state_path, "rb") as f:
1773                     dataloader_state = torch.load(f, map_location="cpu", weights_only=False)
1774                 self.train_dataloader.load_state_dict(dataloader_state)
1775                 logger.info("Successfully loaded dataloader state")
1776             except Exception as e:
1777                 logger.warning(f"Failed to load dataloader state: {e}. Dataloader will start from beginning.")
1778         else:
1779             logger.warning(
1780                 f"No dataloader state found at {dataloader_state_path}. Dataloader will start from beginning."
1781             )
1782 
1783         # 3. Load policy checkpoint (dispatch handles offload/backload)
1784         logger.info(f"Loading policy checkpoint from {policy_ckpt_dir}")
1785         self.dispatch.load_checkpoint(
1786             "policy",
1787             policy_ckpt_dir,
1788             load_optimizer_states=True,
1789             load_lr_scheduler_states=True,
```

## skyrl/pyproject.toml

[Exact source](https://raw.githubusercontent.com/NovaSky-AI/SkyRL/f5bc3b78dfddfb352870d5d7430cd226e5785838/pyproject.toml) · SHA-256 `60c7d573298b2b286bf735f188a5ca1845caa8b68a8aa4e105bf6f50fce8d5df`

```text
 106 fsdp = [
 107     "skyrl[skyrl-train]",
 108     "vllm==0.23.0; sys_platform == 'linux'",
 109     "vllm-router; sys_platform == 'linux'",
 110     # The `nixl` shim provides that namespace and dispatches to `nixl_cu12`.
 111     # `nixl-cu12` ships the `nixl_cu12` module, but vLLM imports `nixl._api`.
 112     # Its metadata hard-depends on `nixl-cu13` too; that variant is overridden
 113     # out below (it would drag in the CUDA-13 stack and break the cu12 torch pin).
 114     "nixl; sys_platform == 'linux'",
 115     "flash-linear-attention; sys_platform == 'linux'",
 116     "causal-conv1d; sys_platform == 'linux'",
 117     "flash-attn==2.8.3; sys_platform == 'linux'",
 118     "torch==2.11.0; sys_platform == 'linux'",
 119     "flashinfer-python==0.6.12; sys_platform == 'linux' and platform_machine == 'x86_64'",
```

```text
 282 name = "vllm-cu129"
 283 url = "https://wheels.vllm.ai/0.23.0/cu129"
 284 explicit = true
 285 
 286 [tool.uv.sources]
 287 # Match torch's CUDA variant (cu128).
 288 flashinfer-jit-cache = { index = "flashinfer-cu128", marker = "sys_platform == 'linux'" }
 289 # vllm 0.23.0's PyPI wheel needs CUDA 13 (libcudart.so.13); the cu129 wheel
 290 # links libcudart.so.12, which torch+cu128 supplies.
 291 vllm = [
 292     { index = "vllm-cu129", marker = "sys_platform == 'linux'" },
 293 ]
 294 # torch-2.11 wheels for these three aren't published upstream; built locally
 295 # on erictang000 forks (cu128 / cxx11abiTRUE / py3.12). flash-attn has its
 296 # +cu12torch2.11cxx11abiTRUE local-version stripped from METADATA — PEP 440
 297 # treats 2.8.3+local > 2.8.3, so TE's get_attention_backend (max_version=
 298 # "2.8.3") rejected it and silently fell back to broken unfused attention.
 299 flash-attn = { url = "https://github.com/erictang000/flash-attention/releases/download/v2.8.3-torch2.11-clean/flash_attn-2.8.3-cp312-cp312-linux_x86_64.whl", marker = "sys_platform == 'linux' and python_version == '3.12' and platform_machine == 'x86_64'" }
 300 causal-conv1d = { url = "https://github.com/erictang000/causal-conv1d/releases/download/v1.6.1.post4-torch2.11/causal_conv1d-1.6.1-cp312-cp312-linux_x86_64.whl", marker = "sys_platform == 'linux' and python_version == '3.12' and platform_machine == 'x86_64'" }
 301 mamba-ssm = { url = "https://github.com/erictang000/mamba/releases/download/v2.3.1-torch2.11/mamba_ssm-2.3.1-cp312-cp312-linux_x86_64.whl", marker = "sys_platform == 'linux' and python_version == '3.12' and platform_machine == 'x86_64'" }
 302 # transformer-engine-torch 2.11.0 built against torch 2.11 (cu128 / cp312).
 303 transformer-engine-torch = { url = "https://github.com/erictang000/TransformerEngine/releases/download/v2.11.0-torch2.11/transformer_engine_torch-2.11.0-cp312-cp312-linux_x86_64.whl", marker = "sys_platform == 'linux' and python_version == '3.12' and platform_machine == 'x86_64'" }
 304 # CUDA torch on Linux, CPU torch on macOS (must match skyrl-train).
 305 # Linux uses the cu128 index (torch 2.11 wheels are published there).
 306 torch = [
 307     { index = "pytorch-cu128", marker = "sys_platform == 'linux'" },
 308     { index = "pytorch-cpu", marker = "sys_platform == 'darwin'" },
 309 ]
 310 torchvision = [
 311     { index = "pytorch-cu128", marker = "sys_platform == 'linux'" },
 312     { index = "pytorch-cpu", marker = "sys_platform == 'darwin'" },
 313 ]
 314 harbor = { git = "https://github.com/laude-institute/harbor", rev = "3de07a0e01f3368921766437fc7afece3ddec23d" }
```

## skyrl/docker/Dockerfile

[Exact source](https://raw.githubusercontent.com/NovaSky-AI/SkyRL/f5bc3b78dfddfb352870d5d7430cd226e5785838/docker/Dockerfile) · SHA-256 `8ab8557f02d046ed271b420fac70fc15d102f022d79b973e6c17f9656869a95e`

```text
   1 FROM anyscale/ray:2.56.0-slim-py312-cu128
   2 
   3 RUN sudo apt-get update -y && sudo apt-get install -y wget kmod libxml2 build-essential libnuma-dev
   4 
   5 # the cuda compiler here is needed for deepspeed
   6 RUN wget https://developer.download.nvidia.com/compute/cuda/12.8.0/local_installers/cuda_12.8.0_570.86.10_linux.run \
   7     && sudo sh cuda_12.8.0_570.86.10_linux.run --silent --toolkit && rm -rf cuda_12.8.0_570.86.10_linux.run
   8 
   9 RUN curl -LsSf https://astral.sh/uv/0.9.4/install.sh | sh
  10 RUN echo "export RAY_RUNTIME_ENV_HOOK=ray._private.runtime_env.uv_runtime_env_hook.hook" >> /home/ray/.bashrc
```
