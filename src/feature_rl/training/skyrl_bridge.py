"""Lazy source-bound adapter for SkyRL skyrl-v0.3.0.

Imports and CUDA/FSDP execution remain separately qualified deployment gates.
These are actual pinned trainer calls, not a hardware preflight substitute.
The stock Harbor trial/artifact/retry pipeline is intentionally never invoked.
"""
from __future__ import annotations

from dataclasses import dataclass
import copy
import importlib.metadata
import json
import logging
import threading
import types

from .torch_backend import CausalTurn, _masked, _torch

PINNED_SKYRL = 'f5bc3b78dfddfb352870d5d7430cd226e5785838'
PINNED_HARBOR = '3de07a0e01f3368921766437fc7afece3ddec23d'
GRPO_LOSS = 'feature_rl_sampled_clipped'
_LOSS_REGISTRATION_LOCK = threading.RLock()
_LOGGER = logging.getLogger(__name__)


def _info(message):
    """SkyRL training logs through loguru; stdlib logging stays available for tests."""
    _LOGGER.info(message)
    try:
        from loguru import logger
    except ImportError:
        return
    logger.info(message)


def feature_grpo_loss(log_probs, old_log_probs, advantages, config, loss_mask=None, rollout_logprobs=None):
    """SkyRL signature; advantages already carry its minibatch token normalization.

    Hence SUM here, not a second token mean. old_log_probs is deliberately ignored:
    that tensor is a recomputed model probability, not the sampled behavior policy.
    """
    torch = _torch()
    if rollout_logprobs is None or loss_mask is None or not (
            log_probs.shape == rollout_logprobs.shape == advantages.shape == loss_mask.shape):
        raise ValueError('Sampled behavior/logprob/advantage/mask alignment required')
    mask = loss_mask.bool()
    if not mask.any():
        # Padding-only microbatches contribute a differentiable zero, never NaN.
        return log_probs.reshape(-1)[:0].sum(), {'clip_ratio': 0.}
    current, old, adv = _masked(log_probs, mask), _masked(rollout_logprobs.detach(), mask), _masked(advantages.detach(), mask)
    ratio = (current-old).exp()
    if not torch.isfinite(ratio).all(): raise ValueError('Nonfinite sampled likelihood ratio')
    low, high = 1-config.eps_clip_low, 1+config.eps_clip_high
    loss = -torch.minimum(ratio*adv, ratio.clamp(low, high)*adv).sum()
    return loss, {'clip_ratio': float(((ratio < low) | (ratio > high)).float().mean().detach())}


def _same_loss(actual, expected):
    if actual is expected:
        return True
    if not isinstance(actual, types.FunctionType) or actual.__closure__ is not None:
        return False
    if (actual.__module__, actual.__qualname__, actual.__code__, actual.__defaults__, actual.__kwdefaults__) != (
            expected.__module__, expected.__qualname__, expected.__code__, expected.__defaults__, expected.__kwdefaults__):
        return False
    # Equal bytecode with substituted helper globals is not the same loss.
    return all(actual.__globals__.get(name) is expected.__globals__[name]
               for name in expected.__code__.co_names if name in expected.__globals__)


def register_losses():
    """Idempotent within/across Ray sessions, without overwriting another loss."""
    import ray
    import cloudpickle
    from skyrl.backends.skyrl_train.utils.ppo_utils import PolicyLossRegistry, sync_registries
    losses = {GRPO_LOSS: feature_grpo_loss}
    with _LOSS_REGISTRATION_LOCK:
        if not ray.is_initialized():
            raise ValueError('Ray must be initialized before feature loss registration')
        # Pinned sync_with_actor uploads local functions before importing remote
        # names. Inspect both sides first so it cannot silently replace a conflict.
        for name, expected in losses.items():
            actual = PolicyLossRegistry._functions.get(name)
            if name in PolicyLossRegistry._functions and not _same_loss(actual, expected):
                raise ValueError('Conflicting local SkyRL loss registration: ' + name)
        try:
            actor = ray.get_actor(PolicyLossRegistry._actor_name)
        except ValueError:
            actor = None
        if actor is not None:
            available = ray.get(actor.list_available.remote())
            for name, expected in losses.items():
                if name in available and not _same_loss(cloudpickle.loads(ray.get(actor.get.remote(name))), expected):
                    raise ValueError('Conflicting remote SkyRL loss registration: ' + name)
        PolicyLossRegistry._ray_actor = actor
        PolicyLossRegistry._synced_to_actor = False
        # Also resets the pinned registry's stale actor handle after Ray shutdown.
        sync_registries()
        for name, expected in losses.items():
            if name not in PolicyLossRegistry.list_available():
                PolicyLossRegistry.register(name, expected)
            elif not _same_loss(PolicyLossRegistry.get(name), expected):
                raise ValueError('Conflicting synchronized SkyRL loss registration: ' + name)
        sync_registries()


