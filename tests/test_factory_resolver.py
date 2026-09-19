"""Per-policy dispatch mechanics; positive M5 gate is TEST ONLY, no human claim."""
import importlib

import pytest

from feature_rl import contracts as c
from feature_rl.qualification import QualificationPolicy, QualificationService, QualificationRejected
from feature_rl.pipeline import TaskLifecycle, AdmissionRejected
from m5_fixtures import task_fixture
from test_qualification_service import service
from test_factory_lifecycle import mutate


def configured(tmp_path, monkeypatch):
    api = importlib.import_module('feature_rl.pipeline')
    assert hasattr(api, 'ReleasedTaskResolver'), 'multi-policy admission resolver is missing'
    first = service(tmp_path)
    second = QualificationService(store=first.store, registry=first.registry, grader=first.grader,
        builder=None, revision=first.revision, policy=QualificationPolicy(policy_id='diagnostic-second'))
    lifecycles = tuple(TaskLifecycle(store=q.store, registry=q.registry, qualification=q, revision='d'*40)
        for q in (first, second))
    built = task_fixture(first.store)
    reports = tuple(q.qualify(built).artifacts[0] for q in (first, second))
    def diagnostic_gate(q, task, report):
        # Test substitution keeps reports provisional and never creates a human approval.
        if report != reports[0 if q.policy.policy_id == 'pilot-v1' else 1]:
            raise QualificationRejected('invalid_evidence', 'TEST ONLY wrong policy')
        return q.store.get_artifact(report)
    monkeypatch.setattr(QualificationService, 'verify_accepted', diagnostic_gate)
    released = tuple(l.release(l.qualify(built, report).artifacts[0]).artifacts[0]
        for l, report in zip(lifecycles, reports))
    resolver = api.ReleasedTaskResolver(profiles=(lifecycles[0],), revision='e'*40)
    return api, resolver, first, lifecycles, built, released


def test_two_frozen_policies_use_one_trusted_version_profile_without_events(tmp_path, monkeypatch):
    _, resolver, q, lifecycles, _, released = configured(tmp_path, monkeypatch)
    assert resolver.store is q.store and resolver.registry is q.registry
    with pytest.raises(AdmissionRejected): lifecycles[0].resolve_released(released[1])
    before = q.registry.events(limit=1000)
    for ref in (*released, *released):
        assert resolver.resolve_released(ref) == q.store.get_artifact(ref)
    assert q.registry.events(limit=1000) == before


def test_unknown_lifecycle_revision_fails_without_mutation(tmp_path, monkeypatch):
    api, resolver, q, _, _, released = configured(tmp_path, monkeypatch)
    incompatible = TaskLifecycle(store=q.store, registry=q.registry, qualification=q, revision='f'*40)
    other = api.ReleasedTaskResolver(profiles=(incompatible,), revision='e'*40)
    before = q.registry.events(limit=1000)
    with pytest.raises(AdmissionRejected): other.resolve_released(released[0])
    assert q.registry.events(limit=1000) == before


@pytest.mark.parametrize('subject', ['policy', 'profile', 'task'])
def test_current_quarantine_rechecked_for_selected_policy_and_profile(tmp_path, monkeypatch, subject):
    _, resolver, q, lifecycles, _, released = configured(tmp_path, monkeypatch)
    root = {'policy': lifecycles[1].qualification.policy_ref,
            'profile': lifecycles[0].configuration, 'task': released[1]}[subject]
    proof = q.store.put_bytes(b'TEST ONLY', 'resolver-test-proof', c.Visibility.PRIVATE)
    q.registry.quarantine(root, notice_id='diagnostic-current', reason='TEST ONLY', evidence=(proof,))
    with pytest.raises(AdmissionRejected): resolver.resolve_released(released[1])


def test_current_external_gate_rechecked_after_previous_success(tmp_path, monkeypatch):
    _, resolver, q, _, _, released = configured(tmp_path, monkeypatch)
    resolver.resolve_released(released[1])
    def revoked(*args): raise QualificationRejected('unverified_human_review', 'TEST ONLY revoked')
    monkeypatch.setattr(QualificationService, 'verify_accepted', revoked)
    with pytest.raises(AdmissionRejected, match='revoked'): resolver.resolve_released(released[1])


def test_forged_payload_or_unselected_state_cannot_dispatch(tmp_path, monkeypatch):
    _, resolver, q, _, built, released = configured(tmp_path, monkeypatch)
    changed = mutate(q.store, released[1], repository_family='diagnostic-drift')
    q.registry.register(changed)
    before = q.registry.events(limit=1000)
    for ref in (built, changed):
        with pytest.raises(AdmissionRejected): resolver.resolve_released(ref)
    assert q.registry.events(limit=1000) == before


def test_constructor_rejects_callback_and_mixed_stores(tmp_path, monkeypatch):
    api, _, q, lifecycles, _, _ = configured(tmp_path, monkeypatch)
    other = service(tmp_path/'other')
    other_profile = TaskLifecycle(store=other.store, registry=other.registry, qualification=other, revision='d'*40)
    for profiles in ((), (lambda ref: True,), (lifecycles[0], other_profile)):
        with pytest.raises((TypeError, ValueError)):
            api.ReleasedTaskResolver(profiles=profiles, revision='e'*40)


def test_real_m5_gate_remains_denied_for_diagnostic_chain(tmp_path, monkeypatch):
    original = QualificationService.verify_accepted
    _, resolver, _, _, _, released = configured(tmp_path, monkeypatch)
    monkeypatch.setattr(QualificationService, 'verify_accepted', original)
    with pytest.raises(AdmissionRejected, match='human'):
        resolver.resolve_released(released[0])
