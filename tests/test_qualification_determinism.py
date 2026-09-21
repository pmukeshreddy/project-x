"""Retained typed receipts and real observation replay; no worker execution."""
import base64
import hashlib
import json
from datetime import datetime, timezone

import pytest

from feature_rl import contracts as c
from feature_rl.artifacts import canonical_json
from feature_rl.environments.models import Ownership
from feature_rl.grading import AssertionResult, CaseResult, GradeReceipt, read_grade
from feature_rl.grading.bootstrap import recipe_adapter_argv
from feature_rl.qualification import QualificationRejected
from feature_rl.qualification.evidence import put_record, _assert_case_observation
from feature_rl.verifiers import load_verifier, materialize_manifest
from m4_fixtures import replace_artifact
from m5_fixtures import task_fixture
from test_qualification_service import service


def observed_receipt(q, task, *, output=b'ok first', stderr=b'', exit_code=0, seed=11, operation='1', mode='process', duplicate=False):
    """Both differing values genuinely satisfy the retained contains comparison."""
    checked = load_verifier(q.store, task)
    manifest = materialize_manifest(checked, seed)
    refs = []; cases = []
    for index, (case, comparison) in enumerate(zip(manifest.cases, checked.comparisons)):
        stdout = output if mode == 'process' else canonical_json({'case_id': case.case_id,
            'observations': {'stdout': output.decode()}})
        stdin = canonical_json({'case_id': case.case_id, 'inputs': case.model_dump(mode='json')['inputs']})
        owner = Ownership(operation_id=operation*31+str(index), owner_token=operation*32,
            daemon_id='synthetic', container_name='synthetic-'+operation+str(index),
            container_id='container-'+operation+str(index), phase='removed', binding={}, saved_source={},
            created_at='2026-09-20T00:00:00Z')
        row = {'argv': ['diagnostic-docker', 'exec', owner.container_id, *recipe_adapter_argv(checked)],
            'exit_code': exit_code, 'reason': 'exited', 'stdin_bytes': len(stdin),
            'stdin_sha256': hashlib.sha256(stdin).hexdigest(), 'stdout_b64': base64.b64encode(stdout).decode(),
            'stderr_b64': base64.b64encode(stderr).decode(), 'wall_seconds': int(operation)}
        raw = {'phase': 'execute', 'record': owner.model_dump(mode='json'), 'cleanup_verified': True,
            'commands': [row, row] if duplicate else [row],
            'extra': {'error': None, 'failure_category': 'candidate' if exit_code else 'none',
                      'reason': 'command_failed' if exit_code else 'completed'}}
        ref = put_record(q.store, raw, 'environment-execution'); refs.append(ref)
        passed = b'ok' in output
        actual = CaseResult(case_id=case.case_id, mandatory=case.mandatory, status='completed', passed=passed,
            assertions=(AssertionResult(assertion_id='contains', requirement_ids=('echo',), passed=passed),),
            evidence=ref, reason='Synthetic retained observation; no worker execution')
        if not duplicate:
            _assert_case_observation(checked, actual, case, comparison, raw, owner)
        cases.append(actual)
    marker = checked.task.provenance.evidence[0].artifacts[0]
    receipt = GradeReceipt(version='m4-grade-v1', task=task, submission=marker, verifier=checked.task.private_oracle,
        case_seed=seed, manifest=put_record(q.store, manifest, 'm4-case-manifest'), source=checked.task.baseline,
        disposition=c.Disposition.SUCCESS if passed else c.Disposition.REJECTED, reward=int(passed), reason='Synthetic parser diagnostic, not a qualified task',
        expected_case_ids=tuple(case.case_id for case in cases), cases=tuple(cases), build_evidence=marker,
        runtime_evidence=(marker, *refs), cleanup_verified=True, implementation_revision=q.grader.revision,
        recorded_at=datetime(2026, 9, 20, tzinfo=timezone.utc))
    return checked, read_grade(q.store, put_record(q.store, receipt, 'm4-grade-receipt'))


def fixture(tmp_path, *, mode='process'):
    q = service(tmp_path); task = task_fixture(q.store); checked = load_verifier(q.store, task)
    cases = []
    for case, comparison in zip(checked.verifier.cases, checked.comparisons):
        value = comparison.model_dump(mode='json')
        value.update(mode=mode, observations=[{'name': 'stdout', 'type': 'string'}], assertions=[{
            'assertion_id': 'contains', 'requirement_ids': ['echo'],
            'oracle_origin': comparison.assertions[0].oracle_origin.model_dump(mode='json'),
            'actual': 'stdout', 'operator': 'contains', 'expected': {'kind': 'literal', 'value': 'ok'}}])
        ref = put_record(q.store, value, 'm4-case-comparison')
        cases.append(case.model_copy(update={'comparison': ref}))
    verifier = replace_artifact(q.store, checked.task.private_oracle,
        cases=[case.model_dump(mode='json') for case in cases],
        worker_adapter=checked.verifier.worker_adapter.model_copy(update={'supported_observables': (mode,)}))
    return q, replace_artifact(q.store, task, private_oracle=verifier)


