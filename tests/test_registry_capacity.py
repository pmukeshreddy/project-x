"""Audited capacity growth retains real durable registry history and identities."""
import json
import sqlite3
import subprocess
import sys

import pytest

from feature_rl.artifacts import ArtifactError, canonical_json
from feature_rl.contracts import Visibility
from test_registry import setup, observation, result, cost


def larger(reg, **values):
    return type(reg.limits).model_validate_json(canonical_json(reg.limits.model_dump(mode='json') | values))


def test_expansion_preserves_history_claims_costs_quarantine_and_live_clients(tmp_path):
    m, store, reg, source, config, spec = setup(tmp_path)
    peer = m.Registry(reg.root, store)
    job = reg.enqueue(spec)
    claim = reg.claim(job.job_id, owner='diagnostic', claim_key='original-claim')
    obs = reg.reconcile(claim, observation(m, source, 3.0))
    reg.quarantine(source, notice_id='preserved', reason='diagnostic defect', evidence=(config,))
    before = (reg.root/'events.jsonl').read_bytes()
    events = reg.events()
    expanded = larger(reg, max_closure_artifacts=5000, max_event_bytes=2097152)
    assert reg.expand_limits(expanded, reason='Full qualification ancestry requires more storage') == expanded
    assert (reg.root/'events.jsonl').read_bytes().startswith(before)
    assert reg.events()[:-1] == events
    assert reg.events()[-1].action == 'expand_limits'
    assert peer.job(job.job_id).spec == spec and peer.limits == expanded
    assert peer.attempts(job.job_id)[0].claim == claim
    assert peer.accounting(job.job_id).observations == (obs,)
    with pytest.raises(m.QuarantinedError):
        peer.assert_usable(source)
    peer.lift_quarantine('preserved', reason='diagnostic repair', evidence=(config,))
    assert peer.complete(claim, result(source, (cost(3.0),)), observations=(obs.observation_id,)).job_id == job.job_id
    count = peer.recover().event_count
    reg.expand_limits(expanded, reason='Idempotent unchanged capacity')
    assert reg.recover().event_count == count
    assert m.Registry(reg.root, store).job(job.job_id).state == 'completed'


def test_actual_closure_over_256_is_verified_then_journals_only_new_records(tmp_path):
    m, store, reg, source, config, spec = setup(tmp_path)
    leaves = tuple(store.put_bytes(str(i).encode(), 'capacity-leaf', Visibility.PRIVATE) for i in range(4999))
    with pytest.raises(m.RegistryLimit):
        reg.register(source, dependencies=leaves)
    reg.expand_limits(larger(reg, max_closure_artifacts=8192, max_event_bytes=4194304), reason='Bounded larger closure')
    first = reg.register(source, dependencies=leaves)
    reg.register(config, dependencies=(source,))
    assert len(reg.events()[-1].data['artifacts']) == 1
    before = reg.recover().event_count
    assert reg.register(source, dependencies=leaves) == first
    assert reg.recover().event_count == before
    with pytest.raises(m.RegistryConflict):
        reg.register(source, dependencies=leaves[:-1])
    # Compact journals still re-read every already-known ancestor's CAS bytes.
    (store.root/(leaves[-1].sha256+'.json')).write_bytes(b'corrupt known ancestor')
    with pytest.raises(ArtifactError):
        reg.assert_usable(config)


@pytest.mark.parametrize('change', [{'max_closure_artifacts': 255}, {'max_attempts_per_job': 4},
                                  {'max_active_jobs': 33}, {'lock_timeout_seconds': 2.0}])
def test_capacity_api_rejects_shrink_or_execution_policy_changes(tmp_path, change):
    m, store, reg, source, config, spec = setup(tmp_path)
    reg.enqueue(spec)
    before = (reg.root/'events.jsonl').read_bytes()
    with pytest.raises(m.RegistryConflict):
        reg.expand_limits(larger(reg, **change), reason='invalid diagnostic change')
    assert (reg.root/'events.jsonl').read_bytes() == before


@pytest.mark.parametrize('damage', ['rewind', 'unaudited_growth'])
def test_unaudited_metadata_growth_or_rewind_fails_closed(tmp_path, damage):
    m, store, reg, source, config, spec = setup(tmp_path)
    reg.enqueue(spec)
    old = reg.limits
    expanded = larger(reg, max_closure_artifacts=5000)
    reg.expand_limits(expanded, reason='Audited growth')
    with sqlite3.connect(reg.root/'index.sqlite3') as con:
        payload = json.loads(con.execute('SELECT configuration FROM meta').fetchone()[0])
        payload['limits'] = (old if damage=='rewind' else larger(reg,max_closure_artifacts=6000)).model_dump(mode='json')
        con.execute('UPDATE meta SET configuration=?', (canonical_json(payload),))
    before = (reg.root/'events.jsonl').read_bytes()
    with pytest.raises(m.RegistryIntegrityError):
        m.Registry(reg.root, store, limits=old)
    assert (reg.root/'events.jsonl').read_bytes() == before


def test_capacity_expansion_can_append_when_original_event_capacity_is_full(tmp_path):
    m, store, reg, source, config, spec = setup(tmp_path, max_events=1)
    job=reg.enqueue(spec)
    reg.expand_limits(larger(reg,max_events=8),reason='Additional bounded bookkeeping capacity')
    assert reg.job(job.job_id)==job and reg.recover().event_count==2


