"""Controller-side, lazy resolution against immutable trusted PyPI responses.

Registry metadata and wheel ZIPs are inert inputs.  A resolution captures each
project index once, retains content-addressed wheel bytes, and publishes the
index references needed for entirely offline validation of its selected pins.
"""
from collections.abc import Collection, Iterator
from dataclasses import dataclass
import hashlib
import json
import re
import time
from urllib.parse import unquote, urlsplit

from packaging.requirements import Requirement
from packaging.specifiers import InvalidSpecifier, SpecifierSet
from packaging.utils import InvalidWheelFilename, canonicalize_name, parse_wheel_filename
from packaging.version import Version
from pydantic import ValidationError

from feature_rl.artifacts import canonical_json
from feature_rl.contracts import ArtifactRef, Visibility
from feature_rl.intake.sources import BoundedHttpFetcher, SourceFetchError, SourceTooLarge
from .candidates import CatalogWheel, _Wheel, _bytes, _inspect_wheel, _reference, _safe_requirement, _tags, _target
from .models import DependencyUnavailable, EnvironmentError, PolicyRejected, SourceRejected
from .profiles import WheelPin

INDEX = 'https://pypi.org/simple/'
INDEX_BYTES = 16 * 1024 * 1024
SNAPSHOT_BYTES = 16 * 1024 * 1024
_INDEX_MEDIA_TYPE = 'application/vnd.pypi.simple.v1+json'
_SNAPSHOT_VERSION = 'pypi-index-snapshot-v1'


@dataclass(frozen=True)
class _IndexedWheel:
    pin: WheelPin
    url: str
    version: Version
    build: tuple
    rank: int
    size: int | None


def _name(value):
    if (not isinstance(value, str)
            or not re.fullmatch(r'[a-z0-9](?:[a-z0-9-]*[a-z0-9])?', value)
            or canonicalize_name(value) != value):
        raise PolicyRejected('package index requires a safe canonical distribution name')
    return value


def _json(raw):
    def pairs(values):
        result = {}
        for key, value in values:
            if key in result:
                raise PolicyRejected('trusted package index has duplicate JSON keys')
            result[key] = value
        return result

    def constant(value):
        raise ValueError('nonfinite JSON value')

    try:
        return json.loads(raw, object_pairs_hook=pairs, parse_constant=constant)
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise PolicyRejected('invalid trusted package index JSON') from exc


def _authoring_reference(value, *kinds):
    label = '/'.join(kinds)
    try:
        ref = (ArtifactRef.model_validate(value) if isinstance(value, ArtifactRef)
               else ArtifactRef.model_validate_json(canonical_json(value)))
    except (TypeError, ValueError, RecursionError) as exc:
        raise PolicyRejected('invalid trusted ' + label + ' reference') from exc
    if ref.kind not in kinds:
        raise PolicyRejected('unexpected trusted ' + label + ' reference kind')
    _reference(ref, ref.kind)
    if ref.visibility != Visibility.AUTHORING:
        raise PolicyRejected('trusted ' + label + ' reference must be authoring-only')
    return ref


