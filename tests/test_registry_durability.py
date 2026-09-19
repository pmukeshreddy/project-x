"""Actual local processes/files; trusted synthetic payloads, no task execution."""
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys

import pytest

from test_registry import setup


CHILD = r'''
import os, sys
from pathlib import Path
from feature_rl.artifacts import ArtifactStore
from feature_rl.contracts import ActorRole
from feature_rl.registry import Registry, JobSpec
from feature_rl.registry import storage
root, objects, spec, point = sys.argv[1:]
reg = Registry(Path(root), ArtifactStore(Path(objects), ActorRole.CONTROLLER))
original_project = storage.Storage.project
original_persist = storage.Storage.persist
original_write = storage.write_all
original_fsync = os.fsync
ready = False
def persist(self, con, event, body, state):
    original_persist(self, con, event, body, state)
    if point == 'uncommitted': os._exit(51)
def project(self, con, directory, raw):
    if raw and point == 'committed': os._exit(51)
    result = original_project(self, con, directory, raw)
    if raw and point == 'ack_uncommitted': os._exit(51)
    return result
def write(fd, data):
    global ready
    if data:
        ready = True
        if point == 'partial':
            os.write(fd, data[:37]); os._exit(51)
    original_write(fd, data)
def sync(fd):
    if ready and point == 'append_before_fsync': os._exit(51)
    original_fsync(fd)
    if ready and point == 'fsync_before_ack': os._exit(51)
storage.Storage.persist = persist
storage.Storage.project = project
storage.write_all = write
os.fsync = sync
reg.enqueue(JobSpec.model_validate_json(spec))
if point == 'reply_lost': os._exit(51)
raise AssertionError('requested crash point was not reached')
'''


@pytest.mark.parametrize('point', ['uncommitted', 'committed', 'partial', 'append_before_fsync',
                                 'fsync_before_ack', 'ack_uncommitted', 'reply_lost'])
def test_real_process_crash_recovers_exact_committed_history(tmp_path, point):
    m, store, reg, source, config, spec = setup(tmp_path)
    child = subprocess.run([sys.executable, '-c', CHILD, str(reg.root), str(store.root),
                            spec.model_dump_json(), point], capture_output=True, timeout=15)
    assert child.returncode == 51, child.stderr.decode()
    before = (reg.root / 'events.jsonl').read_bytes()
    recovered = reg.recover()
    assert recovered.event_count == (0 if point == 'uncommitted' else 1)
    if point == 'partial':
        assert recovered.completed_tail_bytes == 37
    if point in ('append_before_fsync', 'fsync_before_ack', 'ack_uncommitted', 'reply_lost'):
        assert recovered.appended_bytes == 0
    assert (reg.root / 'events.jsonl').read_bytes().startswith(before)
    job = reg.enqueue(spec)
    assert reg.job(job.job_id) == job
    assert len(reg.events()) == 1
    assert reg.recover().appended_bytes == 0
    assert store.get_bytes(source) == b'trusted synthetic input'


@pytest.mark.parametrize('damage', ['corrupt_tail', 'unknown_line', 'missing_acknowledged_bytes', 'index', 'event'])
def test_corrupt_authority_or_projection_blocks_without_rewriting(tmp_path, damage):
    m, store, reg, source, config, spec = setup(tmp_path)
    reg.enqueue(spec)
    journal = reg.root / 'events.jsonl'
    if damage == 'corrupt_tail':
        journal.write_bytes(journal.read_bytes() + b'UNKNOWN')
    elif damage == 'unknown_line':
        journal.write_bytes(journal.read_bytes() + b'{}\n')
    elif damage == 'missing_acknowledged_bytes':
        journal.write_bytes(journal.read_bytes()[:-10])
    else:
        with sqlite3.connect(reg.root / 'index.sqlite3') as con:
            if damage == 'event':
                con.execute('DROP TRIGGER events_no_update')
                con.execute('UPDATE events SET body=?', (b'{}',))
            else:
                con.execute('UPDATE records SET body=? WHERE namespace=?', (b'{}', 'jobs'))
    before = journal.read_bytes()
    with pytest.raises(m.RegistryError):
        reg.recover()
    assert journal.read_bytes() == before


def test_fsync_error_after_database_commit_is_recoverable_without_duplicate_job(tmp_path, monkeypatch):
    from feature_rl.registry import storage
    m, store, reg, source, config, spec = setup(tmp_path)
    original_write, original_fsync = storage.write_all, os.fsync
    wrote = False
    def write(fd, data):
        nonlocal wrote
        original_write(fd, data)
        wrote |= bool(data)
    def sync(fd):
        if wrote:
            raise OSError('injected fsync failure after actual JSONL append')
        return original_fsync(fd)
    with monkeypatch.context() as patch:
        patch.setattr(storage, 'write_all', write)
        patch.setattr(os, 'fsync', sync)
        with pytest.raises(m.RegistryIOError):
            reg.enqueue(spec)
    assert reg.recover().event_count == 1
    job = reg.enqueue(spec)
    assert len(reg.events()) == 1 and reg.job(job.job_id) == job


def test_two_real_processes_cannot_claim_one_job(tmp_path):
    m, store, reg, source, config, spec = setup(tmp_path)
    job = reg.enqueue(spec)
    code = r'''
import json,sys
from pathlib import Path
from feature_rl.artifacts import ArtifactStore
from feature_rl.contracts import ActorRole
from feature_rl.registry import Registry, ClaimConflict
root, objects, job, owner = sys.argv[1:]
reg=Registry(Path(root), ArtifactStore(Path(objects),ActorRole.CONTROLLER))
print('ready',flush=True)
sys.stdin.readline()
try:
    claim=reg.claim(job,owner=owner,claim_key=owner)
    print(claim.model_dump_json(),flush=True)
except ClaimConflict:
    raise SystemExit(3)
'''
    children = [subprocess.Popen([sys.executable, '-c', code, str(reg.root), str(store.root), job.job_id, owner],
                                 stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                for owner in ('worker-1', 'worker-2')]
    try:
        for child in children:
            assert child.stdout.readline().strip() == 'ready'
        for child in children:
            child.stdin.write('go\n')
            child.stdin.flush()
        outputs = [child.communicate(timeout=15) for child in children]
        assert sorted(child.returncode for child in children) == [0, 3], outputs
        assert len(reg.attempts(job.job_id)) == 1
    finally:
        for child in children:
            if child.poll() is None:
                child.kill()
                child.wait()
