"""Actual Registry/CAS failure recovery; no worker or human approval is simulated."""
import pytest
from feature_rl import contracts as c
from feature_rl.qualification import QualificationPublicationFailed
from feature_rl.qualification.evidence import put_record
from test_qualification_service import service
from m5_fixtures import task_fixture


def test_completed_cost_snapshot_publication_retry_preserves_exact_selected_result(tmp_path,monkeypatch):
    q=service(tmp_path);task=task_fixture(q.store)
    job=q._job(task,'unit-publication');claim=q._claim(job)
    ref=put_record(q.store,{'diagnostic':'no task approval'},'unit-diagnostic')
    subject=q.store.get_artifact(task)
    result=c.OperationResult(operation='qualify',disposition=c.Disposition.PROVISIONAL,artifacts=(ref,),
        evidence=subject.provenance.evidence,costs=subject.costs,reason='Synthetic non-admission result')
    original=q.registry.complete;calls=[]
    def committed_then_failed(*args,**kwargs):
        selected=original(*args,**kwargs);calls.append(selected)
        raise OSError('synthetic lost acknowledgement after durable Registry completion')
    monkeypatch.setattr(q.registry,'complete',committed_then_failed)
    with pytest.raises(QualificationPublicationFailed) as failure:q._complete(claim,result)
    monkeypatch.setattr(q.registry,'complete',original)
    recovered=q.retry_publication(failure.value)
    assert recovered==q.registry.job(job.job_id).result==calls[0].result
    before=q.registry.events(limit=1000)
    assert q.retry_publication(failure.value)==recovered
    assert q.registry.events(limit=1000)==before


def test_frozen_final_report_retries_exact_bytes_after_cas_failure(tmp_path,monkeypatch):
    q=service(tmp_path);task=task_fixture(q.store)
    original=q.store.put_artifact;retained=[]
    def fail_report(value):
        if isinstance(value,c.QualificationReport):
            retained.append(value)
            raise OSError('synthetic report storage outage after independent checks')
        return original(value)
    monkeypatch.setattr(q.store,'put_artifact',fail_report)
    with pytest.raises(QualificationPublicationFailed) as failure:q.qualify(task)
    assert failure.value.pending.purpose=='qualification'
    monkeypatch.setattr(q.store,'put_artifact',original)
    result=q.retry_publication(failure.value)
    assert q.store.get_artifact(result.artifacts[0])==retained[0]
    assert q.qualify(task)==result


def test_unknown_worker_attempt_requires_reconciliation_never_redispatch(tmp_path):
    from feature_rl.qualification import QualificationRecoveryRequired
    q=service(tmp_path);task=task_fixture(q.store)
    job=q._job(task,'m5-qualify');claim=q._claim(job)
    with pytest.raises(QualificationRecoveryRequired):q.qualify(task)
    assert q.registry.job(job.job_id).state=='running'
