"""Synthetic semantic-gate tests; no runtime or successful task is claimed."""
from types import SimpleNamespace
import pytest
from feature_rl import contracts as c
from feature_rl.grading import AssertionResult, CaseResult


def checked():
    feature=SimpleNamespace(requirement_id='feature',mandatory=True)
    compatibility=SimpleNamespace(requirement_id='compat',mandatory=True)
    return SimpleNamespace(contract=SimpleNamespace(requirements=(feature,),compatibility_obligations=(compatibility,)))


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
