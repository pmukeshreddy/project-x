"""Lifecycle mechanisms with a TEST-ONLY M5 gate substitution; no human approval.

Positive transition mechanics substitute the trusted M5 method in temporary
diagnostic state. The real M5 method is exercised for incomplete-qualification denial. No
signing, native runtime, model call or accepted QualificationReport is created.
"""
import importlib
import json

import pytest

from feature_rl import contracts as c
from feature_rl.artifacts import canonical_json
from feature_rl.qualification import QualificationRejected
from feature_rl.registry import Registry
from m5_fixtures import task_fixture
from test_qualification_service import service


def route(tmp_path, monkeypatch, *, simulated_gate=True):
    module = importlib.import_module('feature_rl.pipeline')
    assert hasattr(module, 'TaskLifecycle'), 'actual lifecycle resolver is missing'
    q = service(tmp_path)
    built = task_fixture(q.store)
    provisional = q.qualify(built).artifacts[0]
    if simulated_gate:
        def diagnostic_gate(task, report):
            assert report == provisional
            return q.store.get_artifact(report)
        monkeypatch.setattr(q, 'verify_accepted', diagnostic_gate)
    lifecycle = module.TaskLifecycle(store=q.store, registry=q.registry, qualification=q, revision='d'*40)
    return module, lifecycle, q, built, provisional


def mutate(store, reference, **fields):
    old = store.get_artifact(reference)
    value = old.model_dump(mode='json') | {name: item.model_dump(mode='json') if hasattr(item,'model_dump') else item for name,item in fields.items()}
    return store.put_artifact(type(old).model_validate_json(json.dumps(value)))


def test_actual_m5_incomplete_qualification_cannot_transition(tmp_path, monkeypatch):
    _, lifecycle, q, built, provisional = route(tmp_path, monkeypatch, simulated_gate=False)
    result = lifecycle.qualify(built, provisional)
    assert result.disposition == c.Disposition.PROVISIONAL
    assert 'unresolved or failed automated gates' in result.reason
    assert all(ref.kind != 'TaskBundle' for ref in result.artifacts)
    assert result == lifecycle.qualify(built, provisional)


def test_legal_chain_preserves_every_frozen_field_and_reader_is_idempotent(tmp_path, monkeypatch):
    _, lifecycle, q, built, report = route(tmp_path, monkeypatch)
    qualified = lifecycle.qualify(built, report)
    assert qualified.disposition == c.Disposition.SUCCESS
    released = lifecycle.release(qualified.artifacts[0])
    assert released.disposition == c.Disposition.SUCCESS
    target = released.artifacts[0]
    before = q.registry.events(limit=1000)
    actual = lifecycle.resolve_released(target)
    assert actual == q.store.get_artifact(target) and actual.state == c.TaskState.RELEASED
    assert canonical_json(actual.model_dump(mode='json',exclude={'state','qualification'})) == canonical_json(
        q.store.get_artifact(built).model_dump(mode='json',exclude={'state','qualification'}))
    assert lifecycle.qualify(built,report) == qualified
    assert lifecycle.release(qualified.artifacts[0]) == released
    assert q.registry.events(limit=1000) == before
    assert all(not q.registry.accounting(job).unobserved_attempts for job in q.registry.trace(report).jobs)


def test_state_string_and_unselected_cas_cannot_release(tmp_path, monkeypatch):
    module, lifecycle, q, built, report = route(tmp_path, monkeypatch)
    forged = mutate(q.store,built,state='released',qualification=report)
    q.registry.register(forged)
    with pytest.raises(module.AdmissionRejected):
        lifecycle.resolve_released(forged)
    failed = lifecycle.release(built)
    assert failed.disposition != c.Disposition.SUCCESS
    assert all(ref.kind != 'TaskBundle' for ref in failed.artifacts)


@pytest.mark.parametrize('field,value',[
    ('repository_family','different-diagnostic'),('request_lineage',['different-lineage']),
    ('partition','train'),('visibility','evaluation'),('adapter_version','different-adapter')])
def test_actual_stored_payload_drift_is_rejected(tmp_path, monkeypatch, field, value):
    module, lifecycle, q, built, report = route(tmp_path, monkeypatch)
    qualified = lifecycle.qualify(built,report).artifacts[0]
    released = lifecycle.release(qualified).artifacts[0]
    changed = mutate(q.store,released,**{field:value});q.registry.register(changed)
    with pytest.raises(module.AdmissionRejected):lifecycle.resolve_released(changed)


@pytest.mark.parametrize('root_kind',['built','report','receipt','configuration'])
def test_later_quarantine_traces_and_blocks_consumers(tmp_path, monkeypatch, root_kind):
    module, lifecycle, q, built, report = route(tmp_path, monkeypatch)
    qualified = lifecycle.qualify(built,report)
    released = lifecycle.release(qualified.artifacts[0])
    roots={'built':built,'report':report,'receipt':qualified.artifacts[1],'configuration':lifecycle.configuration}
    root=roots[root_kind]
    proof=q.store.put_bytes(b'TEST ONLY diagnosed dependency defect','lifecycle-test-proof',c.Visibility.PRIVATE)
    q.registry.quarantine(root,notice_id='diagnostic-defect',reason='TEST ONLY',evidence=(proof,))
    assert released.artifacts[0] in q.registry.trace(root).artifacts
    with pytest.raises(module.AdmissionRejected):lifecycle.resolve_released(released.artifacts[0])


