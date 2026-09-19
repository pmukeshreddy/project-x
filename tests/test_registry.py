"""Trusted synthetic registry checks. No historical or generated code execution."""
from datetime import datetime, timezone
import importlib
import importlib.util
import json
import os
from pathlib import Path

import pytest
from pydantic import ValidationError

from feature_rl.artifacts import ArtifactStore, ArtifactError
from feature_rl.contracts import ActorRole, Visibility, CostRecord, EvidenceRecord, OperationResult, Disposition


def api():
    assert importlib.util.find_spec('feature_rl.registry') is not None, 'M6 registry core is missing'
    return importlib.import_module('feature_rl.registry')


def setup(tmp_path, **limits):
    m = api()
    base = tmp_path.resolve()
    store = ArtifactStore(base / 'objects', ActorRole.CONTROLLER)
    source = store.put_bytes(b'trusted synthetic input', 'registry-input', Visibility.PRIVATE)
    config = store.put_bytes(b'fixed diagnostic config', 'registry-config', Visibility.PRIVATE)
    registry = m.Registry(base / 'registry', store, limits=m.RegistryLimits(**limits))
    spec = m.JobSpec(operation='construct', inputs=(source,), configuration=config,
                     implementation='a' * 40, invocation='diagnostic-1', attempt_limit=2)
    return m, store, registry, source, config, spec


def cost(wall=None):
    return CostRecord(category='construction', wall_seconds=wall, cpu_seconds=None,
                      gpu_seconds=None, input_tokens=None, output_tokens=None,
                      human_minutes=None, usd=None, measurement='unknown' if wall is None else 'partial',
                      note='Synthetic accounting observation; no task execution')


def observation(m, source, wall=None, revision=1, upstream='external-call-1'):
    return m.CostObservation(source='diagnostic', upstream_attempt_id=upstream,
                             revision=revision, receipts=(source,), costs=(cost(wall),))


def result(output, costs):
    evidence = EvidenceRecord(producer='registry synthetic check', command=('inert',),
                              recorded_at=datetime(2026, 9, 19, tzinfo=timezone.utc), exit_status=0,
                              artifacts=(output,), revision='a' * 40, scope='unit_diagnostic')
    return OperationResult(operation='construct', disposition=Disposition.SUCCESS,
                           artifacts=(output,), evidence=(evidence,), costs=costs,
                           reason='Synthetic immutable-result publication only')


def test_registration_revalidates_actual_bytes_and_ref_metadata(tmp_path):
    m, store, reg, source, config, spec = setup(tmp_path)
    assert reg.register(source).ref == source
    forged = source.model_copy(update={'visibility': Visibility.PUBLIC})
    with pytest.raises((m.RegistryError, ArtifactError)):
        reg.register(forged)
    (store.root / (source.sha256 + '.json')).write_bytes(b'corrupted')
    with pytest.raises((m.RegistryError, ArtifactError)):
        reg.register(source)
    assert not reg.events(after=100)


def test_artifact_caps_and_symlink_refusal(tmp_path):
    m, store, reg, source, config, spec = setup(tmp_path, max_artifact_envelope_bytes=1024, max_artifact_payload_bytes=512)
    large = store.put_bytes(b'x' * 2048, 'registry-large', Visibility.PRIVATE)
    with pytest.raises((m.RegistryError, ArtifactError)):
        reg.register(large)
    path = store.root / (source.sha256 + '.json')
    outside = tmp_path / 'outside'
    outside.write_bytes(path.read_bytes())
    path.unlink()
    path.symlink_to(outside)
    with pytest.raises((m.RegistryError, ArtifactError)):
        reg.register(source)


