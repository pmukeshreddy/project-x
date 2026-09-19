"""Training admission joins over actual M0 artifacts and M4 grading receipts.

The authoritative M5/M6 release resolver is mandatory and supplied by the caller;
there is intentionally no state-string-based substitute in this module.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from feature_rl.contracts import ArtifactRef, Partition, PolicyConfig, RolloutRecord, TaskBundle
from feature_rl.grading.service import read_grade
from feature_rl.environments.archive import SourceArchive
from feature_rl.environments import SandboxPolicy
from .core import GroupPlan, group_advantages, validate_trace
from .torch_backend import CausalTurn


@dataclass(frozen=True)
class PreparedGroup:
    plan: GroupPlan
    records: tuple[RolloutRecord, ...]
    turns: tuple[tuple[CausalTurn, ...], ...]
    rewards: tuple[int | None, ...]
    advantages: tuple[float | None, ...]

    @property
    def effective_size(self):
        return sum(r is not None for r in self.rewards)

    def optimization_turns(self):
        if self.effective_size < 2:
            return []
        return [turn for episode in self.turns for turn in episode]


class TrainingDataGate:
    def __init__(self, *, store, admit: Callable[[ArtifactRef], TaskBundle], grader_revision: str):
        if not callable(admit) or len(grader_revision) not in (40, 64) or any(c not in '0123456789abcdef' for c in grader_revision):
            raise ValueError('Authoritative release resolver and exact grader revision required')
        self.store, self._admit, self.grader_revision = store, admit, grader_revision

    def admit_task(self, ref: ArtifactRef) -> TaskBundle:
        admitted = self._admit(ref)
        resolved = self.store.get_artifact(ref, max_envelope_bytes=8*1024*1024)
        if not isinstance(admitted, TaskBundle) or admitted != resolved or admitted.partition != Partition.TRAIN:
            raise ValueError('Admission must resolve exact immutable training task')
        return admitted

    def _grade(self, ref, *, task, submission, seed=None, reward=None):
        grade = read_grade(self.store, ref)
        if (grade.implementation_revision != self.grader_revision or grade.task != task
                or grade.submission != submission or not grade.cleanup_verified
                or grade.reward is None or (reward is not None and grade.reward != reward)
                or (seed is not None and grade.case_seed != seed)):
            raise ValueError('Grading receipt does not bind the exact task/submission/cases/outcome')
        return grade

    def prepare_group(self, plan: GroupPlan, records: tuple[RolloutRecord, ...], *,
                      contexts: tuple[tuple[tuple[int, ...], ...], ...],
                      expected_policy: PolicyConfig, vocab_size: int, max_seq_len: int,
                      normalize_std: bool = False) -> PreparedGroup:
        if len(records) != 4 or len(contexts) != 4:
            raise ValueError('Exactly four assigned independent episodes required')
        tokenizer_digest = expected_policy.identity.tokenizer_digest
        if tokenizer_digest is None or expected_policy.policy_version != plan.policy_version:
            raise ValueError('Expected exact behavior policy/tokenizer required')
        task = records[0].task
        self.admit_task(task)
        if task.sha256 != plan.task.task_id or len({r.run_id for r in records}) != 4:
            raise ValueError('Group task identity or fresh episode identities differ')
        checked = tuple(RolloutRecord.model_validate_json(r.model_dump_json()) for r in records)
        for n, record in enumerate(checked):
            if (record.policy.model_dump(exclude={'seed'}) != expected_policy.model_dump(exclude={'seed'})
                    or record.limits != checked[0].limits or record.task != task or record.policy.policy_version != plan.policy_version
                    or record.policy.seed != plan.episode_seeds[n]
                    or not record.seeds.same_cases_within_group or record.seeds.seeds != (plan.case_seed,)):
                raise ValueError('Group task/policy/episode seed/private case seed mismatch')
            if record.reward is not None:
                if not record.training_eligible or record.policy.identity.tokenizer_digest != tokenizer_digest:
                    raise ValueError('Measured episode is not an exact training trajectory')
                grade_refs = {ref for e in record.grading_evidence if e.producer == 'feature_rl.grading'
                              and e.scope == 'real_integration' for ref in e.artifacts if ref.kind == 'm4-grade-receipt'}
                if len(grade_refs) != 1 or record.submission is None:
                    raise ValueError('Unique real M4 grading receipt required')
                self._grade(next(iter(grade_refs)), task=task, submission=record.submission,
                            seed=plan.case_seed, reward=record.reward)
        rewards = tuple(r.reward for r in checked)
        advantages = group_advantages(rewards, normalize_std=normalize_std)
        episodes = []
        for n, record in enumerate(checked):
            rows = []
            if record.reward is not None:
                if len(contexts[n]) != len(record.steps) or tuple(s.index for s in record.steps) != tuple(range(len(record.steps))):
                    raise ValueError('Per-turn context/step ordering mismatch')
                for step, context in zip(record.steps, contexts[n]):
                    trace = step.token_trace
                    validate_trace(trace, context=context, policy_version=plan.policy_version,
                                   vocab_size=vocab_size, max_seq_len=max_seq_len)
                    rows.append(CausalTurn(context, trace.sampled_token_ids, trace.assistant_loss_mask,
                                           trace.behavior_log_probabilities, advantages[n]))
            episodes.append(tuple(rows))
        return PreparedGroup(plan, checked, tuple(episodes), rewards, advantages)

    def prepare_supervised_source(self, *, task: ArtifactRef, submission: ArtifactRef, grade_ref: ArtifactRef,
                                  render_solution: Callable, encode_context: Callable, encode_target: Callable,
                                  max_seq_len: int) -> tuple[CausalTurn, ...]:
        """SFT targets come only from the exact M4-verified solution source.

        render_solution is the trusted fixed harness renderer, shared across arms;
        it receives the admitted task (controller-only) and inert graded source archive and
        returns (rendered_context, rendered_completion) pairs. It never executes source.
        Tokenization is deterministic supervised labeling, not a sampled TokenTrace.
        """
        admitted = self.admit_task(task)
        grade = self._grade(grade_ref, task=task, submission=submission, reward=1)
        if grade.source is None:
            raise ValueError('Verified solution source missing')
        payload = self.store.get_bytes(grade.source, max_envelope_bytes=24*1024*1024, max_payload_bytes=16*1024*1024)
        source = SourceArchive.read(payload, SandboxPolicy())
        pairs = tuple(render_solution(admitted, source))
        if not pairs:
            raise ValueError('Verified solution produced no supervised targets')
        return tuple(tokenize_supervision(context, target, encode_context=encode_context,
                                         encode_target=encode_target, max_seq_len=max_seq_len)
                     for context, target in pairs)


def tokenize_supervision(context: str, target: str, *, encode_context: Callable, encode_target: Callable,
                         max_seq_len: int) -> CausalTurn:
    """Encode the full rendered text and require the exact generation-context prefix.

    encode_target receives context+target, not the target alone. Different chat
    templates or a BPE merge across the boundary must be fixed, never silently sliced.
    """
    if not context or not target:
        raise ValueError('Nonempty rendered context and supervised target required')
    prompt = tuple(encode_context(context))
    full = tuple(encode_target(context + target))
    if full[:len(prompt)] != prompt:
        raise ValueError('Supervised template/tokenizer changed the generation context prefix')
    targets = full[len(prompt):]
    turn = CausalTurn(prompt, targets, (True,)*len(targets))
    turn.validate(max_seq_len)
    return turn