def _index_files(raw, name, profile, max_wheel_bytes, check_deadline=None):
    """Parse every compatible indexed wheel without acquiring wheel payloads."""
    value = _json(raw)
    if (not isinstance(value, dict) or not isinstance(value.get('name'), str)
            or canonicalize_name(value['name']) != name
            or not isinstance(value.get('meta'), dict)
            or not isinstance(value['meta'].get('api-version'), str)
            or not re.fullmatch(r'1\.\d+', value['meta']['api-version'])
            or not isinstance(value.get('files'), list)):
        raise PolicyRejected('invalid trusted package index snapshot')
    ranks = {tag: rank for rank, tag in reversed(tuple(enumerate(profile.compatible_tags)))}
    files, declarations = {}, {}
    for item in value['files']:
        if check_deadline is not None:
            check_deadline()
        if not isinstance(item, dict):
            raise PolicyRejected('invalid trusted package index file record')
        filename = item.get('filename', '')
        if not isinstance(filename, str):
            raise PolicyRejected('invalid trusted package index filename')
        if not filename.endswith('.whl'):
            continue
        # Reject contradictory records before compatibility/yank filters. A
        # filtered duplicate cannot silently change whether a wheel is trusted.
        previous = declarations.setdefault(filename, item)
        if previous != item:
            raise PolicyRejected('trusted package index has conflicting wheel records')
        yanked = item.get('yanked', False)
        if not isinstance(yanked, (bool, str)):
            raise PolicyRejected('invalid trusted package index yanked status')
        if yanked is True or isinstance(yanked, str):
            continue
        # Invalid vendor filenames/tags/specifiers cannot contribute executable
        # input. Other releases may still provide a valid dependency closure.
        try:
            if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._+!-]*\.whl', filename):
                continue
            _tags('-'.join(filename[:-4].split('-')[-3:]))
            package, version, build, tags = parse_wheel_filename(filename)
            requires_python = item.get('requires-python')
            if requires_python is not None and not isinstance(requires_python, str):
                continue
            python_specifier = SpecifierSet(requires_python or '')
        except (InvalidWheelFilename, InvalidSpecifier, PolicyRejected, ValueError):
            continue
        if package != name:
            raise PolicyRejected('index wheel distribution name mismatch')
        compatible = {str(tag) for tag in tags}.intersection(ranks)
        if (not compatible or not python_specifier.contains(
                profile.interpreter_version, prereleases=True)):
            continue
        size = item.get('size')
        if size is not None and (type(size) is not int or size < 0):
            raise PolicyRejected('invalid indexed wheel size')
        hashes = item.get('hashes')
        digest = hashes.get('sha256') if isinstance(hashes, dict) else None
        if not isinstance(digest, str) or not re.fullmatch(r'[0-9a-f]{64}', digest):
            raise PolicyRejected('trusted index wheel lacks an exact SHA256')
        url = item.get('url')
        if not isinstance(url, str):
            raise PolicyRejected('invalid indexed wheel origin')
        try:
            parsed = urlsplit(url)
        except ValueError as exc:
            raise PolicyRejected('invalid indexed wheel origin') from exc
        if (parsed.scheme != 'https' or parsed.netloc != 'files.pythonhosted.org'
                or not parsed.path.startswith('/packages/') or parsed.query or parsed.fragment
                or unquote(parsed.path.rsplit('/', 1)[-1]) != filename
                or any(ord(char) < 33 or ord(char) > 126 for char in url)):
            raise PolicyRejected('wheel download is outside the trusted PyPI artifact origin')
        try:
            pin = WheelPin(name=name, version=str(version), filename=filename, sha256=digest)
        except ValidationError:
            continue
        option = _IndexedWheel(pin, url, version, build, min(ranks[tag] for tag in compatible), size)
        previous = files.setdefault(filename, option)
        if previous != option:
            raise PolicyRejected('trusted package index has conflicting wheel records')
    ordered = [item for item in files.values() if item.size is None or item.size <= max_wheel_bytes]
    ordered.sort(key=lambda item: (item.pin.filename, item.pin.sha256))
    ordered.sort(key=lambda item: item.build, reverse=True)
    ordered.sort(key=lambda item: item.rank)
    ordered.sort(key=lambda item: item.version, reverse=True)
    if check_deadline is not None:
        check_deadline()
    return ordered


def _load_index(store, value, name, profile, max_wheel_bytes, check_deadline=None):
    ref = _authoring_reference(value, 'package-index', 'package-index-absence')
    raw = _bytes(store, ref, INDEX_BYTES)
    if ref.kind == 'package-index-absence':
        absent = _json(raw)
        if (not isinstance(absent, dict) or set(absent) != {'source', 'status'}
                or absent['source'] != INDEX + name + '/'
                or type(absent['status']) is not int or absent['status'] != 404
                or canonical_json(absent) != raw):
            raise PolicyRejected('invalid retained package index absence provenance')
        return ref, ()
    return ref, _index_files(raw, name, profile, max_wheel_bytes, check_deadline)