def test_immutable_dependencies_and_transitive_quarantine(tmp_path):
    m, store, reg, a, b, spec = setup(tmp_path)
    c = store.put_bytes(b'derived', 'registry-derived', Visibility.PRIVATE)
    reg.register(a)
    reg.register(b, dependencies=(a,))
    reg.register(c, dependencies=(b,))
    with pytest.raises(m.RegistryConflict):
        reg.register(c, dependencies=(a,))
    with pytest.raises(m.RegistryConflict):
        reg.register(a, dependencies=(c,))
    notice = reg.quarantine(a, notice_id='defect-1', reason='synthetic known defect', evidence=(a,))
    assert set(reg.trace(a).artifacts) == {a, b, c}
    with pytest.raises(m.QuarantinedError):
        reg.assert_usable(c)
    reg.lift_quarantine(notice.notice_id, reason='explicit synthetic resolution', evidence=(b,))
    reg.assert_usable(c)
    trace = reg.trace(a)
    assert len(trace.notices) == 1 and not trace.notices[0].active
    assert c in trace.artifacts


def test_content_job_identity_queue_backpressure_and_drift(tmp_path):
    m, store, reg, source, config, spec = setup(tmp_path, max_active_jobs=1)
    first = reg.enqueue(spec)
    assert reg.enqueue(spec) == first
    changed = spec.model_copy(update={'invocation': 'diagnostic-2'})
    with pytest.raises(m.Backpressure):
        reg.enqueue(changed)
    assert len(reg.events()) == 1
    claim = reg.claim(first.job_id, owner='worker', claim_key='claim-1')
    obs = reg.reconcile(claim, observation(m, source))
    reg.complete(claim, result(source, (cost(),)), observations=(obs.observation_id,))
    assert reg.enqueue(changed).job_id != first.job_id
    with pytest.raises(ValidationError):
        m.JobSpec(operation='guess_success', inputs=(source,), configuration=config,
                  implementation='a' * 40, invocation='x', attempt_limit=2)


def test_claim_replay_competition_abandon_retry_and_attempt_limit(tmp_path):
    m, store, reg, source, config, spec = setup(tmp_path)
    job = reg.enqueue(spec)
    claim = reg.claim(job.job_id, owner='worker', claim_key='claim-1')
    assert reg.claim(job.job_id, owner='worker', claim_key='claim-1') == claim
    with pytest.raises(m.ClaimConflict):
        reg.claim(job.job_id, owner='other', claim_key='claim-2')
    reg.abandon(claim, reason='supervisor observed interruption', evidence=(source,))
    with pytest.raises(m.StaleClaim):
        reg.complete(claim, result(source, (cost(),)), observations=())
    with pytest.raises(m.ClaimConflict):
        reg.claim(job.job_id, owner='worker', claim_key='claim-2')
    reg.retry(job.job_id, reason='same saved input; infrastructure repaired', evidence=(config,))
    second = reg.claim(job.job_id, owner='worker', claim_key='claim-2')
    assert second.attempt_id != claim.attempt_id
    reg.abandon(second, reason='second observed interruption', evidence=(source,))
    with pytest.raises(m.AttemptLimit):
        reg.retry(job.job_id, reason='cannot erase the first two attempts', evidence=(config,))
    assert len(reg.attempts(job.job_id)) == 2
    assert len(reg.accounting(job.job_id).unobserved_attempts) == 2


def test_upstream_snapshot_replay_late_cost_and_global_attribution(tmp_path):
    m, store, reg, source, config, spec = setup(tmp_path)
    job = reg.enqueue(spec)
    claim = reg.claim(job.job_id, owner='worker', claim_key='claim-1')
    obs = reg.reconcile(claim, observation(m, source))
    assert reg.reconcile(claim, observation(m, source)) == obs
    completed = reg.complete(claim, result(source, (cost(),)), observations=(obs.observation_id,))
    assert reg.complete(claim, result(source, (cost(),)), observations=(obs.observation_id,)) == completed
    late = reg.reconcile(claim, observation(m, source, wall=0.0, revision=2))
    assert late.observation.costs[0].wall_seconds == 0.0
    assert reg.job(job.job_id).result == completed.result
    accounting = reg.accounting(job.job_id)
    assert len(accounting.observations) == 1 and accounting.observations[0] == late
    assert accounting.unobserved_attempts == ()
    with pytest.raises(m.RegistryConflict):
        reg.reconcile(claim, observation(m, source, wall=2.0, revision=2))
    with pytest.raises(m.RegistryConflict):
        reg.reconcile(claim, observation(m, source, revision=3))
    another = reg.enqueue(spec.model_copy(update={'invocation': 'second'}))
    other = reg.claim(another.job_id, owner='worker', claim_key='claim-other')
    with pytest.raises(m.RegistryConflict):
        reg.reconcile(other, observation(m, source, wall=0.0, revision=2))


