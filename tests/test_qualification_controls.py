"""Small qualification gates: real behavior, no exact mutant isolation."""
from types import SimpleNamespace
import pytest
from feature_rl import contracts as c
from feature_rl.grading import AssertionResult, CaseResult


def checked():
    feature=SimpleNamespace(requirement_id='feature',mandatory=True)
    compatibility=SimpleNamespace(requirement_id='compat',mandatory=True)
    return SimpleNamespace(contract=SimpleNamespace(requirements=(feature,),compatibility_obligations=(compatibility,)))


def receipt(*,failed=(),feature_status='completed',reward=0):
    ref=c.ArtifactRef(sha256='a'*64,kind='unit-diagnostic',schema_version=1,visibility=c.Visibility.PRIVATE,encoding='bytes')
    cases=[]
    for key in ('feature','compat'):
        status=feature_status if key=='feature' else 'completed'
        assertions=(AssertionResult(assertion_id=key,requirement_ids=(key,),passed=key not in failed),) if status=='completed' else ()
        cases.append(CaseResult(case_id=key,mandatory=True,status=status,passed=key not in failed if status=='completed' else None,
            assertions=assertions,evidence=ref,reason='Synthetic observation'))
    return SimpleNamespace(cases=tuple(cases),reward=reward,cleanup_verified=True,build_evidence=ref,
        disposition=c.Disposition.SUCCESS if reward else c.Disposition.REJECTED,reason='required comparison failed')


def test_baseline_may_lack_the_new_api_while_existing_compatibility_runs():
    from feature_rl.qualification import assess_outcome
    state=checked();state.comparisons=(SimpleNamespace(assertions=(SimpleNamespace(requirement_ids=('feature',)),)),
        SimpleNamespace(assertions=(SimpleNamespace(requirement_ids=('compat',)),)))
    observed=receipt(feature_status='candidate_failure')
    assert assess_outcome(state,observed,'baseline_health',()).passed
    assert assess_outcome(state,observed,'baseline_absence',('feature',)).passed
    assert not assess_outcome(state,receipt(failed=('compat',)),'baseline_health',()).passed


def test_wrong_implementation_may_fail_multiple_requirements():
    from feature_rl.qualification import assess_outcome
    assert assess_outcome(checked(),receipt(failed=('feature','compat')),'negative',('feature',)).passed


def test_baseline_without_compatibility_allows_new_feature_cases_to_fail():
    from feature_rl.qualification import assess_outcome
    state=checked();state.contract.compatibility_obligations=()
    observed=receipt(failed=('feature','compat'))
    assert assess_outcome(state,observed,'baseline_health',()).passed


def test_baseline_can_lack_every_new_api_when_build_and_execution_are_healthy():
    from feature_rl.qualification import assess_outcome
    state=checked();state.contract.compatibility_obligations=()
    state.comparisons=(SimpleNamespace(assertions=(SimpleNamespace(requirement_ids=('feature',)),)),)
    observed=receipt(feature_status='candidate_failure')
    observed.cases=observed.cases[:1]
    assert assess_outcome(state,observed,'baseline_health',()).passed
    assert assess_outcome(state,observed,'baseline_absence',('feature',)).passed
    observed.build_evidence=None
    assert not assess_outcome(state,observed,'baseline_health',()).passed


@pytest.mark.parametrize('status',['candidate_failure','protocol_failure'])
def test_wrong_implementation_observed_candidate_failure_is_not_a_full_reward(status):
    from feature_rl.qualification import assess_outcome
    assert assess_outcome(checked(),receipt(feature_status=status),'negative',('feature',)).passed


@pytest.mark.parametrize('mode',['positive','negative','baseline_absence','baseline_health'])
def test_infrastructure_and_unclean_runs_never_count(mode):
    from feature_rl.qualification import assess_outcome
    observed=receipt(failed=('feature',));observed.disposition=c.Disposition.INFRASTRUCTURE;observed.reward=None
    assert not assess_outcome(checked(),observed,mode,('feature',)).passed
    observed=receipt(failed=('feature',));observed.cleanup_verified=False
    assert not assess_outcome(checked(),observed,mode,('feature',)).passed


