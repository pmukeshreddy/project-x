"""Bounded-reader regressions use real store objects and test-local I/O faults."""
import hashlib
import json
import os
from pathlib import Path

import pytest

from feature_rl import artifacts as a, contracts as c
from feature_rl.artifacts import store as implementation
from test_contracts_examples import examples


def size_error():
    assert hasattr(a, 'ArtifactSizeLimitError'), 'public typed size-limit failure is missing'
    return a.ArtifactSizeLimitError


def typed_object(store):
    artifact = c.EnvironmentRecipe.model_validate_json(json.dumps(examples()['EnvironmentRecipe']))
    return store.put_artifact(artifact), artifact


def envelope_size(root, reference):
    return (root / (reference.sha256 + '.json')).stat().st_size


@pytest.mark.parametrize('data', [b'', b'a', b'ab', b'abc', b'abcd', b'abcde', b'abcdef'])
def test_bounded_bytes_exact_envelope_and_decoded_boundaries(tmp_path, data):
    store = a.ArtifactStore(tmp_path, c.ActorRole.CONTROLLER)
    reference = store.put_bytes(data, 'source', c.Visibility.PRIVATE)
    length = envelope_size(tmp_path, reference)
    assert store.get_bytes(reference, max_envelope_bytes=length, max_payload_bytes=len(data)) == data
    assert store.get_bytes(reference) == data
    assert store.get_bytes(reference, max_envelope_bytes=None, max_payload_bytes=None) == data
    with pytest.raises(size_error()):
        store.get_bytes(reference, max_envelope_bytes=length - 1)
    if data:
        with pytest.raises(size_error()):
            store.get_bytes(reference, max_payload_bytes=len(data) - 1)


def test_bounded_typed_artifact_exact_boundary_and_oversize(tmp_path):
    store = a.ArtifactStore(tmp_path, c.ActorRole.CONTROLLER)
    reference, expected = typed_object(store)
    length = envelope_size(tmp_path, reference)
    assert store.get_artifact(reference, max_envelope_bytes=length) == expected
    assert store.get_artifact(reference) == expected
    with pytest.raises(size_error()) as failure:
        store.get_artifact(reference, max_envelope_bytes=length - 1)
    assert failure.value.limit_name == 'max_envelope_bytes'
    assert failure.value.limit == length - 1
    assert failure.value.observed_bytes == length


@pytest.mark.parametrize('method,keyword', [
    ('get_bytes', 'max_envelope_bytes'), ('get_bytes', 'max_payload_bytes'),
    ('get_artifact', 'max_envelope_bytes'),
])
@pytest.mark.parametrize('value,error', [
    (True, TypeError), (False, TypeError), (1.0, TypeError), ('1', TypeError),
    (float('nan'), TypeError), (-1, ValueError),
])
def test_bounded_limit_arguments_reject_coercion_and_negatives(tmp_path, method, keyword, value, error):
    store = a.ArtifactStore(tmp_path, c.ActorRole.CONTROLLER)
    reference = typed_object(store)[0] if method == 'get_artifact' else store.put_bytes(b'a', 'source', c.Visibility.PRIVATE)
    with pytest.raises(error):
        getattr(store, method)(reference, **{keyword: value})


def test_bounded_zero_envelope_and_huge_integer_caps(tmp_path):
    store = a.ArtifactStore(tmp_path, c.ActorRole.CONTROLLER)
    reference = store.put_bytes(b'', 'source', c.Visibility.PRIVATE)
    with pytest.raises(size_error()):
        store.get_bytes(reference, max_envelope_bytes=0)
    # Valid large integer bounds must not request an impossibly large one-shot read allocation.
    assert store.get_bytes(reference, max_envelope_bytes=10**100, max_payload_bytes=0) == b''


@pytest.mark.parametrize('typed', [False, True])
def test_bounded_stat_excess_rejected_before_read_and_json_decode(tmp_path, monkeypatch, typed):
    store = a.ArtifactStore(tmp_path, c.ActorRole.CONTROLLER)
    reference = typed_object(store)[0] if typed else store.put_bytes(b'payload', 'source', c.Visibility.PRIVATE)
    cap = envelope_size(tmp_path, reference) - 1
    real_fdopen = os.fdopen

    class Unreadable:
        def __init__(self, stream): self.stream = stream
        def __enter__(self): return self
        def __exit__(self, *args): return self.stream.__exit__(*args)
        def fileno(self): return self.stream.fileno()
        def read(self, *args): raise AssertionError('oversized stat must be rejected before read')

    def forbidden_json(*args): raise AssertionError('oversized envelope reached JSON decoding')
    monkeypatch.setattr(implementation.os, 'fdopen', lambda *args, **kwargs: Unreadable(real_fdopen(*args, **kwargs)))
    monkeypatch.setattr(implementation, '_decode', forbidden_json)
    method = store.get_artifact if typed else store.get_bytes
    with pytest.raises(size_error()): method(reference, max_envelope_bytes=cap)