def test_completion_requires_existing_exact_costs_and_immutable_result(tmp_path):
    m, store, reg, source, config, spec = setup(tmp_path)
    job = reg.enqueue(spec)
    claim = reg.claim(job.job_id, owner='worker', claim_key='claim-1')
    with pytest.raises(m.RegistryConflict):
        reg.complete(claim, result(source, (cost(),)), observations=())
    obs = reg.reconcile(claim, observation(m, source, wall=2.0))
    with pytest.raises(m.RegistryConflict):
        reg.complete(claim, result(source, (cost(),)), observations=(obs.observation_id,))
    reg.complete(claim, result(source, (cost(2.0),)), observations=(obs.observation_id,))
    with pytest.raises(m.RegistryConflict):
        reg.complete(claim, result(config, (cost(2.0),)), observations=(obs.observation_id,))


def test_abandoned_upstream_receipts_remain_reconcilable(tmp_path):
    m, store, reg, source, config, spec = setup(tmp_path)
    job = reg.enqueue(spec)
    claim = reg.claim(job.job_id, owner='worker', claim_key='claim-1')
    reg.abandon(claim, reason='controller lost reply', evidence=(source,))
    obs = reg.reconcile(claim, observation(m, source, wall=3.0))
    assert reg.accounting(job.job_id).observations == (obs,)
    assert reg.job(job.job_id).state == 'paused'
    assert reg.attempts(job.job_id)[0].state == 'abandoned'


@pytest.mark.parametrize('entry', ['extra', 'index.sqlite3', 'events.jsonl', 'registry.lock'])
def test_unexpected_state_files_links_and_hardlinks_refused(tmp_path, entry):
    m, store, reg, source, config, spec = setup(tmp_path)
    reg.enqueue(spec)
    path = reg.root / entry
    outside = tmp_path / 'outside'
    outside.write_bytes(b'preserve outside')
    if path.exists():
        path.unlink()
    path.symlink_to(outside)
    with pytest.raises(m.RegistryError):
        reg.recover()
    assert outside.read_bytes() == b'preserve outside'


def test_state_root_symlink_and_changed_limits_refused(tmp_path):
    m, store, reg, source, config, spec = setup(tmp_path)
    alias = tmp_path / 'alias'
    alias.symlink_to(reg.root)
    with pytest.raises(m.RegistryError):
        m.Registry(alias, store)
    with pytest.raises(m.RegistryError):
        m.Registry(reg.root, store, limits=m.RegistryLimits(max_active_jobs=99))


def test_events_and_closure_limits_fail_before_unbounded_growth(tmp_path):
    m, store, reg, source, config, spec = setup(tmp_path, max_events=2)
    job = reg.enqueue(spec)
    reg.claim(job.job_id, owner='worker', claim_key='claim-1')
    with pytest.raises(m.RegistryLimit):
        reg.quarantine(source, notice_id='q', reason='bounded log', evidence=(source,))
    assert len(reg.events()) == 2


