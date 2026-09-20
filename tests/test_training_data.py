"""Inert diagnostic artifact fixtures; admissions here are explicit test doubles."""
from m4_fixtures import runtime_policy
import json
from pathlib import Path
import pytest
from feature_rl.artifacts import ArtifactStore
from feature_rl.contracts import ActorRole, TaskBundle, TrainingConfig


def test_training_data_module_exists():
    import importlib.util
    assert importlib.util.find_spec('feature_rl.training.data') is not None


def test_admission_callback_is_mandatory_and_failure_propagates(tmp_path):
    from feature_rl.training.data import TrainingDataGate
    samples = json.loads(Path('docs/evidence/M0/examples.json').read_text())
    store = ArtifactStore(tmp_path, role=ActorRole.CONTROLLER)
    task = TaskBundle.model_validate_json(json.dumps(samples['TaskBundle']))
    ref = store.put_artifact(task)
    def reject(_): raise ValueError('not released: authoritative gate')
    gate = TrainingDataGate(store=store, admit=reject, grader_revision='a'*40)
    with pytest.raises(ValueError, match='authoritative gate'): gate.admit_task(ref)
    with pytest.raises(ValueError): TrainingDataGate(store=store, admit=None, grader_revision='a'*40)


def actual_group_fixture(tmp_path):
    """Real M4 source checks, diagnostic admission/tokens; no worker is invoked."""
    from copy import deepcopy
    from feature_rl import contracts as c
    from feature_rl.environments import EnvironmentRuntime, SandboxPolicy
    from feature_rl.grading.service import GradingService, read_grade
    from feature_rl.training.core import BalancedSampler, TaskSlot
    from feature_rl.training.data import TrainingDataGate
    from m4_fixtures import diagnostic, replace_artifact
    samples = json.loads(Path('docs/evidence/M0/examples.json').read_text())
    store = ArtifactStore(tmp_path.resolve(), role=ActorRole.CONTROLLER)
    task_ref = replace_artifact(store, diagnostic(store), partition='train')
    task = store.get_artifact(task_ref)
    policy = c.PolicyConfig.model_validate_json(json.dumps(samples['RolloutRecord']['policy']))
    plan = BalancedSampler([TaskSlot(task_ref.sha256, task.repository_family, 'feature')], seed=2).next_group(policy.policy_version)
    runtime = object.__new__(EnvironmentRuntime)
    runtime.store, runtime.policy = store, runtime_policy()
    grader = GradingService(store=store, runtime=runtime, revision='a'*40)
    gate = TrainingDataGate(store=store, admit=lambda ref: task, grader_revision='a'*40)
    rejected = store.put_bytes(b'{"version":"forged","reward":1}', 'm4-submission', c.Visibility.PRIVATE)
    missing = rejected.model_copy(update={'sha256':'f'*64})
    records = []
    for n in range(4):
        submission = rejected if n % 2 == 0 else missing
        result = grader.grade(task_ref, submission, plan.case_seed)
        receipt = read_grade(store, result.artifacts[0])
        measured = receipt.reward is not None
        assert result.evidence[0].scope == 'source_inspection'
        assert receipt.disposition == (c.Disposition.REJECTED if measured else c.Disposition.INFRASTRUCTURE)
        value = deepcopy(samples['RolloutRecord'])
        value.update(run_id='diagnostic-'+str(n), task=task_ref.model_dump(mode='json'), reward=receipt.reward,
            steps=[], training_eligible=measured, costs=[x.model_dump(mode='json') for x in result.costs],
            stopping_reason='candidate_failure' if measured else 'infrastructure_failure',
            disposition=receipt.disposition.value, submission=submission.model_dump(mode='json'),
            grading_evidence=[e.model_dump(mode='json') for e in result.evidence])
        value['policy']['seed'] = plan.episode_seeds[n]
        value['seeds'] = dict(algorithm='m7-group-v1',seeds=[plan.case_seed],same_cases_within_group=True)
        if measured:
            value['steps'] = [dict(index=0,action=submission.model_dump(mode='json'),observation=submission.model_dump(mode='json'),
                token_trace=dict(context_token_ids=[1,2],sampled_token_ids=[3],behavior_log_probabilities=[-1.],
                                 assistant_loss_mask=[True],policy_version=policy.policy_version),
                evidence=value['grading_evidence'])]
        records.append(c.RolloutRecord.model_validate_json(json.dumps(value)))
    kwargs = dict(contexts=(((1,2),), (), ((1,2),), ()), expected_policy=policy, vocab_size=8,max_seq_len=8)
    return gate, plan, tuple(records), kwargs


