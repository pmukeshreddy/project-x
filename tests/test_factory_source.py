"""Actual M0/Registry source disposition diagnostics, never model/human evidence."""
import importlib

import pytest

from feature_rl import contracts as c
from feature_rl.artifacts import ArtifactStore, canonical_json
from feature_rl.registry import Registry
from m5_fixtures import task_fixture
from test_factory_lifecycle import mutate


def setup(tmp_path):
    api = importlib.import_module('feature_rl.pipeline')
    assert hasattr(api, 'Factory'), 'concrete factory source prerequisite is missing'
    store = ArtifactStore(tmp_path/'store', c.ActorRole.CONTROLLER)
    registry = Registry(tmp_path/'registry', store)
    task = store.get_artifact(task_fixture(store))
    candidate = store.get_artifact(task.source_pair).candidate
    factory = api.Factory(store=store, registry=registry, revision='e'*40)
    return api, factory, candidate


@pytest.mark.parametrize('disposition,status', [('candidate_rejection','rejected'),
    ('unsupported_semantics','unresolved'),('infrastructure_failure','unresolved'),('success','eligible')])
def test_source_prerequisite_preserves_actual_disposition_and_costs(tmp_path, disposition, status):
    api, factory, candidate = setup(tmp_path)
    original = factory.store.get_artifact(candidate)
    candidate = mutate(factory.store, candidate, screening=original.screening.model_copy(update={
        'disposition': c.Disposition(disposition), 'reason':'TEST ONLY source screening'}))
    result = factory.screen_source(candidate)
    assert result.operation == 'construct' and result.disposition == c.Disposition(disposition)
    record = api.read_source_disposition(factory.store, result.artifacts[0])
    assert record.candidate == candidate and record.source_status == status
    assert record.repository_family == original.repository_family
    assert record.original_costs == original.costs
    assert record.screening == factory.store.get_artifact(candidate).screening
    job = factory.registry.job(record.claim.job_id)
    assert job.spec.inputs == (candidate,) and job.result == result
    assert job.spec.invocation == 'm6-source-admission' and job.state == 'completed'
    assert not any(r.kind in ('TaskBundle', 'RolloutRecord') for r in result.artifacts)
    before = factory.registry.events(limit=1000)
    assert factory.screen_source(candidate) == result
    assert before == factory.registry.events(limit=1000)


def test_unresolved_license_is_blocked_not_a_semantic_rejection(tmp_path):
    api, factory, candidate = setup(tmp_path)
    original = factory.store.get_artifact(candidate)
    candidate = mutate(factory.store, candidate, license=original.license.model_copy(update={'status':'unknown'}))
    result = factory.screen_source(candidate)
    assert result.disposition == c.Disposition.BLOCKED
    assert api.read_source_disposition(factory.store, result.artifacts[0]).source_status == 'unresolved'


def test_new_factory_revision_does_not_import_original_source_costs_twice(tmp_path):
    api, factory, candidate = setup(tmp_path)
    first = factory.screen_source(candidate)
    later = api.Factory(store=factory.store, registry=factory.registry, revision='f'*40)
    before = factory.registry.events(limit=1000)
    assert later.screen_source(candidate) == first
    assert factory.registry.events(limit=1000) == before


def test_crash_before_claim_can_resume_queued_source_job(tmp_path, monkeypatch):
    _, factory, candidate = setup(tmp_path)
    claim = factory.registry.claim
    def stop(*args,**kwargs): raise SystemExit('TEST ONLY after enqueue before claim')
    monkeypatch.setattr(factory.registry,'claim',stop)
    with pytest.raises(SystemExit): factory.screen_source(candidate)
    monkeypatch.setattr(factory.registry,'claim',claim)
    assert factory.screen_source(candidate).disposition == c.Disposition.SUCCESS


def test_candidate_lock_blocks_competing_source_import_without_registry_event(tmp_path):
    _, factory, candidate = setup(tmp_path)
    from feature_rl.pipeline.locking import candidate_lock
    from feature_rl.registry import Backpressure
    before = factory.registry.events(limit=1000)
    with candidate_lock(factory.store,candidate):
        with pytest.raises(Backpressure): factory.screen_source(candidate)
    assert factory.registry.events(limit=1000) == before


def test_lost_source_receipt_reply_recovers_exact_bytes_and_accounting(tmp_path, monkeypatch):
    api, factory, candidate = setup(tmp_path)
    put = factory.store.put_bytes
    def lost(data,kind,visibility):
        ref = put(data,kind,visibility)
        if kind == 'm6-source-disposition': raise OSError('TEST ONLY lost reply')
        return ref
    monkeypatch.setattr(factory.store,'put_bytes',lost)
    with pytest.raises(api.FactoryPublicationFailed) as pending: factory.screen_source(candidate)
    claim = pending.value.claim
    before = factory.registry.accounting(claim.job_id)
    assert before.observations[0].observation.revision == 1
    monkeypatch.setattr(factory.store,'put_bytes',put)
    result = factory.retry_publication(pending.value)
    assert factory.store.get_bytes(result.artifacts[0]) == pending.value.payload
    assert result == factory.recover(claim)
    assert factory.registry.accounting(claim.job_id).observations[0].observation.revision == 2


def test_missing_freeze_preserves_unknown_and_never_rescreens(tmp_path, monkeypatch):
    api, factory, candidate = setup(tmp_path)
    put = factory.store.put_bytes
    def stop(data,kind,visibility):
        if kind == 'm6-source-disposition': raise SystemExit('TEST ONLY process stop')
        return put(data,kind,visibility)
    monkeypatch.setattr(factory.store,'put_bytes',stop)
    with pytest.raises(SystemExit): factory.screen_source(candidate)
    jobs = [factory.registry.job(j) for j in factory.registry.trace(candidate).jobs]
    claim = factory.registry.attempts(jobs[0].job_id)[-1].claim
    with pytest.raises(api.FactoryRecoveryRequired): factory.recover(claim)
    assert all(x.measurement == 'unknown' for x in factory.registry.accounting(claim.job_id).observations[0].observation.costs)


def test_later_source_evidence_quarantine_preserves_history_and_blocks_use(tmp_path):
    api, factory, candidate = setup(tmp_path)
    result = factory.screen_source(candidate)
    record = api.read_source_disposition(factory.store, result.artifacts[0])
    proof = factory.store.put_bytes(b'TEST ONLY defect', 'source-test-proof', c.Visibility.PRIVATE)
    root = record.screening.evidence[0].artifacts[0]
    factory.registry.quarantine(root,notice_id='source-diagnostic',reason='TEST ONLY',evidence=(proof,))
    assert factory.registry.job(record.claim.job_id).result == result
    assert result.artifacts[0] in factory.registry.trace(root).artifacts
    from feature_rl.registry import QuarantinedError
    with pytest.raises(QuarantinedError): factory.registry.assert_usable(result.artifacts[0])
