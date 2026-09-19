"""Synthetic frozen-subject invariants; never create or sign an approved review."""
from datetime import timedelta
import pytest
from feature_rl import contracts as c
from feature_rl.qualification import QualificationRejected
from test_qualification_service import service
from m5_fixtures import task_fixture


def package(tmp_path):
    q=service(tmp_path);task_ref=task_fixture(q.store)
    result=q.qualify(task_ref)
    return q,q.store.get_artifact(task_ref),q.store.get_artifact(result.artifacts[0])


def test_frozen_comparison_allows_only_documented_admission_additions(tmp_path):
    from feature_rl.qualification.admission import assert_frozen_evidence
    _,_,report=package(tmp_path)
    # This is still a provisional report with no human record. The pure equality
    # check grants no admission and does not synthesize an approved report.
    changed=report.model_copy(update={'rejection_reasons':('different provisional reason',),
        'provenance':report.provenance.model_copy(update={'producer':'synthetic comparison only'})})
    assert_frozen_evidence(report,changed)


@pytest.mark.parametrize('field',[
    'kind','schema_version','visibility','task','baseline_health','baseline_absence',
    'reference_run','controls','fresh_runs','interrupted_reset_runs','repair_attempts','policy_version'])
def test_every_reviewed_field_is_immutable_even_when_reports_look_provisional(tmp_path,field):
    from feature_rl.qualification.admission import assert_frozen_evidence
    _,_,report=package(tmp_path)
    # model_copy is deliberately used to probe the comparison without claiming a
    # valid artifact or constructing an approval. The store never receives it.
    drift=report.model_copy(update={field:object()})
    with pytest.raises(QualificationRejected,match='frozen reviewed evidence'):
        assert_frozen_evidence(report,drift)


def test_lifecycle_comparison_excludes_only_state_and_qualification(tmp_path):
    from feature_rl.qualification.admission import assert_lifecycle_subject
    _,task,report=package(tmp_path)
    assert_lifecycle_subject(task,task)
    # Compare unvalidated diagnostic values only; no admitted task is published.
    assert_lifecycle_subject(task,task.model_copy(update={'state':c.TaskState.QUALIFIED,
        'qualification':report.task}))
    for field in c.TaskBundle.model_fields:
        if field in {'state','qualification'}:continue
        with pytest.raises(QualificationRejected,match='BUILT subject payload'):
            assert_lifecycle_subject(task,task.model_copy(update={field:object()}))


def test_missing_human_verifier_and_callback_injection_fail_closed(tmp_path):
    q,task,report=package(tmp_path)
    class FakeHuman:
        def verify(self,*args,**kwargs):raise AssertionError('untrusted verifier callback must never execute')
    q.attestation_verifier=FakeHuman()
    with pytest.raises(QualificationRejected,match='external SSHHumanVerifier'):
        q.accept(report.task,report.task)


def test_provisional_result_and_arbitrary_cas_are_never_accepted_origin(tmp_path):
    q,task,report=package(tmp_path)
    for ref in (report.task,q.store.put_artifact(report)):
        with pytest.raises(QualificationRejected):q.verify_accepted(report.task,ref)