@pytest.mark.parametrize('change', [{'output': b'ok second'}, {'stderr': b'changed warning'}, {'exit_code': 3}])
def test_same_seed_changed_output_rejects_even_when_both_grades_pass(tmp_path, change):
    from feature_rl.qualification.evidence import assert_reference_determinism
    q, task = fixture(tmp_path)
    checked, first = observed_receipt(q, task)
    _, second = observed_receipt(q, task, operation='2', **change)
    assert first.reward == second.reward == 1
    seen = {}; assert_reference_determinism(q.store, checked, first, seen)
    with pytest.raises(QualificationRejected, match='flaky_task'):
        assert_reference_determinism(q.store, checked, second, seen)


def test_same_seed_json_observations_are_compared_beyond_verdicts(tmp_path):
    from feature_rl.qualification.evidence import assert_reference_determinism
    q, task = fixture(tmp_path, mode='json')
    checked, first = observed_receipt(q, task, mode='json')
    _, second = observed_receipt(q, task, mode='json', operation='2', output=b'ok different')
    seen = {}; assert_reference_determinism(q.store, checked, first, seen)
    with pytest.raises(QualificationRejected, match='flaky_task'):
        assert_reference_determinism(q.store, checked, second, seen)


def test_repeated_raw_observations_ignore_controller_identity_and_metrics(tmp_path):
    from feature_rl.qualification.evidence import assert_reference_determinism
    q, task = fixture(tmp_path)
    checked, first = observed_receipt(q, task)
    _, reset = observed_receipt(q, task, operation='2')
    seen = {}; assert_reference_determinism(q.store, checked, first, seen)
    assert_reference_determinism(q.store, checked, reset, seen)
    _, other_seed = observed_receipt(q, task, seed=23, operation='3', output=b'ok other seed')
    assert_reference_determinism(q.store, checked, other_seed, seen)


def test_determinism_requires_unique_retained_adapter_execution(tmp_path):
    from feature_rl.qualification.evidence import assert_reference_determinism
    q, task = fixture(tmp_path)
    checked, receipt = observed_receipt(q, task, duplicate=True)
    with pytest.raises(QualificationRejected, match='invalid_evidence'):
        assert_reference_determinism(q.store, checked, receipt, {})


@pytest.mark.parametrize('tamper', ['legacy', 'startup_site', 'different_paths', 'missing_owner'])
def test_observation_replay_requires_exact_current_bootstrap_and_profile_paths(tmp_path, tamper):
    q, task = fixture(tmp_path)
    checked, receipt = observed_receipt(q, task)
    actual = receipt.cases[0]
    value = json.loads(q.store.get_bytes(actual.evidence))
    row = value['commands'][0]
    if tamper == 'legacy':
        row['argv'] = row['argv'][:3] + ['/usr/local/bin/python', '-c', checked.adapter.decode()]
    elif tamper == 'startup_site':
        row['argv'].remove('-S')
    elif tamper == 'different_paths':
        row['argv'][-1] = '[]'
    else:
        row['argv'] = list(recipe_adapter_argv(checked))
    owner = Ownership.model_validate_json(canonical_json(value['record']))
    manifest = materialize_manifest(checked, receipt.case_seed)
    with pytest.raises(QualificationRejected, match='invalid_evidence'):
        _assert_case_observation(checked, actual, manifest.cases[0], checked.comparisons[0], value, owner)


@pytest.mark.parametrize('changed_run', ['fresh_1', 'reset_0'])
def test_actual_qualification_orchestration_records_output_flake(tmp_path, monkeypatch, changed_run):
    from types import SimpleNamespace
    from feature_rl.qualification import QualificationPolicy

    q, task = fixture(tmp_path)
    q.policy = QualificationPolicy(fresh_seeds=(11, 11, 11, 23, 47), reset_seeds=(11, 11, 11, 23, 47))
    q.builder = SimpleNamespace(solver_package=lambda task: None)
    monkeypatch.setattr(q.grader, 'select_task', lambda checked: None)
    monkeypatch.setattr(q, '_history', lambda checked: (0, None))
    monkeypatch.setattr(q, '_publish_qualification', lambda claim, frozen: frozen)
    dispatched = []

    def retained_run(checked, projection, submission, seed, name, mode, targets, claim, reset, seen):
        dispatched.append(name)
        _, receipt = observed_receipt(q, task, seed=seed,
            output=b'missing' if name=='baseline_absence' else b'ok changed' if name==changed_run else b'ok first')
        return receipt.manifest, SimpleNamespace(costs=()), receipt

    # Substitute only the worker/Registry run boundary. _execute still consumes
    # typed retained receipts through the real observation and determinism code.
    monkeypatch.setattr(q, '_run', retained_run)
    claim = q._claim(q._job(task, 'm5-qualify'))
    frozen = q._execute(task, claim)
    assert any(issue.startswith('flaky_task: repeated reference seed 11') for issue in frozen['issues'])
    assert frozen['assessments'][changed_run].passed  # The comparison itself passed.
    assert dispatched[-1] == changed_run
