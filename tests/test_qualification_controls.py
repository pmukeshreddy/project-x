"""Synthetic semantic-gate tests; no runtime or successful task is claimed."""
from types import SimpleNamespace
import pytest
from feature_rl import contracts as c
from feature_rl.grading import AssertionResult, CaseResult


def checked():
    feature=SimpleNamespace(requirement_id='feature',mandatory=True)
    compatibility=SimpleNamespace(requirement_id='compat',mandatory=True)
    return SimpleNamespace(contract=SimpleNamespace(requirements=(feature,),compatibility_obligations=(compatibility,)),
        comparisons=(SimpleNamespace(mode="json"),SimpleNamespace(mode="json")))


def receipt(*,failed=(),status='completed',reward=0):
    ref=c.ArtifactRef(sha256='a'*64,kind='unit-diagnostic',schema_version=1,visibility=c.Visibility.PRIVATE,encoding='bytes')
    cases=[]
    for key in ('feature','compat'):
        assertions=(AssertionResult(assertion_id=key,requirement_ids=(key,),passed=key not in failed),) if status=='completed' else ()
        cases.append(CaseResult(case_id=key,mandatory=True,status=status,passed=key not in failed if status=='completed' else None,
            assertions=assertions,evidence=ref,reason='Synthetic observation'))
    return SimpleNamespace(cases=tuple(cases),reward=reward,cleanup_verified=True,build_evidence=ref,
        disposition=c.Disposition.SUCCESS if reward else c.Disposition.REJECTED,reason='required comparison failed')


def test_baseline_health_and_missing_feature_are_separate_semantic_checks():
    from feature_rl.qualification import assess_outcome
    observed=receipt(failed=('feature',))
    assert assess_outcome(checked(),observed,'baseline_health',()).passed
    assert assess_outcome(checked(),observed,'semantic_negative',('feature',)).passed
    assert not assess_outcome(checked(),receipt(failed=('compat',)),'baseline_health',()).passed


@pytest.mark.parametrize('status',['protocol_failure','candidate_failure','not_run','infrastructure_failure'])
def test_syntax_protocol_infrastructure_and_unrun_failures_are_not_semantic_omissions(status):
    from feature_rl.qualification import assess_outcome
    assert not assess_outcome(checked(),receipt(status=status),'semantic_negative',('feature',)).passed


@pytest.mark.parametrize('failed',[(),('compat',),('feature','compat')])
def test_semantic_negative_requires_exact_target_failures_and_preserved_other_obligations(failed):
    from feature_rl.qualification import assess_outcome
    assert not assess_outcome(checked(),receipt(failed=failed),'semantic_negative',('feature',)).passed


def test_known_false_pass_false_rejection_and_cleanup_have_explicit_reasons():
    from feature_rl.qualification import assess_outcome
    assert assess_outcome(checked(),receipt(reward=1),'semantic_negative',('feature',)).code=='false_acceptance'
    assert assess_outcome(checked(),receipt(failed=('feature',)),'positive',()).code=='false_rejection'
    bad=receipt(reward=1);bad.cleanup_verified=False
    assert assess_outcome(checked(),bad,'positive',()).code=='environment_failure'


def test_positive_reference_requires_complete_compared_cases():
    from feature_rl.qualification import assess_outcome
    assert assess_outcome(checked(),receipt(reward=1),'positive',()).passed
    assert not assess_outcome(checked(),receipt(status='not_run',reward=1),'positive',()).passed


def test_adversarial_rejection_does_not_hide_infrastructure_failure():
    from feature_rl.qualification import assess_outcome
    output=receipt(status='protocol_failure')
    assert assess_outcome(checked(),output,'protocol_failure',()).passed
    output.disposition=c.Disposition.INFRASTRUCTURE;output.reward=None
    assert not assess_outcome(checked(),output,'protocol_failure',()).passed


def test_repair_budget_rejects_duplicate_no_change_wrong_candidate_and_exact_caps():
    from feature_rl.qualification import RepairAttempt, RepairHistory, validate_repairs, QualificationRejected
    from datetime import datetime,timezone
    ref=lambda char:c.ArtifactRef(sha256=char*64,kind='unit-diagnostic',schema_version=1,visibility=c.Visibility.PRIVATE,encoding='bytes')
    evidence=c.EvidenceRecord(producer='synthetic diagnostic',command=('diagnostic',),recorded_at=datetime.now(timezone.utc),
        exit_status=0,artifacts=(ref('0'),),revision='a'*40,scope='unit_diagnostic')
    cost=c.CostRecord(category='repair',wall_seconds=1.0,cpu_seconds=None,gpu_seconds=None,input_tokens=None,
        output_tokens=None,human_minutes=None,usd=None,measurement='partial',note='Synthetic measured-shaped fixture, no real repair')
    first=RepairAttempt(stage='authoring',before=ref('1'),after=ref('2'),diagnosis='Synthetic',change='Synthetic change',evidence=(evidence,),costs=(cost,))
    second=first.model_copy(update={'before':ref('2'),'after':ref('3')})
    history=RepairHistory(candidate=ref('a'),complete=True,initial_evidence=(evidence,),attempts=(first,second),journal_refs=(ref('0'),))
    assert validate_repairs(history,ref('a'),())==2
    for bad in [history.model_copy(update={'candidate':ref('b')}),history.model_copy(update={'attempts':(first,first)}),
                history.model_copy(update={'attempts':(first.model_copy(update={'after':ref('1')}),)}),
                history.model_copy(update={'attempts':(first,second,second.model_copy(update={'before':ref('3'),'after':ref('4')}))})]:
        with pytest.raises(QualificationRejected):validate_repairs(bad,ref('a'),())
    assert validate_repairs(history.model_copy(update={'complete':False}),ref('a'),()) is None