def verify_installed_pins():
    """Check installed provenance; source checkout identity is verified by the launcher."""
    for package, expected in [('harbor', PINNED_HARBOR)]:
        dist = importlib.metadata.distribution(package)
        direct = json.loads(dist.read_text('direct_url.json') or '{}')
        if direct.get('vcs_info', {}).get('commit_id') != expected:
            raise ValueError(f'{package} installation does not attest required commit {expected}')
    if importlib.metadata.version('torch').split('+')[0] != '2.11.0':
        raise ValueError('Pinned SkyRL requires Torch 2.11.0')
    if importlib.metadata.version('vllm').split('+')[0] != '0.23.0':
        raise ValueError('Pinned SkyRL requires vLLM 0.23.0 with the frozen CUDA wheel sources')


@dataclass(frozen=True)
class UpdateRow:
    turn: CausalTurn
    instance_id: str
    repetition_id: int
    is_last_step: bool
    reward: float


def group_rows(groups):
    """Only already-admitted/validated PreparedGroup inputs from TrainingDataGate."""
    rows = []
    for group in groups:
        if group.effective_size < 2 or not any(a is not None and a != 0 for a in group.advantages): continue
        for n, turns in enumerate(group.turns):
            for t, turn in enumerate(turns):
                last = t == len(turns)-1
                rows.append(UpdateRow(turn, group.plan.group_id, n, last, float(group.rewards[n]) if last else 0.))
    return rows


def convert_eligible_batch(trainer, output, uids):
    """Native tensor conversion for the rows that actually survived admission.

    Pinned conversion assumes a divisible, fixed number of prompts. A shallow
    trainer view with a private config converts the actual groups as single-prompt
    minibatches. Only its boundary metadata is then coalesced to the configured
    minibatch size, including a smaller final minibatch. Native tensor construction
    and DP padding remain unchanged; no replacement episodes or measured rows are
    inserted. The original trainer config and collection/accounting remain intact.
    """
    from skyrl.train.dataset.preprocess import compute_prompt_mini_batch_boundaries
    if not uids or any(not isinstance(uid, str) or not uid for uid in uids):
        raise ValueError('Nonempty eligible group identities required')
    cfg = trainer.cfg
    group_size = cfg.generator.n_samples_per_prompt
    if not cfg.generator.step_wise_trajectories or type(group_size) is not int or group_size < 2:
        raise ValueError('Stepwise conversion requires at least two assigned episodes per group')
    count = len(set(uids))
    try:
        prompts = compute_prompt_mini_batch_boundaries(uids, 1, count, True, group_size)
    except AssertionError as exc:
        raise ValueError('Eligible group rows must be contiguous') from exc
    sizes = {'policy': cfg.trainer.policy_mini_batch_size}
    if cfg.trainer.critic.model.path is not None:
        sizes['critic'] = cfg.trainer.critic_mini_batch_size
    if any(type(size) is not int or size < 1 for size in sizes.values()):
        raise ValueError('Positive integral native minibatch sizes required')
    view = copy.copy(trainer)
    view.cfg = copy.deepcopy(cfg)
    view.cfg.trainer.train_batch_size = count
    for model in sizes:
        setattr(view.cfg.trainer, f'{model}_mini_batch_size', 1)
    data = view.convert_to_training_input(output, uids)
    for model, size in sizes.items():
        data.metadata[f'{model}_mini_batch_boundaries'] = [
            (prompts[i][0], prompts[min(i + size, count) - 1][1]) for i in range(0, count, size)
        ]
    return data


