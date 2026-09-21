"""Unchanged private registry files do not need another historical replay."""
import sqlite3
import pytest
from test_registry import setup


def test_unchanged_reads_and_local_appends_reuse_verified_state(tmp_path, monkeypatch):
    _, _, registry, source, _, spec = setup(tmp_path)
    job = registry.enqueue(spec)
    def replay(_):
        raise AssertionError('unchanged committed history was replayed')
    monkeypatch.setattr(registry._storage, 'load', replay)
    assert registry.job(job.job_id) == job
    registry.register(source)
    assert registry.job(job.job_id) == job


def test_changed_database_invalidates_snapshot_and_still_rejects_corruption(tmp_path):
    module, _, registry, _, _, spec = setup(tmp_path)
    job = registry.enqueue(spec)
    assert registry.job(job.job_id) == job
    with sqlite3.connect(registry.root/'index.sqlite3') as con:
        con.execute("DELETE FROM records WHERE namespace='jobs'")
    with pytest.raises(module.RegistryIntegrityError):
        registry.job(job.job_id)


def test_views_copy_only_the_result_and_do_not_expose_cached_event_data(tmp_path, monkeypatch):
    from feature_rl.registry import storage
    from feature_rl.registry.state import State
    _, _, registry, _, _, spec = setup(tmp_path)
    job = registry.enqueue(spec)
    original = storage.deepcopy
    def copy_result(value):
        if isinstance(value, tuple) and value and isinstance(value[0], State):
            raise AssertionError('a read copied the complete retained history')
        return original(value)
    monkeypatch.setattr(storage, 'deepcopy', copy_result)
    assert registry.job(job.job_id) == job
    event = registry.events()[0]
    expected = original(event.data)
    event.data.clear()
    assert registry.events()[0].data == expected
