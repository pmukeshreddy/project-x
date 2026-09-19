"""Deterministic grouping and serialization checks; no framework imports or admission claims."""
from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
import math

from feature_rl.contracts import TokenTrace


def digest(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def group_advantages(rewards: tuple[int | None, ...], *, normalize_std: bool = False) -> tuple[float | None, ...]:
    """Four assigned trials, valid-only population mean; invalid peers are never zero rewards."""
    if len(rewards) != 4 or any(r is not None and (type(r) is not int or r not in (0, 1)) for r in rewards):
        raise ValueError('GRPO requires four binary-or-invalid outcomes')
    valid = [r for r in rewards if r is not None]
    n = len(valid)
    if n < 2:
        return tuple(None if r is None else 0. for r in rewards)
    total = sum(valid)
    std = math.sqrt(sum(((r*n-total)/n)**2 for r in valid) / n)
    scale = max(std, 1e-6) if normalize_std else 1.
    return tuple(None if r is None else (r*n-total)/n/scale for r in rewards)


def validate_trace(trace: TokenTrace, *, context: tuple[int, ...], policy_version: str,
                   vocab_size: int, max_seq_len: int) -> None:
    # Revalidate even objects produced through model_construct/model_copy.
    trace = TokenTrace.model_validate_json(trace.model_dump_json())
    if not context or trace.context_token_ids != context:
        raise ValueError('Trace differs from actual per-turn context or has no causal context')
    if trace.policy_version != policy_version:
        raise ValueError('Stale behavior policy')
    if len(context) + len(trace.sampled_token_ids) > max_seq_len:
        raise ValueError('Trajectory exceeds declared context; truncation forbidden')
    if any(type(t) is not int or not 0 <= t < vocab_size for t in context + trace.sampled_token_ids):
        raise ValueError('Token outside exact tokenizer vocabulary')
    if any(not math.isfinite(p) or p > 0 for p in trace.behavior_log_probabilities):
        raise ValueError('Invalid behavior log probabilities')
    if not any(trace.assistant_loss_mask):
        raise ValueError('Turn has no sampled assistant loss tokens')


@dataclass(frozen=True)
class TaskSlot:
    task_id: str
    family: str
    feature: str

    def __post_init__(self):
        if any(not isinstance(v, str) or not v.strip() for v in (self.task_id, self.family, self.feature)):
            raise ValueError('Task identity, family and feature are required')


@dataclass(frozen=True)
class GroupPlan:
    group_id: str
    task: TaskSlot
    policy_version: str
    episode_seeds: tuple[int, int, int, int]
    case_seed: int
    position: int


class BalancedSampler:
    """Round-robin family, then feature, then task; seeded stable category order.

    Only the explicit admitted training roster may be passed by the training service.
    No metadata is inferred from repository text; no replacement for uniform groups.
    """
    def __init__(self, tasks: list[TaskSlot], *, seed: int):
        if not tasks or type(seed) is not int or seed < 0:
            raise ValueError('Nonempty roster and nonnegative seed required')
        if len({t.task_id for t in tasks}) != len(tasks):
            raise ValueError('Duplicate task in frozen roster')
        self.tasks = tuple(sorted(tasks, key=lambda t: t.task_id))
        self.seed = seed
        self.position = 0
        self.roster_digest = digest([asdict(t) for t in self.tasks])
        self._families = self._ordered({t.family for t in self.tasks})

    def _ordered(self, values):
        return sorted(values, key=lambda value: digest([self.seed, value]))

    def next_group(self, policy_version: str) -> GroupPlan:
        if not policy_version:
            raise ValueError('Policy version required')
        pos = self.position
        family = self._families[pos % len(self._families)]
        visits = pos // len(self._families)
        features = self._ordered({t.feature for t in self.tasks if t.family == family})
        feature = features[visits % len(features)]
        tasks = sorted((t for t in self.tasks if (t.family, t.feature) == (family, feature)),
                       key=lambda t: digest([self.seed, t.task_id]))
        task = tasks[(visits // len(features)) % len(tasks)]
        gid = digest([self.roster_digest, self.seed, pos, policy_version])
        seeds = tuple(int(digest([gid, 'episode', n])[:15], 16) for n in range(4))
        if len(set(seeds)) != 4:
            raise ValueError('Episode seed collision')
        self.position += 1
        return GroupPlan(gid, task, policy_version, seeds, int(digest([gid, 'case'])[:15], 16), pos)

    def state_dict(self):
        return dict(roster_digest=self.roster_digest, seed=self.seed, position=self.position)

    def load_state_dict(self, state):
        if (set(state) != {'roster_digest', 'seed', 'position'} or state['roster_digest'] != self.roster_digest
                or state['seed'] != self.seed or type(state['position']) is not int or state['position'] < 0):
            raise ValueError('Sampler checkpoint differs from frozen roster/seed/cursor')
        self.position = state['position']


class SignalGate:
    """Bounded engineering probe, evaluated only on real admitted groups by the service."""
    def __init__(self):
        self._groups: dict[str, tuple[str, tuple[int | None, ...]]] = {}

    @property
    def ready(self):
        mixed = [(task, rewards) for task, rewards in self._groups.values() if 0 in rewards and 1 in rewards]
        return len(mixed) >= 8 and len({task for task, _ in mixed}) >= 4

    def observe(self, group_id: str, task_id: str, rewards: tuple[int | None, ...]):
        group_advantages(rewards)
        if not group_id or not task_id or group_id in self._groups:
            raise ValueError('Missing/duplicate signal group identity')
        if len(self._groups) >= 64:
            raise ValueError('Feasibility probe exhausted 64 groups')
        self._groups[group_id] = (task_id, rewards)

    def state_dict(self):
        return {key: [task, list(rewards)] for key, (task, rewards) in self._groups.items()}

    def load_state_dict(self, state):
        restored = SignalGate()
        for key, (task, rewards) in state.items():
            restored.observe(key, task, tuple(rewards))
        self._groups = restored._groups