class SkyRLUpdateBridge:
    """Drive a constructed RayPPOTrainer with exact per-turn rows and explicit advantages.

    The caller owns task collection/admission, sampler/cost state and worker
    policy acknowledgments. initialize_sync/update are real native operations.
    No private grader material enters this tensor bridge or a candidate worker.
    """
    def __init__(self, trainer):
        from skyrl.train.trainer import RayPPOTrainer
        if not isinstance(trainer, RayPPOTrainer):
            raise TypeError('Actual pinned RayPPOTrainer required')
        self.trainer = trainer
        cfg = trainer.cfg
        if (cfg.trainer.strategy != 'fsdp' or cfg.trainer.update_epochs_per_batch != 1
                or type(cfg.generator.n_samples_per_prompt) is not int or cfg.generator.n_samples_per_prompt < 2
                or not cfg.generator.step_wise_trajectories
                or cfg.generator.merge_stepwise_output or cfg.generator.apply_overlong_filtering
                or cfg.trainer.algorithm.dynamic_sampling.type is not None
                or cfg.trainer.algorithm.zero_variance_filter or cfg.trainer.algorithm.advantage_batch_normalize
                or cfg.trainer.algorithm.loss_reduction != 'token_mean'
                or cfg.trainer.algorithm.use_kl_in_reward or cfg.trainer.update_ref_every_epoch
                or trainer.has_critic):
            raise ValueError('Expected synchronous grouped FSDP/token-mean configuration without filtering/critic/ref updates')
        self.ready = False

    async def _inference_engine_is_sleeping(self, client):
        """Read vLLM sleep state through the pinned inference client.

        Pinned RemoteInferenceClient exposes sleep() and control-plane fan-out,
        not is_sleeping(). Newer SkyRL added that method as GET /is_sleeping,
        which vLLM 0.23 already serves. Use the method when present and the
        same existing fan-out otherwise. Do not treat a missing report as asleep.
        """
        query = getattr(client, 'is_sleeping', None)
        if query is None:
            responses = await client._call_all_servers('/is_sleeping', method='GET')
            if not isinstance(responses, dict) or not responses:
                raise RuntimeError('colocated inference engine sleep state was not reported')
            try:
                flags = [response['body']['is_sleeping'] for response in responses.values()]
            except (KeyError, TypeError) as exc:
                raise RuntimeError('colocated inference engine sleep state was not reported') from exc
            if any(type(flag) is not bool for flag in flags):
                raise RuntimeError('colocated inference engine sleep state was not reported')
            return all(flags)
        sleeping = await query()
        if type(sleeping) is not bool:
            raise RuntimeError('colocated inference engine sleep state was not reported')
        return sleeping

    async def _ensure_colocated_inference_asleep(self):
        """Fail closed unless colocated vLLM is asleep before policy backload."""
        trainer = self.trainer
        if not trainer.colocate_all:
            return
        client = trainer.inference_engine_client
        if not await self._inference_engine_is_sleeping(client):
            _info('colocated inference engine is awake before policy weight sync')
            await client.sleep()
            if not await self._inference_engine_is_sleeping(client):
                raise RuntimeError('colocated inference engine did not enter sleep state before policy backload')
        _info('colocated inference sleep verified before policy weight sync')

    async def initialize_sync(self):
        self.trainer.init_weight_sync_state()
        await self._ensure_colocated_inference_asleep()
        await self.trainer.dispatch.save_weights_for_sampler()
        self.ready = True

    async def update(self, rows: list[UpdateRow], *, algorithm: str):
        torch = _torch()
        from skyrl.train.generators.base import GeneratorOutput, TrajectoryID
        from skyrl.train.utils.trainer_utils import validate_generator_output
        trainer = self.trainer
        if not self.ready:
            raise ValueError('Initial native weight synchronization required')
        if algorithm != 'grpo' or trainer.cfg.trainer.algorithm.policy_loss_type != GRPO_LOSS:
            raise ValueError('Explicit registered GRPO policy loss required')
        if not rows:
            return {'optimizer_skipped': True, 'reason': 'no valid group with at least two outcomes'}
        for row in rows:
            row.turn.validate(trainer.cfg.trainer.algorithm.max_seq_len)
            if row.turn.behavior is None or row.turn.advantage is None:
                raise ValueError('GRPO rows require validated sampled behavior and group advantage')
        output = GeneratorOutput(
            prompt_token_ids=[list(r.turn.context) for r in rows], response_ids=[list(r.turn.targets) for r in rows],
            loss_masks=[list(map(int, r.turn.mask)) for r in rows],
            rewards=[[0.]*(len(r.turn.targets)-1)+[r.reward] for r in rows],
            rollout_logprobs=[list(r.turn.behavior) for r in rows],
            trajectory_ids=[TrajectoryID(r.instance_id, r.repetition_id) for r in rows],
            is_last_step=[r.is_last_step for r in rows], stop_reasons=['complete']*len(rows), rollout_metrics=None)
        validate_generator_output(len(rows), output, step_wise=True)
        uids = [row.instance_id for row in rows]
        self.ready = False
        if trainer.colocate_all:
            await trainer.inference_engine_client.sleep()
        data = convert_eligible_batch(trainer, output, uids)
        data = trainer.fwd_logprobs_values_reward(data)
        # Avoid stock singleton/invalid-peer estimator. Padding rows retain zero weight.
        advantages = torch.zeros_like(data['loss_mask'])
        for n, row in enumerate(rows):
            advantages[n] = float(row.turn.advantage) * data['loss_mask'][n]
        data['advantages'] = advantages
        data['returns'] = torch.zeros_like(advantages)
        data.pop('rewards')
        data.metadata.pop('uids', None)
        data.metadata.pop('is_last_step', None)
        trainer.global_step += 1
        self.ready = False  # Failure requires explicit recovery, never stale collection.
        status = trainer.train_critic_and_policy(data)
        await self._ensure_colocated_inference_asleep()
        await trainer.dispatch.save_weights_for_sampler()
        self.ready = True
        return status