def test_bounded_file_growth_after_stat_reads_at_most_cap_plus_one(tmp_path, monkeypatch):
    store = a.ArtifactStore(tmp_path, c.ActorRole.CONTROLLER)
    reference = store.put_bytes(b'initial', 'source', c.Visibility.PRIVATE)
    path = tmp_path / (reference.sha256 + '.json')
    cap = path.stat().st_size
    original_fstat, original_fdopen = os.fstat, os.fdopen
    observations = {'bytes': 0, 'requests': [], 'grew': False}

    def grow_after_stat(fd):
        observed = original_fstat(fd)
        if not observations['grew']:
            append_fd = os.open(path, os.O_WRONLY | os.O_APPEND)
            try: os.write(append_fd, b'x' * 1000)
            finally: os.close(append_fd)
            observations['grew'] = True
        return observed

    class Reader:
        def __init__(self, stream): self.stream = stream
        def __enter__(self): return self
        def __exit__(self, *args): return self.stream.__exit__(*args)
        def fileno(self): return self.stream.fileno()
        def read(self, size=-1):
            assert 0 <= size <= cap + 1, 'reader requested unbounded data after stat'
            observations['requests'].append(size)
            data = self.stream.read(size)
            observations['bytes'] += len(data)
            return data

    def forbidden_json(*args): raise AssertionError('growing oversized envelope reached JSON decoding')
    monkeypatch.setattr(implementation.os, 'fstat', grow_after_stat)
    monkeypatch.setattr(implementation.os, 'fdopen', lambda *args, **kwargs: Reader(original_fdopen(*args, **kwargs)))
    monkeypatch.setattr(implementation, '_decode', forbidden_json)
    with pytest.raises(size_error()) as failure:
        store.get_bytes(reference, max_envelope_bytes=cap)
    assert observations['grew'] and observations['requests']
    assert observations['bytes'] == cap + 1
    assert failure.value.observed_bytes == cap + 1


@pytest.mark.parametrize('data', [b'a', b'ab', b'abc', b'abcd'])
def test_bounded_payload_excess_rejected_before_base64_decode(tmp_path, monkeypatch, data):
    store = a.ArtifactStore(tmp_path, c.ActorRole.CONTROLLER)
    reference = store.put_bytes(data, 'source', c.Visibility.PRIVATE)
    def forbidden_decode(*args, **kwargs): raise AssertionError('oversized payload allocated decoded bytes')
    monkeypatch.setattr(implementation.base64, 'b64decode', forbidden_decode)
    with pytest.raises(size_error()) as failure:
        store.get_bytes(reference, max_envelope_bytes=envelope_size(tmp_path, reference), max_payload_bytes=len(data)-1)
    assert failure.value.limit_name == 'max_payload_bytes'
    assert failure.value.observed_bytes == len(data)


@pytest.mark.parametrize('payload', ['YR==', 'YWJ=', '====', 'a===', 'YQ=', 'YQ===', 'Y Q=', 'éééé', 3])
def test_bounded_calls_still_reject_invalid_base64(tmp_path, payload):
    store = a.ArtifactStore(tmp_path, c.ActorRole.CONTROLLER)
    raw = a.canonical_json(dict(kind='source', schema_version=1, visibility='private', encoding='bytes', payload=payload))
    digest = hashlib.sha256(raw).hexdigest()
    (tmp_path / (digest + '.json')).write_bytes(raw)
    reference = c.ArtifactRef(sha256=digest, kind='source', schema_version=1, visibility=c.Visibility.PRIVATE, encoding='bytes')
    with pytest.raises(a.ArtifactIntegrityError):
        store.get_bytes(reference, max_envelope_bytes=len(raw), max_payload_bytes=100)


def test_bounded_reads_preserve_visibility_metadata_hash_and_symlink_defenses(tmp_path):
    store = a.ArtifactStore(tmp_path, c.ActorRole.CONTROLLER)
    reference = store.put_bytes(b'secret', 'source', c.Visibility.PRIVATE)
    limits = dict(max_envelope_bytes=1024, max_payload_bytes=1024)
    with pytest.raises(a.AccessDenied):
        a.ArtifactStore(tmp_path, c.ActorRole.AUTHOR).get_bytes(reference, **limits)
    with pytest.raises(a.ArtifactIntegrityError):
        store.get_bytes(reference.model_copy(update={'visibility': c.Visibility.PUBLIC}), **limits)
    path = tmp_path / (reference.sha256 + '.json')
    original = path.read_bytes()
    path.write_bytes(original.replace(b'c2VjcmV0', b'c2VjcmV1'))
    with pytest.raises(a.ArtifactIntegrityError): store.get_bytes(reference, **limits)
    target = tmp_path / 'target'; target.write_bytes(original); path.unlink(); path.symlink_to(target)
    with pytest.raises(a.ArtifactIntegrityError): store.get_bytes(reference, **limits)


def test_bounded_typed_reads_preserve_public_reference_exposure_checks(tmp_path):
    value = examples()['RequirementContract']; value['visibility'] = 'public'
    raw = a.canonical_json(dict(kind='RequirementContract', schema_version=1, visibility='public', encoding='json', payload=value))
    digest = hashlib.sha256(raw).hexdigest()
    (tmp_path / (digest + '.json')).write_bytes(raw)
    reference = c.ArtifactRef(sha256=digest, kind='RequirementContract', schema_version=1, visibility=c.Visibility.PUBLIC, encoding='json')
    with pytest.raises(a.ArtifactIntegrityError):
        a.ArtifactStore(tmp_path, c.ActorRole.CONTROLLER).get_artifact(reference, max_envelope_bytes=len(raw))


def test_bounded_duplicate_comparison_retains_corruption_error(tmp_path):
    store = a.ArtifactStore(tmp_path, c.ActorRole.CONTROLLER)
    reference = store.put_bytes(b'a', 'source', c.Visibility.PRIVATE)
    path = tmp_path / (reference.sha256 + '.json')
    path.write_bytes(b'x' * 10000)
    with pytest.raises(a.ArtifactIntegrityError): store.put_bytes(b'a', 'source', c.Visibility.PRIVATE)
    assert path.stat().st_size == 10000
