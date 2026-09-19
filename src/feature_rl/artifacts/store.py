"""Canonical immutable object store using ordinary JSON and opaque bytes only.

The caller's configured role is an application capability, not OS authentication.
Never mount this store in an untrusted worker; export an inspected allowlist instead.
"""
from __future__ import annotations

import base64
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import stat
import uuid

from pydantic import ValidationError

from feature_rl.contracts import (
    ARTIFACT_TYPES, ARTIFACT_SCHEMA_VERSIONS, ActorRole, ArtifactModel, ArtifactRef, Visibility,
)


class ArtifactError(Exception):
    """Base error for artifact boundary failures."""


class AccessDenied(ArtifactError):
    """The configured role cannot read or publish the visibility."""


class ArtifactIntegrityError(ArtifactError):
    """An object, path, reference, or schema failed validation."""


class ArtifactNotFound(ArtifactError):
    """No committed immutable object exists for this reference."""


ACCESS = {
    ActorRole.SOLVER: frozenset({Visibility.PUBLIC}),
    ActorRole.AUTHOR: frozenset({Visibility.PUBLIC, Visibility.AUTHORING}),
    ActorRole.CONTROLLER: frozenset(Visibility),
    ActorRole.TRAINER: frozenset({Visibility.PUBLIC, Visibility.TRAINING}),
    ActorRole.EVALUATOR: frozenset({Visibility.PUBLIC, Visibility.EVALUATION}),
    ActorRole.REVIEWER: frozenset(Visibility),
}


def canonical_json(value) -> bytes:
    """UTF-8, sorted keys, compact separators, finite numbers; no executable types."""
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'),
                      allow_nan=False).encode('utf-8')


def _reject_duplicates(pairs):
    result = {}
    for key, value in pairs:
        if key in result: raise ArtifactIntegrityError('duplicate JSON key')
        result[key] = value
    return result


def _decode(data: bytes):
    try:
        value = json.loads(data.decode('utf-8'), object_pairs_hook=_reject_duplicates,
                           parse_constant=lambda _: (_ for _ in ()).throw(ValueError('nonfinite JSON')))
        if canonical_json(value) != data:
            raise ArtifactIntegrityError('noncanonical serialized object')
        return value
    except (ValueError, UnicodeError, TypeError) as exc:
        raise ArtifactIntegrityError('invalid canonical JSON') from exc


def _validate_ref(ref):
    if not isinstance(ref, ArtifactRef):
        raise ArtifactIntegrityError('expected ArtifactRef')
    try:
        return ArtifactRef.model_validate(ref)
    except ValidationError as exc:
        raise ArtifactIntegrityError('invalid artifact reference') from exc


def _public_refs(value, permitted):
    if isinstance(value, dict):
        if {'sha256','kind','schema_version','visibility','encoding'} <= value.keys():
            if value['visibility'] not in permitted:
                raise ArtifactIntegrityError('artifact exposes a restricted reference')
        for member in value.values(): _public_refs(member, permitted)
    elif isinstance(value, list):
        for member in value: _public_refs(member, permitted)


