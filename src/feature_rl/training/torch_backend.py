"""Real causal GRPO tensor updates and strict local optimizer checkpoints.

This low-level engine is also usable for CPU diagnostics. Task admission
belongs to the training service; an optimizer receipt is not feature-learning evidence.
Torch imports are lazy so the core package remains usable without the training extra.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import copy
import hashlib
import io
import json
import math
import os
from pathlib import Path
import random
import shutil
import tempfile

from .core import digest


def _torch():
    import torch
    return torch


def _masked(values, mask):
    torch = _torch()
    if values.shape != mask.shape or mask.dtype != torch.bool or not mask.any():
        raise ValueError('Nonempty aligned boolean assistant mask required')
    selected = values.masked_select(mask)
    if not torch.isfinite(selected).all():
        raise ValueError('Nonfinite trainable values')
    return selected


def clipped_surrogate(logprobs, behavior, advantages, mask, epsilon_low: float, epsilon_high: float):
    """Token mean of the PPO clipped surrogate using exact sampled behavior probabilities."""
    torch = _torch()
    if not 0 <= epsilon_low < 1 or not 0 <= epsilon_high < 1:
        raise ValueError('Clipping epsilon outside [0,1)')
    if not (logprobs.shape == behavior.shape == advantages.shape == mask.shape):
        raise ValueError('Policy probability/advantage/mask shapes differ')
    logp = _masked(logprobs, mask)
    old = _masked(behavior.detach(), mask)
    adv = _masked(advantages.detach(), mask)
    ratio = (logp - old).exp()
    if not torch.isfinite(ratio).all():
        raise ValueError('Nonfinite likelihood ratio')
    return -torch.minimum(ratio * adv, ratio.clamp(1-epsilon_low, 1+epsilon_high) * adv).mean()


def model_digest(model) -> str:
    torch = _torch()
    result = hashlib.sha256()
    for name, tensor in sorted(model.state_dict().items()):
        if not isinstance(tensor, torch.Tensor):
            raise ValueError('Checkpoint model state must contain tensors only')
        value = tensor.detach().cpu().contiguous().clone()
        result.update(json.dumps([name, str(value.dtype), list(value.shape)], separators=(',', ':')).encode())
        result.update(bytes(value.untyped_storage()))
    return result.hexdigest()


@dataclass(frozen=True)
class CausalTurn:
    context: tuple[int, ...]
    targets: tuple[int, ...]
    mask: tuple[bool, ...]
    behavior: tuple[float, ...] | None = None
    advantage: float | None = None

    def validate(self, max_seq_len):
        if (not self.context or not self.targets or len(self.targets) != len(self.mask)
                or not any(self.mask) or any(type(m) is not bool for m in self.mask)):
            raise ValueError('Nonempty context/targets and exact assistant mask required')
        if any(type(t) is not int or t < 0 for t in self.context + self.targets):
            raise ValueError('Invalid token IDs')
        if len(self.context) + len(self.targets) > max_seq_len:
            raise ValueError('Context exceeds declared maximum; no truncation permitted')
        if self.behavior is not None and (len(self.behavior) != len(self.targets)
                or any(not math.isfinite(v) or v > 0 for v in self.behavior)):
            raise ValueError('Invalid sampled behavior probabilities')
        if self.advantage is not None and not math.isfinite(self.advantage):
            raise ValueError('Nonfinite advantage')


@dataclass(frozen=True)
class UpdateReceipt:
    algorithm: str
    loss: float
    gradient_norm: float
    assistant_tokens: int
    updated: bool
    before: str
    after: str
    optimizer_steps: int


class TorchUpdater:
    def __init__(self, model, *, learning_rate: float, kl_coefficient: float = .001,
                 epsilon_low: float = .2, epsilon_high: float = .2,
                 max_grad_norm: float = 1., weight_decay: float = 0.,
                 max_seq_len: int = 4096, temperature: float = 1.):
        torch = _torch()
        if (not math.isfinite(learning_rate) or learning_rate <= 0 or not math.isfinite(kl_coefficient)
                or kl_coefficient < 0 or not 0 <= epsilon_low < 1 or not 0 <= epsilon_high < 1
                or not math.isfinite(max_grad_norm) or max_grad_norm <= 0 or max_seq_len < 2
                or not math.isfinite(temperature) or temperature <= 0
                or not math.isfinite(weight_decay) or weight_decay < 0):
            raise ValueError('Invalid optimizer configuration')
        self.settings = dict(learning_rate=learning_rate, kl_coefficient=kl_coefficient,
                             epsilon_low=epsilon_low, epsilon_high=epsilon_high,
                             max_grad_norm=max_grad_norm, weight_decay=weight_decay,
                             max_seq_len=max_seq_len, temperature=temperature)
        self.model = model
        self.reference = copy.deepcopy(model).eval()
        for param in self.reference.parameters():
            param.requires_grad_(False)
        self.optimizer = torch.optim.AdamW(model.parameters(), lr=learning_rate, weight_decay=weight_decay)
        self.optimizer_steps = 0
        self.failed = False

    def logprobs(self, turn: CausalTurn, *, reference=False):
        torch = _torch()
        turn.validate(self.settings['max_seq_len'])
        model = self.reference if reference else self.model
        # Dropout must not change the conditional probability between collection/update.
        model.eval()
        device = next(model.parameters()).device
        ids = torch.tensor([turn.context + turn.targets], dtype=torch.long, device=device)
        try:
            output = model(input_ids=ids, attention_mask=torch.ones_like(ids))
        except (IndexError, RuntimeError) as exc:
            raise ValueError('Causal model rejected exact context/target tokens') from exc
        logits = output.logits
        if logits.ndim != 3 or logits.shape[:2] != ids.shape or max(turn.context + turn.targets) >= logits.shape[-1]:
            raise ValueError('Causal logits/token vocabulary mismatch')
        conditional = logits[0, len(turn.context)-1:-1].float() / self.settings['temperature']
        target = ids[0, len(turn.context):]
        return conditional.log_softmax(-1).gather(-1, target[:, None]).squeeze(-1)

    def update(self, turns: list[CausalTurn], *, algorithm: str) -> UpdateReceipt:
        torch = _torch()
        if self.failed:
            raise ValueError('Updater must reload after failed optimizer mutation')
        if algorithm != 'grpo' or not turns:
            raise ValueError('Nonempty explicit GRPO batch required')
        for turn in turns:
            turn.validate(self.settings['max_seq_len'])
            if turn.behavior is None or turn.advantage is None:
                raise ValueError('GRPO requires exact behavior probabilities and group advantage')
        before = model_digest(self.model)
        self.optimizer.zero_grad(set_to_none=True)
        terms, total_tokens = [], sum(sum(t.mask) for t in turns)
        for turn in turns:
            logp = self.logprobs(turn)
            mask = torch.tensor(turn.mask, dtype=torch.bool, device=logp.device)
            tokens = int(mask.sum())
            old = torch.tensor(turn.behavior, dtype=logp.dtype, device=logp.device)
            adv = torch.full_like(logp, turn.advantage)
            loss = clipped_surrogate(logp, old, adv, mask, self.settings['epsilon_low'], self.settings['epsilon_high'])
            if self.settings['kl_coefficient']:
                with torch.no_grad():
                    ref = self.logprobs(turn, reference=True)
                delta = _masked(ref - logp, mask)
                kl = (delta.exp() - delta - 1).mean()
                loss = loss + self.settings['kl_coefficient'] * kl
            terms.append(loss * tokens / total_tokens)
        objective = torch.stack(terms).sum()
        if not torch.isfinite(objective):
            raise ValueError('Nonfinite loss')
        objective.backward()
        norm = torch.nn.utils.clip_grad_norm_(self.model.parameters(), self.settings['max_grad_norm'], error_if_nonfinite=True)
        if not torch.isfinite(norm):
            raise ValueError('Nonfinite gradients')
        changed = False
        if float(norm) > 0:
            self.failed = True  # No usable receipt/checkpoint after partial optimizer failure.
            self.optimizer.step()
            if any(not torch.isfinite(p).all() for p in self.model.parameters()):
                raise ValueError('Optimizer produced nonfinite parameters; reload required')
            self.optimizer_steps += 1
            self.failed = False
            changed = model_digest(self.model) != before
        self.optimizer.zero_grad(set_to_none=True)
        return UpdateReceipt(algorithm, float(objective.detach()), float(norm), total_tokens,
                             changed, before, model_digest(self.model), self.optimizer_steps)

    def save_checkpoint(self, path: Path, *, binding: dict, progress: dict, policy_version: str) -> str:
        """Private, content-addressed receipt over tensor/optimizer/reference/RNG and immutable joins.

        Caller publishes M0 TrainingCheckpoint separately with actual experiment evidence.
        This file is never a candidate-supplied pickle and load uses weights_only=True.
        """
        torch = _torch()
        if self.failed or not binding or not policy_version:
            raise ValueError('Cannot checkpoint failed or unidentified state')
        path = Path(path)
        if path.exists() or path.is_symlink():
            raise ValueError('Checkpoint destination already exists')
        # Validate caller metadata is finite JSON before creating anything.
        metadata = dict(schema_version=1, binding=binding, progress=progress, policy_version=policy_version,
                        settings=self.settings, optimizer_steps=self.optimizer_steps,
                        policy_digest=model_digest(self.model), reference_digest=model_digest(self.reference))
        metadata = json.loads(json.dumps(metadata, allow_nan=False))
        buffer = io.BytesIO()
        torch.save(dict(model=self.model.state_dict(), reference=self.reference.state_dict(),
                        optimizer=self.optimizer.state_dict(), torch_rng=torch.get_rng_state(),
                        cuda_rng=torch.cuda.get_rng_state_all() if torch.cuda.is_available() else [],
                        python_rng=random.getstate()), buffer)
        payload = buffer.getvalue()
        metadata['state_sha256'] = hashlib.sha256(payload).hexdigest()
        encoded = json.dumps(metadata, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()
        identity = hashlib.sha256(encoded).hexdigest()
        path.parent.mkdir(parents=True, exist_ok=True)
        staging = Path(tempfile.mkdtemp(prefix='.training-checkpoint-', dir=path.parent))
        try:
            for name, data in [('state.pt', payload), ('manifest.json', encoded)]:
                with (staging / name).open('xb') as output:
                    os.chmod(staging / name, 0o600)
                    output.write(data)
                    output.flush()
                    os.fsync(output.fileno())
            os.rename(staging, path)
            parent_fd = os.open(path.parent, os.O_RDONLY)
            try: os.fsync(parent_fd)
            finally: os.close(parent_fd)
        finally:
            if staging.exists(): shutil.rmtree(staging)
        return identity

    def load_checkpoint(self, path: Path, *, expected_digest: str, binding: dict) -> dict:
        torch = _torch()
        path = Path(path)
        if path.is_symlink() or any((path / name).is_symlink() for name in ('manifest.json', 'state.pt')):
            raise ValueError('Checkpoint links forbidden')
        manifest_bytes = (path / 'manifest.json').read_bytes()
        if hashlib.sha256(manifest_bytes).hexdigest() != expected_digest:
            raise ValueError('Checkpoint manifest digest mismatch')
        metadata = json.loads(manifest_bytes)
        if metadata['binding'] != binding or metadata['settings'] != self.settings or metadata['schema_version'] != 1:
            raise ValueError('Checkpoint configuration/task/reference binding mismatch')
        payload = (path / 'state.pt').read_bytes()
        if hashlib.sha256(payload).hexdigest() != metadata['state_sha256']:
            raise ValueError('Checkpoint tensor digest mismatch')
        state = torch.load(io.BytesIO(payload), map_location=next(self.model.parameters()).device, weights_only=True)
        # Validate on isolated model/optimizer copies before mutating the live updater.
        candidate, reference = copy.deepcopy(self.model), copy.deepcopy(self.reference)
        candidate.load_state_dict(state['model'], strict=True)
        reference.load_state_dict(state['reference'], strict=True)
        if model_digest(candidate) != metadata['policy_digest'] or model_digest(reference) != metadata['reference_digest']:
            raise ValueError('Checkpoint model identity mismatch')
        optimizer = torch.optim.AdamW(candidate.parameters(), lr=self.settings['learning_rate'], weight_decay=self.settings['weight_decay'])
        optimizer.load_state_dict(state['optimizer'])
        if metadata['optimizer_steps'] > 0 and not optimizer.state:
            raise ValueError('Checkpoint has no required optimizer moments')
        if state['cuda_rng'] and not torch.cuda.is_available():
            raise ValueError('CUDA RNG resume requires original CUDA topology')
        if state['cuda_rng'] and len(state['cuda_rng']) != torch.cuda.device_count():
            raise ValueError('CUDA RNG topology differs')
        self.model, self.reference, self.optimizer = candidate, reference, optimizer
        self.optimizer_steps = metadata['optimizer_steps']
        torch.set_rng_state(state['torch_rng'].cpu())
        if state['cuda_rng']: torch.cuda.set_rng_state_all([s.cpu() for s in state['cuda_rng']])
        random.setstate(state['python_rng'])
        self.failed = False
        return metadata