class _VerifiedPayload:
    """Let ZIP inspection use already hash-verified bytes without another IO read."""
    def __init__(self, ref, data):
        self.ref, self.data = ref, data

    def get_bytes(self, ref, *, max_envelope_bytes, max_payload_bytes):
        if ref != self.ref or len(self.data) > max_payload_bytes:
            raise PolicyRejected('verified wheel payload binding changed')
        return self.data


class RegistryCatalog:
    """Lazy trusted registry provider. The caller holds the runtime state lock.

    ``cache_key`` is the dependency request identity, including resolver code.
    Each project page is captured once for that identity, even across interrupted
    attempts. Another dependency request may capture a newer registry response.
    """
    def __init__(self, runtime, policy, cache_key: str, *, available=()):
        if not isinstance(cache_key, str) or not re.fullmatch(r'[0-9a-f]{64}', cache_key):
            raise PolicyRejected('invalid registry resolution cache identity')
        if policy.profile is None:
            raise DependencyUnavailable('registry resolution requires a fixed worker runtime')
        _target(policy.profile)
        self.runtime, self.policy = runtime, policy
        self.store, self.state = runtime.store, runtime.engine.state
        self.cache_key = cache_key
        self.deadline = time.monotonic() + runtime.engine.image_seconds
        self.indexes, self.index_refs, self.wheels = {}, {}, {}
        self.available = {}
        for ref in available:
            self.check_deadline()
            try:
                ref = (ArtifactRef.model_validate(ref) if isinstance(ref, ArtifactRef)
                       else ArtifactRef.model_validate_json(canonical_json(ref)))
            except (TypeError, ValueError, RecursionError) as exc:
                raise PolicyRejected('invalid available dependency wheel reference') from exc
            _reference(ref, 'dependency-wheel')
            digest = hashlib.sha256(_bytes(self.store, ref, policy.max_staging_bytes)).hexdigest()
            self.check_deadline()
            prior = self.available.get(digest)
            if prior is None or ref.sha256 < prior.sha256:
                self.available[digest] = ref

    def check_deadline(self):
        if time.monotonic() >= self.deadline:
            raise DependencyUnavailable('trusted registry dependency resolution deadline exceeded')

    def _read_state(self, name):
        try:
            value = self.state.read(name)
            if value is None:
                raise PolicyRejected('null registry cache record')
            return value
        except FileNotFoundError:
            return None
        except (EnvironmentError, OSError, ValueError, RecursionError) as exc:
            raise PolicyRejected('unavailable or corrupt registry cache record') from exc

    def _fetch(self, url, cap, accept, *, allow_absent=False):
        self.check_deadline()
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise DependencyUnavailable('trusted registry dependency resolution deadline exceeded')
        try:
            fetched = BoundedHttpFetcher(max_bytes=cap,
                timeout_seconds=min(30.0, remaining)).fetch(
                    url, edit_history='not_applicable', accept=accept)
        except SourceTooLarge as exc:
            raise DependencyUnavailable('trusted registry response exceeds the byte budget') from exc
        except SourceFetchError as exc:
            if (allow_absent and accept == _INDEX_MEDIA_TYPE and type(exc) is SourceFetchError
                    and str(exc) == 'source returned HTTP 404'):
                # The fetcher enforces the requested HTTPS origin even on
                # redirects. Only this explicit response means no project;
                # timeouts and other transport failures remain unavailable.
                self.check_deadline()
                return None
            if 'invalid Content-Length' in str(exc):
                raise PolicyRejected('trusted registry transport metadata is corrupt') from exc
            raise DependencyUnavailable('trusted registry dependency fetch unavailable: ' + str(exc)) from exc
        except ValueError as exc:
            raise PolicyRejected('trusted registry transport provenance changed') from exc
        self.check_deadline()
        if (fetched.url != url or fetched.status_code != 200
                or not fetched.redirect_chain or fetched.redirect_chain[0] != url):
            raise PolicyRejected('trusted registry response provenance mismatch')
        origin = urlsplit(url).netloc
        for hop in fetched.redirect_chain:
            parsed = urlsplit(hop)
            if (parsed.scheme != 'https' or parsed.netloc != origin
                    or parsed.username is not None or parsed.password is not None):
                raise PolicyRejected('trusted registry redirect changed origin')
        if accept == _INDEX_MEDIA_TYPE and fetched.media_type.lower() != _INDEX_MEDIA_TYPE:
            raise PolicyRejected('trusted package index returned an unexpected media type')
        return fetched.body

    def _index(self, name):
        self.check_deadline()
        if name in self.indexes:
            return self.indexes[name]
        record_name = 'registry-index-' + self.cache_key + '-' + hashlib.sha256(name.encode()).hexdigest() + '.json'
        source = INDEX + name + '/'
        cached = self._read_state(record_name)
        if cached is None:
            raw = self._fetch(source, INDEX_BYTES, _INDEX_MEDIA_TYPE, allow_absent=True)
            kind = 'package-index'
            if raw is None:
                raw = canonical_json({'source': source, 'status': 404})
                kind = 'package-index-absence'
            ref = self.store.put_bytes(raw, kind, Visibility.AUTHORING)
            self.state.write(record_name, {'identity': self.cache_key, 'name': name,
                                          'source': source, 'index': ref.model_dump(mode='json')})
            # Retain before parsing: an interrupted or rejected interpretation
            # must not replace this request's response with a later index page.
        else:
            if (not isinstance(cached, dict) or set(cached) != {'identity', 'name', 'source', 'index'}
                    or cached['identity'] != self.cache_key or cached['name'] != name
                    or cached['source'] != source):
                raise PolicyRejected('cached package index provenance changed')
            ref = cached['index']
        ref, options = _load_index(self.store, ref, name, self.policy.profile,
                                   self.policy.max_staging_bytes, self.check_deadline)
        self.indexes[name], self.index_refs[name] = options, ref
        return options

    def _acquire(self, option):
        self.check_deadline()
        pin = option.pin
        key = (pin.filename, pin.sha256)
        if key in self.wheels:
            return self.wheels[key]
        record_name = 'registry-wheel-' + pin.sha256 + '.json'
        ref = self.available.get(pin.sha256)
        if ref is None:
            cached = self._read_state(record_name)
            if cached is not None:
                if (not isinstance(cached, dict) or set(cached) != {'sha256', 'artifact'}
                        or cached['sha256'] != pin.sha256):
                    raise PolicyRejected('cached wheel payload identity changed')
                ref = _authoring_reference(cached['artifact'], 'dependency-wheel')
        if ref is None:
            data = self._fetch(option.url, self.policy.max_staging_bytes, 'application/octet-stream')
        else:
            _reference(ref, 'dependency-wheel')
            data = _bytes(self.store, ref, self.policy.max_staging_bytes)
        if hashlib.sha256(data).hexdigest() != pin.sha256:
            raise PolicyRejected('wheel payload differs from trusted index SHA256')
        if option.size is not None and len(data) != option.size:
            raise PolicyRejected('wheel payload differs from trusted index size')
        if ref is None:
            ref = self.store.put_bytes(data, 'dependency-wheel', Visibility.AUTHORING)
        # Persist all verified raw bytes before inspecting vendor metadata. A
        # failed or interrupted search never needs to download them again.
        if ref.visibility == Visibility.AUTHORING:
            self.state.write(record_name, {'sha256': pin.sha256, 'artifact': ref.model_dump(mode='json')})
        item = CatalogWheel(pin=pin, artifact=ref)
        try:
            wheel = _inspect_wheel(_VerifiedPayload(ref, data), item, self.policy)
            for requirement in wheel.requirements:
                _safe_requirement(str(requirement))
        except (PolicyRejected, SourceRejected):
            wheel = None
        if wheel is not None and (not wheel.tags.intersection(self.policy.profile.compatible_tags)
                or not wheel.requires_python.contains(self.policy.profile.interpreter_version, prereleases=True)):
            wheel = None
        self.check_deadline()
        self.wheels[key] = wheel
        return wheel

    def options(self, name, requirements: tuple[Requirement, ...],
                hashes: Collection[str] | None, prefer_prereleases: bool = False) -> Iterator[_Wheel]:
        name = _name(name)
        limits = tuple(_safe_requirement(str(requirement)) for requirement in requirements)
        if any(canonicalize_name(requirement.name) != name for requirement in limits):
            raise PolicyRejected('registry requirement distribution name mismatch')
        allowed_hashes = None if hashes is None else frozenset(hashes)
        if allowed_hashes is not None and any(not isinstance(value, str)
                or not re.fullmatch(r'[0-9a-f]{64}', value) for value in allowed_hashes):
            raise PolicyRejected('invalid candidate dependency SHA256 restriction')
        options = self._index(name)
        if not (prefer_prereleases or any(requirement.specifier.prereleases for requirement in limits)):
            options = sorted(options, key=lambda item: item.version.is_prerelease)
        for option in options:
            self.check_deadline()
            if (allowed_hashes is not None and option.pin.sha256 not in allowed_hashes
                    or not all(requirement.specifier.contains(option.version, prereleases=True)
                               for requirement in limits)):
                continue
            wheel = self._acquire(option)
            if wheel is not None:
                yield wheel

    def freeze(self) -> ArtifactRef:
        self.check_deadline()
        value = {'version': _SNAPSHOT_VERSION, 'source': INDEX,
                 'indexes': {name: self.index_refs[name].model_dump(mode='json')
                             for name in sorted(self.index_refs)}}
        raw = canonical_json(value)
        if len(raw) > SNAPSHOT_BYTES:
            raise DependencyUnavailable('retained package index snapshot exceeds its byte budget')
        return self.store.put_bytes(raw, 'package-index-snapshot', Visibility.AUTHORING)