@pytest.mark.parametrize('code,expected',[
    ('false_acceptance',c.Disposition.REJECTED),('false_rejection',c.Disposition.REJECTED),
    ('oracle_disagreement',c.Disposition.REJECTED),('ambiguous_requirement',c.Disposition.REJECTED),
    ('budget_exhausted',c.Disposition.REJECTED),('flaky_task',c.Disposition.INVALID),
    ('environment_failure',c.Disposition.INFRASTRUCTURE),('unsupported_semantics',c.Disposition.UNSUPPORTED),
    ('unrecoverable_history',c.Disposition.BLOCKED),('unverified_human_review',c.Disposition.PROVISIONAL)])
def test_observed_failures_do_not_become_provisional_because_human_review_is_also_missing(code,expected):
    from feature_rl.qualification.controls import disposition_for
    assert disposition_for((code+': diagnostic', 'unverified_human_review: missing'))==expected


def test_missing_review_does_not_quarantine_but_known_defect_tracks_dependents(tmp_path):
    from test_qualification_service import service
    from m5_fixtures import task_fixture
    from feature_rl.registry import QuarantinedError
    q=service(tmp_path);task=task_fixture(q.store);result=q.qualify(task)
    summary=result.artifacts[1]
    q._quarantine_defect(task,summary,'a'*64,('unverified_human_review: missing',))
    assert not q.affected_versions(task).notices
    q._quarantine_defect(task,summary,'a'*64,('false_acceptance: explicit synthetic quarantine diagnostic, no actual control claim',))
    trace=q.affected_versions(task)
    assert trace.notices[0].active and result.artifacts[0] in trace.artifacts
    with pytest.raises(QuarantinedError):q.registry.assert_usable(result.artifacts[0])
    with pytest.raises(QuarantinedError):q.qualify(task)


def test_control_plan_requires_behavioral_categories_and_mandatory_omission(tmp_path):
    from test_qualification_service import service
    from m5_fixtures import task_fixture
    from feature_rl.verifiers import load_verifier
    from feature_rl.qualification import validate_control_plan
    q=service(tmp_path);checked=load_verifier(q.store,task_fixture(q.store))
    missing,targets=validate_control_plan(checked,q.policy)
    assert targets==('echo',)
    for category in ('omission','plausible_wrong','hardcoded','alternative_positive'):
        assert 'missing control category: '+category in missing
    assert 'missing control category: regression' not in missing
    assert 'missing control category: adversarial' not in missing
    assert not any(item.startswith('missing adversarial attack:') for item in missing)
    assert 'missing targeted omission: echo' in missing


@pytest.mark.parametrize('independent', [False, True])
def test_behavioral_coverage_needs_independent_alternative_without_adversarial_controls(tmp_path, independent):
    from dataclasses import replace
    from test_qualification_service import service
    from m5_fixtures import task_fixture
    from feature_rl.verifiers import load_verifier
    from feature_rl.qualification import ControlDiagnosis, validate_control_plan
    q=service(tmp_path);loaded=load_verifier(q.store,task_fixture(q.store))
    controls=[];diagnoses=[]
    for category in ('omission','plausible_wrong','hardcoded','alternative_positive'):
        positive=category=='alternative_positive'
        targets=() if positive else ('echo',)
        controls.append(SimpleNamespace(control_id=category,category=category,expected_valid=positive,
            requirement_ids=targets,author_provenance=loaded.task.provenance.model_copy(update={
                'inputs':(loaded.task.baseline,loaded.task.contract)})))
        diagnoses.append(ControlDiagnosis(control_id=category,validity='valid' if positive else 'invalid',
            mode='positive' if positive else 'semantic_negative',targets=targets,attack=None,
            evidence=loaded.task.provenance.evidence,
            independence_evidence=loaded.task.provenance.evidence if positive and independent else (),
            note='Synthetic policy coverage only; no actual qualification claimed'))
    selected=replace(loaded,verifier=SimpleNamespace(controls=tuple(controls)))
    missing,targets=validate_control_plan(selected,q.policy.model_copy(update={'controls':tuple(diagnoses)}))
    assert targets==('echo',)
    assert missing==(() if independent else ('alternative independence evidence: alternative_positive',))
