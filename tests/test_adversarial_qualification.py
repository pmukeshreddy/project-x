"""Control-plan and replay-schedule checks, without claiming worker execution."""
from types import SimpleNamespace
from dataclasses import replace

import pytest

from feature_rl.environments import SourceArchive
from feature_rl.qualification import ControlDiagnosis, QualificationPolicy, QualificationRejected
from feature_rl.qualification.admission import expected_runs
from feature_rl.qualification.controls import validate_control_plan
from feature_rl.verifiers import load_verifier
from m5_fixtures import task_fixture
from test_qualification_service import service


def planned_control(tmp_path, *, generated=False, reason=None, policy=None):
    from feature_rl.qualification.attacks import attack_expected_reason
    q = service(tmp_path)
    q.policy = policy or QualificationPolicy(fresh_seeds=(11, 23, 47), reset_seeds=(47, 89, 11))
    checked = load_verifier(q.store, task_fixture(q.store))
    patch = q.grader.submissions.create(checked.task.baseline, SourceArchive({}).to_tar(), (),
                                       checked.contract.allowed_changes)
    control = SimpleNamespace(control_id='spoof', category='adversarial', patch=patch,
        requirement_ids=('echo',), expected_valid=False, author_provenance=checked.task.provenance,
        expected_reason=reason if reason is not None else attack_expected_reason('observation_spoofing', ('echo',)))
    checked = replace(checked, verifier=SimpleNamespace(controls=(control,)))
    diagnosis = ControlDiagnosis(control_id='spoof', validity='invalid', mode='semantic_negative',
        targets=('echo',), attack='observation_spoofing', evidence=checked.task.provenance.evidence,
        note='Synthetic schedule diagnostic, not a demonstrated attack.')
    if not generated:
        q.policy = q.policy.model_copy(update={'controls': (diagnosis,)})
    return q, checked, SimpleNamespace(submission=patch), diagnosis


def test_observation_spoofing_remains_a_supported_optional_authoring_slot():
    from feature_rl.pipeline.authoring_models import ControlSlot
    from feature_rl.qualification.controls import ATTACKS
    slot = ControlSlot(category='adversarial', requirement_ids=('echo',), attack='observation_spoofing')
    assert slot.attack in ATTACKS


@pytest.mark.parametrize('generated', [False, True])
def test_control_replay_requires_every_distinct_fresh_and_reset_seed(tmp_path, generated):
    q, checked, projection, diagnosis = planned_control(tmp_path, generated=generated)
    runs, _ = expected_runs(q, checked, projection, generated=(diagnosis,) if generated else ())
    controls = [run for run in runs if run[0].startswith('control_')]
    assert [run[2] for run in controls] == [11, 23, 47, 89]
    assert len({run[0] for run in controls}) == 4
    assert [run[3] for run in controls] == ([None, 'semantic_negative', 'semantic_negative', 'semantic_negative']
                                           if generated else ['semantic_negative'] * 4)


def test_generic_named_attack_cannot_satisfy_specific_spoofing_coverage(tmp_path):
    q, checked, _, _ = planned_control(tmp_path, reason='Keep the program runnable while exposing observation_spoofing')
    with pytest.raises(QualificationRejected, match='attack specification'):
        validate_control_plan(checked, q.policy)


@pytest.mark.parametrize('mode', ['resource_failure', 'source_rejection'])
def test_spoofing_coverage_cannot_be_claimed_from_unrelated_rejection(tmp_path, mode):
    q, checked, _, diagnosis = planned_control(tmp_path)
    q.policy = q.policy.model_copy(update={'controls': (diagnosis.model_copy(update={'mode': mode}),)})
    with pytest.raises(QualificationRejected, match='attack rejection mechanism'):
        validate_control_plan(checked, q.policy)


@pytest.mark.parametrize('has_compatibility', [False, True])
def test_regression_category_is_required_only_for_mandatory_compatibility(tmp_path, has_compatibility):
    q, checked, _, _ = planned_control(tmp_path)
    if has_compatibility:
        obligation = checked.contract.requirements[0].model_copy(update={'requirement_id': 'preserved'})
        checked = replace(checked, contract=checked.contract.model_copy(update={'compatibility_obligations': (obligation,)}))
    missing, _ = validate_control_plan(checked, q.policy)
    assert ('missing control category: regression' in missing) is has_compatibility


