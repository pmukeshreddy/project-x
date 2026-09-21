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
    assert all(ref.kind != 'TaskBundle' for ref in result.artifacts)
    assert result == lifecycle.qualify(built, provisional)


def test_legal_chain_preserves_every_frozen_field_and_reader_is_idempotent(tmp_path, monkeypatch):
    _, lifecycle, q, built, report = route(tmp_path, monkeypatch)
    def no_execution_or_history(*args, **kwargs):
        raise AssertionError('frozen lifecycle must not execute or replay operation history')
    for name in ('enqueue', 'claim', 'job', 'attempts', 'accounting', 'reconcile', 'complete', 'trace'):
        monkeypatch.setattr(q.registry, name, no_execution_or_history)
    monkeypatch.setattr(q.grader, 'grade', no_execution_or_history)
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


def test_private_frozen_manifest_needs_no_transition_receipt_but_release_requires_qualified(tmp_path, monkeypatch):
    _, lifecycle, q, built, report = route(tmp_path, monkeypatch)
    frozen = mutate(q.store,built,state='released',qualification=report)
    q.registry.register(frozen)
    assert lifecycle.resolve_released(frozen)==q.store.get_artifact(frozen)
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


@pytest.mark.parametrize('root_kind',['built','report'])
def test_later_quarantine_traces_and_blocks_consumers(tmp_path, monkeypatch, root_kind):
    module, lifecycle, q, built, report = route(tmp_path, monkeypatch)
    qualified = lifecycle.qualify(built,report)
    released = lifecycle.release(qualified.artifacts[0])
    roots={'built':built,'report':report}
    root=roots[root_kind]
    proof=q.store.put_bytes(b'TEST ONLY diagnosed dependency defect','lifecycle-test-proof',c.Visibility.PRIVATE)
    q.registry.quarantine(root,notice_id='diagnostic-defect',reason='TEST ONLY',evidence=(proof,))
    assert released.artifacts[0] in q.registry.trace(root).artifacts
    with pytest.raises(module.AdmissionRejected):lifecycle.resolve_released(released.artifacts[0])


def test_current_report_integrity_is_checked(tmp_path, monkeypatch):
    module, lifecycle, q, built, report = route(tmp_path,monkeypatch)
    qualified=lifecycle.qualify(built,report).artifacts[0]
    released=lifecycle.release(qualified).artifacts[0]
    def corrupted(*_):raise QualificationRejected('invalid_evidence','TEST ONLY corrupted report')
    monkeypatch.setattr(q,'verify_accepted',corrupted)
    with pytest.raises(module.AdmissionRejected,match='corrupted'):lifecycle.resolve_released(released)
def test_constructor_rejects_callbacks_and_mismatched_registry(tmp_path,monkeypatch):
    module,lifecycle,q,_,_=route(tmp_path,monkeypatch)
    with pytest.raises(TypeError):module.TaskLifecycle(store=q.store,registry=q.registry,
        qualification=lambda *args:True,revision='d'*40)
    other=Registry(tmp_path/'other-registry',q.store)
    with pytest.raises(TypeError):module.TaskLifecycle(store=q.store,registry=other,
        qualification=q,revision='d'*40)


def test_manifest_publication_can_repeat_without_a_recovery_protocol(tmp_path, monkeypatch):
    _,lifecycle,q,built,report=route(tmp_path,monkeypatch)
    original=q.store.put_artifact
    def unavailable(value):
        if isinstance(value,c.TaskBundle) and value.state==c.TaskState.QUALIFIED:
            raise OSError('TEST ONLY publication outage')
        return original(value)
    monkeypatch.setattr(q.store,'put_artifact',unavailable)
    assert lifecycle.qualify(built,report).disposition==c.Disposition.INFRASTRUCTURE
    monkeypatch.setattr(q.store,'put_artifact',original)
    result=lifecycle.qualify(built,report)
    assert result.disposition==c.Disposition.SUCCESS
    assert lifecycle.qualify(built,report)==result
