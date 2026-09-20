"""Explicit historical audit quarantine scope; no human finding is fabricated."""
import pytest

from feature_rl import contracts as c
from feature_rl.artifacts import canonical_json
from test_registry import setup, observation, result


def audit(tmp_path, *, quarantine_first=False):
    m,store,registry,subject,_,old=setup(tmp_path)
    assert hasattr(registry,'historical_audit_configuration'), 'explicit historical audit scope is missing'
    instruction=store.put_bytes(b'TEST ONLY current audit instruction','audit-instruction',c.Visibility.PRIVATE)
    attestation=store.put_bytes(b'TEST ONLY bytes, no human approval','audit-attestation',c.Visibility.PRIVATE)
    config=store.put_bytes(b'TEST ONLY audit configuration','audit-configuration',c.Visibility.PRIVATE)
    registry.register(config,dependencies=(subject,instruction,attestation))
    if quarantine_first:
        registry.quarantine(subject,notice_id='old-defect',reason='TEST ONLY',evidence=(instruction,))
    scoped=registry.historical_audit_configuration(config,subjects=(subject,),protected=(instruction,attestation))
    spec=old.model_copy(update={'operation':'audit','configuration':scoped})
    job=registry.enqueue(spec)
    claim=registry.claim(job.job_id,owner='diagnostic-auditor',claim_key='audit-attempt')
    return m,store,registry,subject,instruction,attestation,config,scoped,spec,claim


@pytest.mark.parametrize('quarantine_first',[False,True])
def test_audit_can_select_actual_report_after_subject_quarantine(tmp_path,quarantine_first):
    m,store,registry,subject,instruction,_,_,scoped,spec,claim=audit(tmp_path,quarantine_first=quarantine_first)
    if not quarantine_first:
        registry.quarantine(subject,notice_id='new-defect',reason='TEST ONLY',evidence=(instruction,))
    report=store.put_bytes(b'TEST ONLY applied notice receipt','audit-report',c.Visibility.PRIVATE)
    registry.register(report,dependencies=(subject,scoped))
    snapshot=registry.reconcile(claim,observation(m,report,1.0))
    value=result(report,snapshot.observation.costs).model_copy(update={'operation':'audit'})
    selected=registry.complete(claim,value,observations=(snapshot.observation_id,))
    assert selected.state=='completed' and selected.result==value
    assert registry.recover().event_count>0
    assert registry.job(claim.job_id).result==value
    assert registry.complete(claim,value,observations=(snapshot.observation_id,))==selected
    with pytest.raises(m.QuarantinedError):registry.assert_usable(report)
    assert report in registry.trace(subject).artifacts


@pytest.mark.parametrize('current',['instruction','attestation','configuration','scope','output'])
def test_current_audit_inputs_and_new_output_notices_still_block(tmp_path,current):
    m,store,registry,subject,instruction,attestation,config,scoped,spec,claim=audit(tmp_path)
    output=store.put_bytes(b'TEST ONLY output','audit-report',c.Visibility.PRIVATE)
    registry.register(output,dependencies=(subject,))
    target={'instruction':instruction,'attestation':attestation,'configuration':config,'scope':scoped,'output':output}[current]
    registry.quarantine(target,notice_id='current-defect',reason='TEST ONLY',evidence=(subject,))
    snapshot=registry.reconcile(claim,observation(m,output,1.0))
    value=result(output,snapshot.observation.costs).model_copy(update={'operation':'audit'})
    with pytest.raises(m.QuarantinedError):registry.complete(claim,value,observations=(snapshot.observation_id,))
    assert registry.job(claim.job_id).state=='running'


@pytest.mark.parametrize('operation',['construct','run','train','evaluate'])
def test_scope_never_relaxes_other_operations(tmp_path,operation):
    m,store,registry,subject,instruction,_,_,scope,spec,claim=audit(tmp_path)
    registry.quarantine(subject,notice_id='historical',reason='TEST ONLY',evidence=(instruction,))
    with pytest.raises(m.QuarantinedError):
        registry.enqueue(spec.model_copy(update={'operation':operation,'invocation':'other-'+operation}))