def test_supplied_semantic_attack_diagnosis_cannot_change_declared_targets(tmp_path):
    q, checked, _, diagnosis = planned_control(tmp_path)
    obligation = checked.contract.requirements[0].model_copy(update={'requirement_id': 'preserved'})
    checked = replace(checked, contract=checked.contract.model_copy(update={'compatibility_obligations': (obligation,)}))
    q.policy = q.policy.model_copy(update={'controls': (diagnosis.model_copy(update={'targets': ('preserved',)}),)})
    with pytest.raises(QualificationRejected, match='exact declared requirement targets'):
        validate_control_plan(checked, q.policy)


def test_one_repeated_seed_does_not_establish_seed_diversity(tmp_path):
    q, checked, _, _ = planned_control(tmp_path,
        policy=QualificationPolicy(fresh_seeds=(11, 11, 11), reset_seeds=(11, 11, 11)))
    missing, _ = validate_control_plan(checked, q.policy)
    assert 'at least three distinct qualification seeds are required for control coverage' in missing


def test_default_policy_tests_three_distinct_seeds_and_repeats_them_after_reset():
    policy = QualificationPolicy()
    assert len(set(policy.fresh_seeds)) >= 3
    assert set(policy.fresh_seeds) == set(policy.reset_seeds)


@pytest.mark.parametrize('attack', ['observation_spoofing', 'evaluator_detection'])
def test_adversarial_dispatch_binds_attack_specification_and_actual_prompt(tmp_path, monkeypatch, attack):
    from feature_rl.requirements import GenerationCandidate
    from feature_rl.pipeline.authoring_models import AuthoringCall, ControlPlan, ControlSlot
    from feature_rl.qualification.attacks import attack_expected_reason
    from feature_rl.verifiers import ControlFinalizationInputs, build_control_request
    from feature_rl.requirements import AuthoringEvidenceResolver
    from test_factory_authoring import setup

    factory, _, base, runner = setup(tmp_path, monkeypatch)
    inputs = ControlFinalizationInputs(control_id='ATTACK', category='adversarial', requirement_ids=('echo',),
        expected_valid=False, expected_reason=attack_expected_reason(attack, ('echo',)),
        baseline=base.inputs.baseline, contract=base.inputs.contract, environment=base.inputs.environment,
        scenario_plan=base.inputs.scenario_plan, provenance=base.inputs.provenance, costs=base.inputs.costs)
    request = build_control_request(request_id='ATTACK', response_id='ATTACK_RESPONSE', prompt_id='ATTACK_PROMPT',
        store=factory.store, resolver=AuthoringEvidenceResolver(store=factory.store, **base.resolver.model_dump()),
        inputs=inputs, sources=base.sources, limits=base.generation.request.limits)
    call = base.model_copy(update={'inputs': inputs, 'generation': GenerationCandidate(request=request),
        'control_plan': ControlPlan(contract=inputs.contract,
            slots=(ControlSlot(category='adversarial', requirement_ids=('echo',), attack=attack),)), 'attack': attack})
    assert AuthoringCall.model_validate(call.model_dump()) == call
    changed = call.model_copy(update={'inputs': inputs.model_copy(update={'expected_reason': 'Generic named attack'})})
    with pytest.raises(ValueError, match='attack specification'):
        AuthoringCall.model_validate(changed.model_dump())
    changed = call.model_copy(update={'generation': GenerationCandidate(request=request.model_copy(
        update={'instruction': 'The concrete attack instruction was removed.'}))})
    with pytest.raises(ValueError, match='attack specification'):
        AuthoringCall.model_validate(changed.model_dump())
    assert not runner.calls  # Validation only; no authoring model or worker is executed.