def test_group_preparation_accepts_actual_source_rejection_and_authenticated_infra(tmp_path):
    gate, plan, records, kwargs = actual_group_fixture(tmp_path)
    prepared = gate.prepare_group(plan, records, **kwargs)
    assert prepared.rewards == (0,None,0,None) and prepared.effective_size == 2
    assert len(prepared.optimization_turns()) == 0 and prepared.advantages == (0.,None,0.,None)
    assert prepared.records == records  # Original costs, scopes and excluded peers retained.
    assert all(r.costs for r in prepared.records)
    assert all(r.grading_evidence[0].scope == 'source_inspection' for r in prepared.records)
    bad = records[0].model_copy(update={'policy': records[0].policy.model_copy(update={'seed':1})})
    with pytest.raises(ValueError,match='seed'):
        gate.prepare_group(plan,(bad,*records[1:]),**kwargs)


def test_null_candidate_failure_and_unestablished_infra_cannot_disappear(tmp_path):
    from feature_rl import contracts as c
    gate, plan, records, kwargs = actual_group_fixture(tmp_path)
    for updates in (
        {'disposition':c.Disposition.REJECTED, 'stopping_reason':c.StopReason.CANDIDATE_FAILURE, 'grading_evidence':()},
        {'grading_evidence':()},
    ):
        bad = records[1].model_copy(update=updates)
        with pytest.raises(ValueError, match='outcome|receipt'):
            gate.prepare_group(plan, (records[0],bad,*records[2:]), **kwargs)


@pytest.mark.parametrize('tamper', ['scope','promoted_scope','revision','command','recorded_at','disposition','seed','task','submission'])
def test_group_receipt_binding_rejects_tampering(tmp_path, tamper):
    from datetime import timedelta
    from feature_rl import contracts as c
    from feature_rl.artifacts import canonical_json
    from feature_rl.grading.service import read_grade
    gate, plan, records, kwargs = actual_group_fixture(tmp_path)
    record = records[0]
    ev = record.grading_evidence[0]
    if tamper in {'scope','promoted_scope','revision','command','recorded_at'}:
        change = {'scope':'unit_diagnostic','promoted_scope':'real_integration','revision':'b'*40,'command':('different',),
                  'recorded_at':ev.recorded_at+timedelta(seconds=1)}[tamper]
        ev = ev.model_copy(update={'scope' if tamper == 'promoted_scope' else tamper:change})
    else:
        receipt = read_grade(gate.store, ev.artifacts[0])
        field = 'case_seed' if tamper == 'seed' else tamper
        change = {'disposition':c.Disposition.SUCCESS, 'seed':plan.case_seed+1,
                  'task':records[1].submission, 'submission':records[1].submission}[tamper]
        receipt = receipt.model_copy(update={field:change})
        ref = gate.store.put_bytes(canonical_json(receipt.model_dump(mode='json')), 'm4-grade-receipt', c.Visibility.PRIVATE)
        ev = ev.model_copy(update={'artifacts':(ref,)})
    bad = record.model_copy(update={'grading_evidence':(ev,)})
    with pytest.raises(ValueError):
        gate.prepare_group(plan, (bad,*records[1:]), **kwargs)