def test_full_reward_wrong_implementation_and_failing_gold_reject():
    from feature_rl.qualification import assess_outcome
    assert assess_outcome(checked(),receipt(reward=1),'negative',('feature',)).code=='false_acceptance'
    assert assess_outcome(checked(),receipt(failed=('feature',)),'positive',()).code=='false_rejection'
    assert assess_outcome(checked(),receipt(reward=1),'positive',()).passed


def test_not_run_does_not_prove_missing_feature_or_a_working_baseline():
    from feature_rl.qualification import assess_outcome
    assert not assess_outcome(checked(),receipt(feature_status='not_run'),'baseline_absence',('feature',)).passed


def test_fixed_schedule_runs_each_wrong_once_and_repeats_gold_at_same_seed(tmp_path):
    from test_qualification_service import service
    from m5_fixtures import task_fixture
    from feature_rl.verifiers import load_verifier
    from feature_rl.qualification import derive_reference
    q=service(tmp_path);task=task_fixture(q.store);loaded=load_verifier(q.store,task)
    controls=wrong_sources(q,loaded)
    state=SimpleNamespace(task=loaded.task,contract=loaded.contract,verifier=SimpleNamespace(controls=controls))
    projection=derive_reference(q.store,task,q.grader.submissions.policy)
    runs=q._runs(state,projection)
    assert [run[0] for run in runs]==['baseline_absence','fresh_0','control_partial','control_happy_path','control_hardcoded','reset_0']
    assert all(run[2]==q.policy.seed for run in runs)
    assert runs[-1][1]==runs[1][1] and runs[-1][-1] is True


def wrong_sources(q,loaded):
    from feature_rl.environments import SourceArchive, SourceFile
    return tuple(SimpleNamespace(control_id=category,category=category,requirement_ids=('echo',),
        patch=q.grader.submissions.create(loaded.task.baseline,
            SourceArchive({'src/click/__init__.py':SourceFile(('value = '+str(index)+'\n').encode(),False)}).to_tar(),
            (),loaded.contract.allowed_changes))
        for index,category in enumerate(('partial','happy_path','hardcoded')))


@pytest.mark.parametrize('defect',['same_patch','different_manifest_same_source','baseline_noop'])
def test_wrong_implementations_must_change_baseline_and_have_distinct_sources(tmp_path,defect):
    from test_qualification_service import service
    from m5_fixtures import task_fixture
    from feature_rl.verifiers import load_verifier
    from feature_rl.environments import SourceArchive
    from feature_rl.qualification import derive_reference, QualificationRejected
    q=service(tmp_path);task=task_fixture(q.store);loaded=load_verifier(q.store,task)
    controls=list(wrong_sources(q,loaded))
    if defect=='same_patch':controls[1].patch=controls[0].patch
    elif defect=='different_manifest_same_source':
        controls[1].patch=q.store.put_bytes(b'\n'+q.store.get_bytes(controls[0].patch),'m4-submission',c.Visibility.PRIVATE)
        assert controls[1].patch!=controls[0].patch
    else:
        baseline=q.grader.submissions.source(loaded.task.baseline)
        unchanged=SourceArchive({'src/click/__init__.py':baseline.files['src/click/__init__.py']}).to_tar()
        controls[0].patch=q.grader.submissions.create(loaded.task.baseline,unchanged,(),loaded.contract.allowed_changes)
    state=SimpleNamespace(task=loaded.task,contract=loaded.contract,verifier=SimpleNamespace(controls=tuple(controls)))
    projection=derive_reference(q.store,task,q.grader.submissions.policy)
    with pytest.raises(QualificationRejected,match='distinct|baseline'):
        q._runs(state,projection)