@pytest.mark.parametrize(('generated', 'failed_at'), [
    (generated, failed_at) for generated in (False, True)
    for failed_at in (None, 'baseline_absence', 'fresh_0', 'control_first', 'control_later')
] + [(True, 'control_unresolved')])
def test_failed_gate_stops_dispatch_but_passing_sequence_matches_full_replay(tmp_path, monkeypatch, generated, failed_at):
    from datetime import datetime, timezone
    from feature_rl import contracts as c
    from feature_rl.grading import AssertionResult, CaseResult
    from feature_rl.qualification import derive_reference
    from feature_rl.qualification.controls import assess_outcome
    import feature_rl.qualification.service as execution

    q, checked, _, diagnosis = planned_control(tmp_path, generated=generated)
    projection = derive_reference(q.store, checked.task_ref, q.grader.submissions.policy)
    q.builder = SimpleNamespace(solver_package=lambda task: None)
    monkeypatch.setattr(q.grader, 'select_task', lambda task: None)
    monkeypatch.setattr(q, '_history', lambda task: (0, None))
    monkeypatch.setattr(execution, 'load_verifier', lambda store, task: checked)
    monkeypatch.setattr(execution, 'control_origins', lambda service, task: {})
    monkeypatch.setattr(execution, 'assert_reference_determinism', lambda *args: None)
    # Isolate scheduling from author provenance and physical worker execution.
    # The ordinary comparison-outcome gate remains real, including false passes.
    if failed_at != 'control_unresolved':
        monkeypatch.setattr(execution, 'diagnose_control', lambda service, task, control, receipt, binding, origins:
            (diagnosis, assess_outcome(task, receipt, diagnosis.mode, diagnosis.targets, store=service.store)))
    monkeypatch.setattr(q, '_publish_qualification', lambda claim, frozen: frozen)
    calls = []
    marker = checked.task.provenance.evidence[0].artifacts[0]
    from feature_rl.qualification.schedule import control_run_name
    failed_name = {'control_first': control_run_name('spoof', 0),
                   'control_unresolved': control_run_name('spoof', 0),
                   'control_later': control_run_name('spoof', 3)}.get(failed_at, failed_at)
    cost = checked.task.costs[0]

    def run(task, projection_ref, submission, seed, name, mode, targets, claim, reset, seen):
        calls.append((name, submission, seed, mode, targets, reset))
        passed = name.startswith(('fresh_', 'reset_'))
        if name == failed_name and failed_at != 'control_unresolved':passed = not passed
        cases = tuple(CaseResult(case_id='case'+str(i), mandatory=True, status='completed', passed=passed,
            assertions=(AssertionResult(assertion_id='echo', requirement_ids=('echo',), passed=passed),),
            evidence=marker, reason='Synthetic schedule observation; no worker execution')
            for i in range(len(checked.comparisons)))
        receipt = SimpleNamespace(cases=cases, reward=int(passed), cleanup_verified=True, build_evidence=marker,
            disposition=c.Disposition.SUCCESS if passed else c.Disposition.REJECTED,
            recorded_at=datetime.now(timezone.utc),
            reason='Synthetic schedule observation; no worker execution')
        return marker, SimpleNamespace(costs=(cost,)), receipt

    monkeypatch.setattr(q, '_run', run)
    claim = q._claim(q._job(checked.task_ref, 'm5-qualify'))
    frozen = q._execute(checked.task_ref, claim)
    expected, _ = expected_runs(q, checked, projection, generated=(diagnosis,) if generated else ())
    cutoff = len(expected) if failed_name is None else next(i+1 for i, run in enumerate(expected) if run[0] == failed_name)
    assert tuple(calls) == expected[:cutoff]
    assert frozen['costs'] == (cost,) * cutoff
    assert len(frozen['bindings']) == cutoff
    if failed_name is not None:
        assert not frozen['assessments'][failed_name].passed
        assert any(failed_name in issue for issue in frozen['issues'])
    else:
        assert all(gate.passed for gate in frozen['assessments'].values())
    controls_ran = any(run[0].startswith('control_') for run in calls)
    if failed_at == 'control_unresolved':
        assert len(frozen['control_diagnoses']) == 1
        assert frozen['control_diagnoses'][0].validity == 'unresolved'
        assert frozen['control_diagnoses'][0].evidence[0].artifacts[0] == marker
        assert any(issue.startswith('provisional: '+failed_name) for issue in frozen['issues'])
    else:
        assert frozen['control_diagnoses'] == ((diagnosis,) if generated and controls_ran else ())