def test_old_live_handle_can_recover_journal_larger_than_its_original_capacity(tmp_path):
    m, store, reg, source, config, spec=setup(tmp_path,max_journal_bytes=2048,max_event_bytes=2048)
    peer=m.Registry(reg.root,store,limits=reg.limits)
    reg.register(source)
    grown=larger(reg,max_journal_bytes=65536,max_event_bytes=8192)
    reg.expand_limits(grown,reason='Bounded bookkeeping expansion '+('x'*1800))
    assert (reg.root/'events.jsonl').stat().st_size>2048
    assert peer.recover().event_count==2 and peer.limits==grown


def test_old_live_handle_completes_with_all_observations_after_event_capacity_expansion(tmp_path):
    m, store, reg, source, config, spec = setup(tmp_path, max_events=4)
    peer = m.Registry(reg.root, store, limits=reg.limits)
    job = reg.enqueue(spec)
    claim = reg.claim(job.job_id, owner='diagnostic', claim_key='original-claim')
    grown = larger(reg, max_events=100)
    reg.expand_limits(grown, reason='Additional bounded accounting capacity')
    snapshots = tuple(reg.reconcile(claim, observation(m, source, float(i), upstream='call-'+str(i)))
        for i in range(5))
    ordered = tuple(sorted(snapshots, key=lambda item: item.observation_id))
    value = result(source, tuple(cost for item in ordered for cost in item.observation.costs))
    before = (reg.root/'events.jsonl').read_bytes()
    assert peer.limits.max_events == 4
    completed = peer.complete(claim, value, observations=tuple(item.observation_id for item in snapshots))
    assert peer.limits == grown and completed.result == value
    assert completed.result_observations == tuple(item.observation_id for item in ordered)
    assert peer.attempts(job.job_id)[0].claim == claim
    assert (reg.root/'events.jsonl').read_bytes().startswith(before)
    # Adoption retains the exact active bound; it does not remove validation.
    before = (reg.root/'events.jsonl').read_bytes()
    with pytest.raises(m.RegistryLimit, match='completion snapshot identities'):
        peer.complete(claim, value, observations=tuple(str(i) for i in range(101)))
    assert (reg.root/'events.jsonl').read_bytes() == before


def test_expansion_does_not_restore_an_exhausted_attempt_budget(tmp_path):
    m, store, reg, source, config, spec=setup(tmp_path)
    job=reg.enqueue(spec.model_copy(update={'attempt_limit':1}))
    claim=reg.claim(job.job_id,owner='diagnostic',claim_key='one-dispatch')
    reg.abandon(claim,reason='Observed diagnostic interruption',evidence=(source,))
    reg.expand_limits(larger(reg,max_jobs=2000),reason='More bookkeeping records only')
    assert reg.job(job.job_id).state=='exhausted'
    with pytest.raises(m.AttemptLimit):
        reg.retry(job.job_id,reason='Not an authorized new attempt',evidence=(config,))


def test_trace_supports_more_than_1024_actual_registered_descendants(tmp_path):
    m, store, reg, source, config, spec = setup(tmp_path)
    reg.expand_limits(larger(reg,max_closure_artifacts=2000,max_event_bytes=2097152),reason='Large diagnostic dependency graph')
    leaves=tuple(store.put_bytes(str(i).encode(),'trace-leaf',Visibility.PRIVATE) for i in range(1100))
    # Batch inert verified CAS records through the real transaction/replay path,
    # avoiding 1100 separate fsync transactions for this response-bound check.
    reg._storage.change(lambda state: ('register','diagnostic-trace-batch',{'artifacts':reg._verify(
        state,leaves,{leaf.sha256:(source,) for leaf in leaves})}))
    assert len(reg.trace(source).artifacts)==1101


@pytest.mark.parametrize('point',['uncommitted','committed'])
def test_capacity_event_and_metadata_commit_atomically_across_process_death(tmp_path,point):
    m, store, reg, source, config, spec = setup(tmp_path)
    job=reg.enqueue(spec);before=(reg.root/'events.jsonl').read_bytes()
    code=r'''
import os,sys
from pathlib import Path
from feature_rl.artifacts import ArtifactStore
from feature_rl.contracts import ActorRole
from feature_rl.registry import Registry
from feature_rl.registry import storage
root,objects,point=sys.argv[1:]
reg=Registry(Path(root),ArtifactStore(Path(objects),ActorRole.CONTROLLER))
original_persist=storage.Storage.persist;original_project=storage.Storage.project
def persist(self,con,event,body,state):
    original_persist(self,con,event,body,state)
    if event.action=='expand_limits' and point=='uncommitted':os._exit(51)
def project(self,con,directory,raw):
    if point=='committed' and any(b'"action":"expand_limits"' in body for body in raw):os._exit(51)
    return original_project(self,con,directory,raw)
storage.Storage.persist=persist;storage.Storage.project=project
reg.expand_limits(reg.limits.model_copy(update={'max_closure_artifacts':5000}),reason='Atomic capacity diagnostic')
raise AssertionError('crash point was not reached')
'''
    child=subprocess.run([sys.executable,'-c',code,str(reg.root),str(store.root),point],capture_output=True,timeout=15)
    assert child.returncode==51,child.stderr.decode()
    assert reg.recover().event_count==(1 if point=='uncommitted' else 2)
    assert reg.limits.max_closure_artifacts==(256 if point=='uncommitted' else 5000)
    assert reg.job(job.job_id)==job and (reg.root/'events.jsonl').read_bytes().startswith(before)