def validate_snapshot(store, ref, profile, policy, items: tuple[CatalogWheel, ...]):
    """Validate selected registry pins using retained bytes only; never write."""
    ref = _authoring_reference(ref, 'package-index-snapshot')
    raw = _bytes(store, ref, SNAPSHOT_BYTES)
    value = _json(raw)
    if (not isinstance(value, dict) or set(value) != {'version', 'source', 'indexes'}
            or value['version'] != _SNAPSHOT_VERSION or value['source'] != INDEX
            or not isinstance(value['indexes'], dict) or canonical_json(value) != raw):
        raise PolicyRejected('invalid retained package index snapshot provenance')
    _target(profile)
    options = {}
    for name, index in value['indexes'].items():
        _name(name)
        _, indexed = _load_index(store, index, name, profile, policy.max_staging_bytes)
        options[name] = {option.pin.filename: option for option in indexed}
    seen = set()
    for item in items:
        try:
            item = (CatalogWheel.model_validate(item) if isinstance(item, CatalogWheel)
                    else CatalogWheel.model_validate_json(canonical_json(item)))
        except (TypeError, ValueError, RecursionError) as exc:
            raise PolicyRejected('invalid retained selected wheel') from exc
        name = _name(item.pin.name)
        if name in seen:
            raise PolicyRejected('duplicate retained selected distribution')
        seen.add(name)
        option = options.get(name, {}).get(item.pin.filename)
        if option is None or option.pin != item.pin:
            raise PolicyRejected('selected wheel lacks matching trusted index provenance')
        _reference(item.artifact, 'dependency-wheel')
        data = _bytes(store, item.artifact, policy.max_staging_bytes)
        if (hashlib.sha256(data).hexdigest() != item.pin.sha256
                or option.size is not None and len(data) != option.size):
            raise PolicyRejected('selected wheel differs from retained trusted index')