class ArtifactStore:
    def __init__(self, root: Path, role: ActorRole):
        if not isinstance(root, Path) or not isinstance(role, ActorRole):
            raise TypeError('root must be Path and role must be ActorRole')
        self.root = root.absolute()
        self.role = role
        with self._directory(create=True):
            pass

    @contextmanager
    def _directory(self, create=False):
        """Walk with directory descriptors; no ancestor or final symlink follows."""
        fd = os.open('/', os.O_RDONLY | os.O_DIRECTORY)
        try:
            for component in self.root.parts[1:]:
                if component in {'.','..'}:
                    raise ArtifactIntegrityError('relative path components are forbidden')
                if create:
                    try:
                        os.mkdir(component, mode=0o700, dir_fd=fd)
                    except FileExistsError:
                        pass
                    else:
                        os.fsync(fd)
                try:
                    next_fd = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                except OSError as exc:
                    raise ArtifactIntegrityError('store path is not a safe directory') from exc
                os.close(fd)
                fd = next_fd
            yield fd
        finally:
            os.close(fd)

    def _authorize(self, visibility, *, write=False):
        if visibility not in ACCESS[self.role] or (write and self.role == ActorRole.REVIEWER):
            raise AccessDenied(f'{self.role.value} cannot {"write" if write else "read"} {visibility.value}')

    def put_bytes(self, data: bytes, kind: str, visibility: Visibility) -> ArtifactRef:
        if type(data) is not bytes:
            raise TypeError('data must be bytes')
        if kind in ARTIFACT_TYPES:
            raise ArtifactIntegrityError('typed artifact kinds require put_artifact')
        return self._put(kind, visibility, 'bytes', base64.b64encode(data).decode('ascii'))

    def put_artifact(self, artifact: ArtifactModel) -> ArtifactRef:
        if type(artifact) not in ARTIFACT_TYPES.values():
            raise ArtifactIntegrityError('unregistered artifact model')
        try:
            # Revalidation catches unsafe model_construct/model_copy mutations.
            checked = type(artifact).model_validate(artifact)
        except ValidationError as exc:
            raise ArtifactIntegrityError('invalid artifact model') from exc
        payload = checked.model_dump(mode='json')
        self._check_exposure(checked.visibility, payload)
        return self._put(checked.kind, checked.visibility, 'json', payload, checked.schema_version)

    @staticmethod
    def _check_exposure(visibility, payload):
        if visibility == Visibility.PUBLIC:
            _public_refs(payload, {'public'})
        elif visibility == Visibility.AUTHORING:
            _public_refs(payload, {'public','authoring'})

    def _put(self, kind, visibility, encoding, payload, schema_version=1):
        # Validate metadata before constructing paths or touching content.
        template = ArtifactRef(sha256='0'*64, kind=kind, schema_version=schema_version,
                               visibility=visibility, encoding=encoding)
        self._authorize(template.visibility, write=True)
        envelope = dict(kind=kind, schema_version=schema_version, visibility=visibility.value,
                        encoding=encoding, payload=payload)
        data = canonical_json(envelope)
        digest = hashlib.sha256(data).hexdigest()
        ref = ArtifactRef(sha256=digest, kind=kind, schema_version=schema_version,
                          visibility=visibility, encoding=encoding)
        name = digest + '.json'
        temp = '.pending-' + uuid.uuid4().hex
        with self._directory() as directory:
            fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=directory)
            try:
                with os.fdopen(fd, 'wb') as stream:
                    stream.write(data)
                    stream.flush()
                    os.fsync(stream.fileno())
                try:
                    # link is atomic create-if-absent, unlike replacing another writer.
                    os.link(temp, name, src_dir_fd=directory, dst_dir_fd=directory, follow_symlinks=False)
                except FileExistsError:
                    existing = self._read_file(directory, name)
                    if existing != data:
                        raise ArtifactIntegrityError('existing object differs: corruption or collision')
                os.fsync(directory)
            finally:
                os.unlink(temp, dir_fd=directory)
                os.fsync(directory)
        return ref

    @staticmethod
    def _read_file(directory, name):
        try:
            fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
        except FileNotFoundError as exc:
            raise ArtifactNotFound(name) from exc
        except OSError as exc:
            raise ArtifactIntegrityError('object path is not a regular file') from exc
        with os.fdopen(fd, 'rb') as stream:
            metadata = os.fstat(stream.fileno())
            if not stat.S_ISREG(metadata.st_mode):
                raise ArtifactIntegrityError('object is not a regular file')
            return stream.read()

    def _get(self, ref):
        ref = _validate_ref(ref)
        self._authorize(ref.visibility)
        with self._directory() as directory:
            data = self._read_file(directory, ref.sha256 + '.json')
        if hashlib.sha256(data).hexdigest() != ref.sha256:
            raise ArtifactIntegrityError('content digest mismatch')
        envelope = _decode(data)
        if not isinstance(envelope, dict) or set(envelope) != {'kind','schema_version','visibility','encoding','payload'}:
            raise ArtifactIntegrityError('invalid object envelope')
        expected = ref.model_dump(mode='json', exclude={'sha256'})
        if {key: envelope[key] for key in expected} != expected:
            raise ArtifactIntegrityError('reference metadata mismatch')
        if type(envelope['schema_version']) is not int:
            raise ArtifactIntegrityError('invalid schema version')
        return envelope['payload']

    def get_bytes(self, ref: ArtifactRef) -> bytes:
        ref = _validate_ref(ref)
        if ref.encoding != 'bytes' or ref.kind in ARTIFACT_TYPES:
            raise ArtifactIntegrityError('reference is not an opaque byte object')
        if ref.schema_version != 1:
            raise ArtifactIntegrityError('unsupported bytes schema version')
        payload = self._get(ref)
        try:
            data = base64.b64decode(payload, validate=True)
            if base64.b64encode(data).decode('ascii') != payload:
                raise ValueError('noncanonical base64')
            return data
        except (ValueError, TypeError) as exc:
            raise ArtifactIntegrityError('invalid byte encoding') from exc

    def get_artifact(self, ref: ArtifactRef) -> ArtifactModel:
        ref = _validate_ref(ref)
        if ref.encoding != 'json' or ref.kind not in ARTIFACT_TYPES:
            raise ArtifactIntegrityError('reference is not a known typed artifact')
        if ref.schema_version != ARTIFACT_SCHEMA_VERSIONS[ref.kind]:
            raise ArtifactIntegrityError('unsupported artifact schema version; revalidate and republish')
        payload = self._get(ref)
        try:
            artifact = ARTIFACT_TYPES[ref.kind].model_validate_json(canonical_json(payload))
        except (ValueError, TypeError) as exc:
            raise ArtifactIntegrityError('invalid artifact payload') from exc
        if artifact.kind != ref.kind or artifact.schema_version != ref.schema_version or artifact.visibility != ref.visibility:
            raise ArtifactIntegrityError('artifact/envelope metadata mismatch')
        self._check_exposure(artifact.visibility, payload)
        return artifact