def test_current_m5_revocation_is_rechecked(tmp_path, monkeypatch):
    module, lifecycle, q, built, report = route(tmp_path,monkeypatch)
    qualified=lifecycle.qualify(built,report).artifacts[0]
    released=lifecycle.release(qualified).artifacts[0]
    def revoked(*_):raise QualificationRejected('unverified_human_review','TEST ONLY revoked enrollment')
    monkeypatch.setattr(q,'verify_accepted',revoked)
    with pytest.raises(module.AdmissionRejected,match='revoked'):lifecycle.resolve_released(released)


def test_publication_failure_recovers_exact_frozen_transition_without_redispatch(tmp_path, monkeypatch):
    module,lifecycle,q,built,report=route(tmp_path,monkeypatch)
    original=q.store.put_artifact
    def unavailable(value):
        if isinstance(value,c.TaskBundle) and value.state==c.TaskState.QUALIFIED:
            raise OSError('TEST ONLY publication outage')
        return original(value)
    monkeypatch.setattr(q.store,'put_artifact',unavailable)
    with pytest.raises(module.LifecycleRecoveryRequired) as pending:lifecycle.qualify(built,report)
    claim=pending.value.claim
    snapshot=q.registry.accounting(claim.job_id)
    assert len(snapshot.observations)==1 and snapshot.observations[0].observation.revision==2
    monkeypatch.setattr(q.store,'put_artifact',original)
    result=lifecycle.recover(claim)
    assert result.disposition==c.Disposition.SUCCESS
    assert result==lifecycle.recover(claim)
    assert q.registry.accounting(claim.job_id)==snapshot


def test_constructor_rejects_callbacks_and_mismatched_registry(tmp_path,monkeypatch):
    module,lifecycle,q,_,_=route(tmp_path,monkeypatch)
    with pytest.raises(TypeError):module.TaskLifecycle(store=q.store,registry=q.registry,
        qualification=lambda *args:True,revision='d'*40)
    other=Registry(tmp_path/'other-registry',q.store)
    with pytest.raises(TypeError):module.TaskLifecycle(store=q.store,registry=other,
        qualification=q,revision='d'*40)


def test_lost_receipt_reply_retries_exact_bytes_and_rejects_configuration_drift(tmp_path,monkeypatch):
    module,lifecycle,q,built,report=route(tmp_path,monkeypatch)
    original=q.store.put_bytes
    def lost_reply(data,kind,visibility):
        value=original(data,kind,visibility)
        if kind=='m6-transition':raise OSError('TEST ONLY lost receipt CAS reply')
        return value
    monkeypatch.setattr(q.store,'put_bytes',lost_reply)
    with pytest.raises(module.LifecyclePublicationFailed) as failure:lifecycle.qualify(built,report)
    pending=failure.value
    monkeypatch.setattr(q.store,'put_bytes',original)
    other=module.TaskLifecycle(store=q.store,registry=q.registry,qualification=q,revision='e'*40)
    before=q.registry.events(limit=1000)
    with pytest.raises(module.AdmissionRejected):other.retry_publication(pending)
    assert q.registry.events(limit=1000)==before
    result=lifecycle.retry_publication(pending)
    assert result.disposition==c.Disposition.SUCCESS
    assert q.store.get_bytes(result.artifacts[1])==pending.payload
    assert result==lifecycle.retry_publication(pending)


def test_pre_freeze_unknown_attempt_is_never_reexecuted(tmp_path,monkeypatch):
    module,lifecycle,q,built,report=route(tmp_path,monkeypatch)
    def stop(*_):raise SystemExit('TEST ONLY stop after intent')
    monkeypatch.setattr(q,'verify_accepted',stop)
    with pytest.raises(SystemExit):lifecycle.qualify(built,report)
    jobs=[q.registry.job(job) for job in q.registry.trace(report).jobs]
    job=next(job for job in jobs if job.spec.invocation=='m6-transition-qualified')
    claim=q.registry.attempts(job.job_id)[-1].claim
    account=q.registry.accounting(job.job_id)
    assert account.observations[0].observation.revision==1
    assert all(value.measurement=='unknown' for value in account.observations[0].observation.costs)
    with pytest.raises(module.LifecycleRecoveryRequired,match='not durably frozen'):lifecycle.recover(claim)


def test_revocation_during_frozen_publication_blocks_recovery(tmp_path,monkeypatch):
    module,lifecycle,q,built,report=route(tmp_path,monkeypatch)
    original=q.store.put_artifact
    def outage(value):
        if isinstance(value,c.TaskBundle) and value.state==c.TaskState.QUALIFIED:raise OSError('TEST ONLY outage')
        return original(value)
    monkeypatch.setattr(q.store,'put_artifact',outage)
    with pytest.raises(module.LifecycleRecoveryRequired) as pending:lifecycle.qualify(built,report)
    monkeypatch.setattr(q.store,'put_artifact',original)
    def revoked(*_):raise QualificationRejected('unverified_human_review','TEST ONLY current revocation')
    monkeypatch.setattr(q,'verify_accepted',revoked)
    with pytest.raises(module.LifecycleRecoveryRequired):lifecycle.recover(pending.value.claim)
    assert q.registry.job(pending.value.claim.job_id).state=='running'
