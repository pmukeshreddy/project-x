"""Inert diagnostic artifact fixtures; admissions here are explicit test doubles."""
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


def test_deterministic_supervised_targets_never_accept_behavior_probabilities():
    from feature_rl.training.data import tokenize_supervision
    # The test tokenizer exposes exact deterministic IDs without invoking a model.
    encode = lambda text: tuple(ord(c) for c in text)
    turn = tokenize_supervision('Context', 'Target', encode_context=encode, encode_target=encode,
                               max_seq_len=100)
    assert turn.context == encode('Context') and turn.targets == encode('Target')
    assert turn.behavior is None and turn.advantage is None
    with pytest.raises(ValueError):
        tokenize_supervision('Context', '', encode_context=encode, encode_target=encode, max_seq_len=100)


def test_supervised_source_is_loaded_from_exact_grade_and_prefix_checked(tmp_path):
    from datetime import datetime, timezone
    from feature_rl import contracts as c
    from feature_rl.artifacts import canonical_json
    from feature_rl.environments import SourceArchive, SourceFile
    from feature_rl.grading.models import GradeReceipt, CaseResult, AssertionResult
    from feature_rl.training.data import TrainingDataGate
    samples = json.loads(Path('docs/evidence/M0/examples.json').read_text())
    store = ArtifactStore(tmp_path, role=ActorRole.CONTROLLER)
    task = TaskBundle.model_validate_json(json.dumps(samples['TaskBundle']))
    task_ref = store.put_artifact(task)
    source = store.put_bytes(SourceArchive({'src/file.py': SourceFile(b'answer = 1\n', False)}).to_tar(), 'source-archive', c.Visibility.PRIVATE)
    submission = store.put_bytes(b'unit diagnostic submission', 'm4-submission', c.Visibility.PRIVATE)
    receipt = GradeReceipt(version='m4-grade-v1',task=task_ref,submission=submission,verifier=task.private_oracle,
        case_seed=1,manifest=source,source=source,disposition=c.Disposition.SUCCESS,reward=1,
        reason='DIAGNOSTIC fixture only',expected_case_ids=('case',),cases=(CaseResult(case_id='case',mandatory=True,
        status='completed',passed=True,assertions=(AssertionResult(assertion_id='a',requirement_ids=('r',),passed=True),),
        evidence=source,reason='DIAGNOSTIC fixture only'),),build_evidence=source,runtime_evidence=(source,),
        cleanup_verified=True,implementation_revision='a'*40,recorded_at=datetime.now(timezone.utc))
    grade_ref = store.put_bytes(canonical_json(receipt.model_dump(mode='json')), 'm4-grade-receipt', c.Visibility.PRIVATE)
    gate = TrainingDataGate(store=store,admit=lambda ref: task,grader_revision='a'*40)
    render = lambda task, archive: [('Context', archive.files['src/file.py'].data.decode())]
    encode = lambda text: tuple(ord(c) for c in text)
    turns = gate.prepare_supervised_source(task=task_ref,submission=submission,grade_ref=grade_ref,
        render_solution=render,encode_context=encode,encode_target=encode,max_seq_len=100)
    assert turns[0].targets == encode('answer = 1\n')
    with pytest.raises(ValueError,match='prefix'):
        gate.prepare_supervised_source(task=task_ref,submission=submission,grade_ref=grade_ref,
            render_solution=render,encode_context=encode,encode_target=lambda text: (9,),max_seq_len=100)


def test_group_preparation_keeps_invalid_peers_and_rejects_seed_or_policy_drift(tmp_path):
    from copy import deepcopy
    from feature_rl import contracts as c
    from feature_rl.artifacts import canonical_json
    from feature_rl.training.core import BalancedSampler, TaskSlot
    from feature_rl.training.data import TrainingDataGate
    samples = json.loads(Path('docs/evidence/M0/examples.json').read_text())
    store = ArtifactStore(tmp_path, role=ActorRole.CONTROLLER)
    task = TaskBundle.model_validate_json(json.dumps(samples['TaskBundle']))
    task_ref = store.put_artifact(task)
    policy = c.PolicyConfig.model_validate_json(json.dumps(samples['RolloutRecord']['policy']))
    plan = BalancedSampler([TaskSlot(task_ref.sha256, task.repository_family, 'feature')], seed=2).next_group(policy.policy_version)
    gate = TrainingDataGate(store=store, admit=lambda ref: task, grader_revision='a'*40)
    records = []
    for n in range(4):
        value = deepcopy(samples['RolloutRecord'])
        value.update(run_id='diagnostic-'+str(n), task=task_ref.model_dump(mode='json'), reward=None,
                     steps=[],training_eligible=False,stopping_reason='infrastructure_failure', disposition='infrastructure_failure')
        value['policy']['seed'] = plan.episode_seeds[n]
        value['seeds'] = dict(algorithm='m7-group-v1',seeds=[plan.case_seed],same_cases_within_group=True)
        if n % 2 == 0:
            submission = store.put_bytes(b'unit diagnostic', 'm4-submission', c.Visibility.PRIVATE)
            receipt = dict(version='m4-grade-v1',task=task_ref.model_dump(mode='json'),submission=submission.model_dump(mode='json'),
                verifier=None,case_seed=plan.case_seed,manifest=None,source=None,disposition='candidate_rejection',reward=0,
                reason='synthetic diagnostic only',expected_case_ids=[],cases=[],build_evidence=None,runtime_evidence=[],
                cleanup_verified=True,implementation_revision='a'*40,recorded_at='2026-09-19T00:00:00Z')
            ref = store.put_bytes(canonical_json(receipt), 'm4-grade-receipt', c.Visibility.PRIVATE)
            # Shape-only branch fixture: this label does not assert real execution occurred.
            ev = dict(producer='feature_rl.grading',command=['diagnostic-fixture'],recorded_at='2026-09-19T00:00:00Z',
                      exit_status=0,artifacts=[ref.model_dump(mode='json')],revision='a'*40,scope='real_integration')
            value.update(reward=0,training_eligible=True,stopping_reason='candidate_failure',disposition='candidate_rejection',
                         submission=submission.model_dump(mode='json'),grading_evidence=[ev])
            value['steps'] = [dict(index=0,action=submission.model_dump(mode='json'),observation=submission.model_dump(mode='json'),
                token_trace=dict(context_token_ids=[1,2],sampled_token_ids=[3],behavior_log_probabilities=[-1.],
                                 assistant_loss_mask=[True],policy_version=policy.policy_version),evidence=[ev])]
        records.append(c.RolloutRecord.model_validate_json(json.dumps(value)))
    contexts = (((1,2),), (), ((1,2),), ())
    prepared = gate.prepare_group(plan, tuple(records), contexts=contexts, expected_policy=policy, vocab_size=8,max_seq_len=8)
    assert prepared.rewards == (0,None,0,None) and prepared.effective_size == 2
    assert len(prepared.optimization_turns()) == 2 and prepared.advantages == (0.,None,0.,None)
    bad = records[0].model_copy(update={'policy': records[0].policy.model_copy(update={'seed':1})})
    with pytest.raises(ValueError,match='seed'):
        gate.prepare_group(plan,(bad,*records[1:]),contexts=contexts,expected_policy=policy,vocab_size=8,max_seq_len=8)
