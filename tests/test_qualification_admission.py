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




def test_provisional_result_and_arbitrary_cas_are_never_accepted_origin(tmp_path):
    q,task,report=package(tmp_path)
    for ref in (report.task,q.store.put_artifact(report)):
        with pytest.raises(QualificationRejected):q.verify_accepted(report.task,ref)

def test_arbitrary_report_cannot_replace_the_current_selected_report(tmp_path):
    from feature_rl.qualification.admission import _selected, _spec
    q,task,report=package(tmp_path)
    forged=q.store.put_artifact(report.model_copy(update={'rejection_reasons':('Synthetic replacement, never admitted',)}))
    job=q._job(report.task,'m5-qualify')
    with pytest.raises(QualificationRejected,match='selected'):
        _selected(q,job.job_id,_spec(q,(report.task,),'m5-qualify'),forged)
