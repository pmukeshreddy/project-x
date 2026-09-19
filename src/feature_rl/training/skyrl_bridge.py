"""Lazy source-bound adapter for SkyRL skyrl-v0.3.0.

Imports and CUDA/FSDP execution remain separately qualified deployment gates.
These are actual pinned trainer calls, not a hardware preflight substitute.
The stock Harbor trial/artifact/retry pipeline is intentionally never invoked.
"""
from __future__ import annotations

from dataclasses import dataclass
import importlib.metadata
import json

from .torch_backend import CausalTurn, _masked, _torch

PINNED_SKYRL = 'f5bc3b78dfddfb352870d5d7430cd226e5785838'
PINNED_HARBOR = '3de07a0e01f3368921766437fc7afece3ddec23d'
GRPO_LOSS = 'feature_rl_sampled_clipped'
SFT_LOSS = 'feature_rl_supervised'


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


def feature_sft_loss(log_probs, old_log_probs, advantages, config, loss_mask=None, rollout_logprobs=None):
    """Supervised cross entropy; normalized unit weights use SkyRL's reduction path."""
    if rollout_logprobs is not None or loss_mask is None or not (log_probs.shape == advantages.shape == loss_mask.shape):
        raise ValueError('Supervised targets require aligned masks and no sampled probabilities')
    mask = loss_mask.bool()
    if not mask.any(): return log_probs.reshape(-1)[:0].sum(), {'clip_ratio': 0.}
    return -(_masked(log_probs, mask) * _masked(advantages.detach(), mask)).sum(), {'clip_ratio': 0.}


def register_losses():
    """Call in the trainer process before building workers; Ray must already exist."""
    from skyrl.backends.skyrl_train.utils.ppo_utils import PolicyLossRegistry, sync_registries
    PolicyLossRegistry.register(GRPO_LOSS, feature_grpo_loss)
    PolicyLossRegistry.register(SFT_LOSS, feature_sft_loss)
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
        if group.effective_size < 2: continue
        for n, turns in enumerate(group.turns):
            for t, turn in enumerate(turns):
                last = t == len(turns)-1
                rows.append(UpdateRow(turn, group.plan.group_id, n, last, float(group.rewards[n]) if last else 0.))
    return rows


class SkyRLUpdateBridge:
    """Drive a constructed RayPPOTrainer with exact per-turn rows and explicit advantages.

    The caller owns task collection/admission, sampler/cost/signal state and worker
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
                or cfg.generator.n_samples_per_prompt != 4 or not cfg.generator.step_wise_trajectories
                or cfg.generator.merge_stepwise_output or cfg.generator.apply_overlong_filtering
                or cfg.trainer.algorithm.dynamic_sampling.type is not None
                or cfg.trainer.algorithm.zero_variance_filter or cfg.trainer.algorithm.advantage_batch_normalize
                or cfg.trainer.algorithm.loss_reduction != 'token_mean'
                or cfg.trainer.algorithm.use_kl_in_reward or cfg.trainer.update_ref_every_epoch
                or trainer.has_critic):
            raise ValueError('Expected synchronous four-episode FSDP/token-mean configuration without filtering/critic/ref updates')
        self.ready = False

    async def initialize_sync(self):
        self.trainer.init_weight_sync_state()
        await self.trainer.dispatch.save_weights_for_sampler()
        self.ready = True

    async def update(self, rows: list[UpdateRow], *, algorithm: str):
        torch = _torch()
        from skyrl.train.generators.base import GeneratorOutput, TrajectoryID
        from skyrl.train.utils.trainer_utils import validate_generator_output
        trainer = self.trainer
        if not self.ready:
            raise ValueError('Initial native weight synchronization required')
        if not rows:
            return {'optimizer_skipped': True, 'reason': 'no valid group with at least two outcomes'}
        expected = {'grpo': GRPO_LOSS, 'sft': SFT_LOSS}.get(algorithm)
        if expected is None or trainer.cfg.trainer.algorithm.policy_loss_type != expected:
            raise ValueError('Explicit registered policy loss does not match requested arm')
        if algorithm == 'sft' and trainer.cfg.trainer.algorithm.use_kl_loss:
            raise ValueError('Declared SFT uses supervised loss only')
        for row in rows:
            row.turn.validate(trainer.cfg.trainer.algorithm.max_seq_len)
            if algorithm == 'grpo' and (row.turn.behavior is None or row.turn.advantage is None):
                raise ValueError('GRPO rows require validated sampled behavior and group advantage')
            if algorithm == 'sft' and (row.turn.behavior is not None or row.turn.advantage is not None):
                raise ValueError('SFT rows are deterministic targets, not sampled RL')
        output = GeneratorOutput(
            prompt_token_ids=[list(r.turn.context) for r in rows], response_ids=[list(r.turn.targets) for r in rows],
            loss_masks=[list(map(int, r.turn.mask)) for r in rows],
            rewards=[[0.]*(len(r.turn.targets)-1)+[r.reward] for r in rows],
            rollout_logprobs=[list(r.turn.behavior) for r in rows] if algorithm == 'grpo' else None,
            trajectory_ids=[TrajectoryID(r.instance_id, r.repetition_id) for r in rows],
            is_last_step=[r.is_last_step for r in rows], stop_reasons=['complete']*len(rows), rollout_metrics=None)
        validate_generator_output(len(rows), output, step_wise=True)
        uids = [row.instance_id for row in rows]
        self.ready = False
        if trainer.colocate_all:
            await trainer.inference_engine_client.sleep()
        data = trainer.convert_to_training_input(output, uids)
        data = trainer.fwd_logprobs_values_reward(data)
        # Avoid stock singleton/invalid-peer estimator. Padding rows retain zero weight.
        advantages = torch.zeros_like(data['loss_mask'])
        for n, row in enumerate(rows):
            advantages[n] = float(row.turn.advantage if algorithm == 'grpo' else 1.) * data['loss_mask'][n]
        data['advantages'] = advantages
        data['returns'] = torch.zeros_like(advantages)
        data.pop('rewards')
        data.metadata.pop('uids', None)
        data.metadata.pop('is_last_step', None)
        trainer.global_step += 1
        self.ready = False  # Failure requires explicit recovery, never stale collection.
        status = trainer.train_critic_and_policy(data)
        await trainer.dispatch.save_weights_for_sampler()
        self.ready = True
        return status