def test_old_job_spec_bytes_and_identity_survive_new_scope_events(tmp_path):
    m,store,registry,subject,config,spec=setup(tmp_path)
    assert hasattr(registry,'historical_audit_configuration'), 'explicit historical audit scope is missing'
    job=registry.enqueue(spec)
    payload=canonical_json(job.spec.model_dump(mode='json'))
    scope=registry.historical_audit_configuration(config,subjects=(subject,),protected=(config,))
    registry.recover()
    assert registry.job(job.job_id)==job
    assert canonical_json(registry.job(job.job_id).spec.model_dump(mode='json'))==payload


def test_reserved_scope_bytes_and_added_dependencies_are_validated(tmp_path):
    m,store,registry,subject,config,spec=setup(tmp_path)
    assert hasattr(registry,'historical_audit_configuration'), 'explicit historical audit scope is missing'
    forged=store.put_bytes(b'{}','registry-historical-audit-policy',c.Visibility.PRIVATE)
    before=registry.events(limit=1000)
    with pytest.raises((ValueError,m.RegistryError)):registry.register(forged)
    assert registry.events(limit=1000)==before
    scope=registry.historical_audit_configuration(config,subjects=(subject,),protected=(config,))
    extra=store.put_bytes(b'TEST ONLY undeclared grant','extra',c.Visibility.PRIVATE)
    with pytest.raises(m.RegistryError):registry.register(scope,dependencies=(extra,))


def test_historical_dependencies_allowed_but_explicit_protection_wins(tmp_path):
    m,store,registry,dependency,config,spec=setup(tmp_path)
    assert hasattr(registry,'historical_audit_configuration'), 'explicit historical audit scope is missing'
    subject=store.put_bytes(b'TEST ONLY historical subject','subject',c.Visibility.PRIVATE)
    registry.register(subject,dependencies=(dependency,))
    registry.register(config,dependencies=(subject,))
    registry.quarantine(dependency,notice_id='historical-child',reason='TEST ONLY',evidence=(subject,))
    allowed=registry.historical_audit_configuration(config,subjects=(subject,),protected=(config,))
    registry.enqueue(spec.model_copy(update={'operation':'audit','inputs':(subject,),'configuration':allowed}))
    denied=registry.historical_audit_configuration(config,subjects=(subject,),protected=(config,dependency))
    with pytest.raises(m.QuarantinedError):
        registry.enqueue(spec.model_copy(update={'operation':'audit','inputs':(subject,),'configuration':denied}))


def test_projection_failure_after_applied_quarantine_recovers_selected_audit(tmp_path,monkeypatch):
    import json
    m,store,registry,subject,instruction,_,_,scoped,spec,claim=audit(tmp_path)
    registry.quarantine(subject,notice_id='applied-finding',reason='TEST ONLY',evidence=(instruction,))
    output=store.put_bytes(b'TEST ONLY notice was applied','audit-report',c.Visibility.PRIVATE)
    registry.register(output,dependencies=(subject,))
    snapshot=registry.reconcile(claim,observation(m,output,1.0))
    value=result(output,snapshot.observation.costs).model_copy(update={'operation':'audit'})
    project=registry._storage.project
    def fail(con,directory,raw):
        if json.loads(raw[-1])['action']=='complete':raise OSError('TEST ONLY after actual SQLite commit before JSONL projection')
        return project(con,directory,raw)
    monkeypatch.setattr(registry._storage,'project',fail)
    with pytest.raises(m.RegistryError):registry.complete(claim,value,observations=(snapshot.observation_id,))
    monkeypatch.setattr(registry._storage,'project',project)
    registry.recover()
    selected=registry.complete(claim,value,observations=(snapshot.observation_id,))
    assert selected.result==value
    assert any(n.notice_id=='applied-finding' and n.active for n in registry.trace(subject).notices)