def test_job_outputs_extend_quarantine_trace_without_task_admission(tmp_path):
    m, store, reg, source, config, spec = setup(tmp_path)
    output = store.put_bytes(b'new inert output', 'registry-output', Visibility.PRIVATE)
    job = reg.enqueue(spec)
    claim = reg.claim(job.job_id, owner='worker', claim_key='claim-1')
    obs = reg.reconcile(claim, observation(m, source))
    reg.complete(claim, result(output, (cost(),)), observations=(obs.observation_id,))
    reg.quarantine(source, notice_id='q', reason='new known defect', evidence=(config,))
    trace = reg.trace(source)
    assert output in trace.artifacts and job.job_id in trace.jobs
    with pytest.raises(m.QuarantinedError):
        reg.assert_usable(output)


def test_old_claim_replay_cannot_authorize_another_dispatch(tmp_path):
    m, store, reg, source, config, spec = setup(tmp_path)
    job = reg.enqueue(spec)
    claim = reg.claim(job.job_id, owner='worker', claim_key='once')
    reg.abandon(claim, reason='observed interruption', evidence=(source,))
    with pytest.raises(m.StaleClaim):
        reg.claim(job.job_id, owner='worker', claim_key='once')


def test_active_claim_replay_is_blocked_after_dependency_quarantine(tmp_path):
    m, store, reg, source, config, spec = setup(tmp_path)
    job = reg.enqueue(spec)
    reg.claim(job.job_id, owner='worker', claim_key='once')
    reg.quarantine(source, notice_id='q', reason='concrete new defect', evidence=(config,))
    with pytest.raises(m.QuarantinedError):
        reg.claim(job.job_id, owner='worker', claim_key='once')


def test_missing_lock_and_hardlinked_database_rejected(tmp_path):
    m, store, reg, source, config, spec = setup(tmp_path)
    reg.enqueue(spec)
    (reg.root / 'registry.lock').unlink()
    with pytest.raises(m.RegistryError):
        reg.recover()
    (reg.root / 'registry.lock').touch(mode=0o600)
    os.link(reg.root / 'index.sqlite3', tmp_path / 'linked-database')
    with pytest.raises(m.RegistryError):
        reg.recover()


def test_real_typed_reference_closure_traces_runs_and_checkpoints(tmp_path):
    # The production mutation caught here is treating typed artifacts as opaque
    # metadata, losing TaskBundle/consumed_tasks/provenance dependency edges.
    from test_contracts_examples import examples
    from feature_rl.contracts import ARTIFACT_TYPES
    m, store, reg, source, config, spec = setup(tmp_path)
    published, raw = {}, {}
    def replace(value):
        if isinstance(value, dict):
            if {'sha256', 'kind', 'encoding', 'visibility', 'schema_version'} <= value.keys():
                if value['encoding'] == 'json':
                    return published[value['kind']].model_dump(mode='json')
                key = (value['kind'], value['visibility'])
                if key not in raw:
                    raw[key] = store.put_bytes(repr(key).encode(), key[0], Visibility(key[1]))
                return raw[key].model_dump(mode='json')
            return {key: replace(item) for key, item in value.items()}
        if isinstance(value, list):
            return [replace(item) for item in value]
        return value
    data = examples()
    for kind in ('CandidateRecord', 'SourcePair', 'RequirementContract', 'ScenarioPlan',
                 'EnvironmentRecipe', 'VerifierBundle', 'TaskBundle', 'RolloutRecord', 'TrainingCheckpoint'):
        artifact = ARTIFACT_TYPES[kind].model_validate_json(json.dumps(replace(data[kind])))
        published[kind] = store.put_artifact(artifact)
    reg.register(published['RolloutRecord'])
    reg.register(published['TrainingCheckpoint'])
    reg.quarantine(published['RequirementContract'], notice_id='contract-defect',
                   reason='synthetic dependency defect', evidence=(source,))
    trace = reg.trace(published['RequirementContract'])
    assert published['RolloutRecord'] in trace.runs
    assert published['TrainingCheckpoint'] in trace.checkpoints
