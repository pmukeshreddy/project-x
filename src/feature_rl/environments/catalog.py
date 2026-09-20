"""Preparation-only acquisition of reusable, immutable offline wheel catalogs.

The controller downloads inert wheels from PyPI's trusted index/CDN; it never
imports them or invokes package hooks. Candidate resolution has no path here.
"""
from collections import deque
import hashlib
import json
from pathlib import Path
import re
import time
from types import SimpleNamespace
from urllib.parse import unquote, urlsplit

from packaging.specifiers import SpecifierSet
from packaging.utils import canonicalize_name, parse_wheel_filename
from packaging.version import Version

from feature_rl.artifacts import canonical_json
from feature_rl.contracts import AllowedChanges, ArtifactRef, Visibility
from feature_rl.intake.sources import BoundedHttpFetcher, SourceFetchError
from .candidates import (CatalogWheel, _bytes, _inspect_wheel, _load_catalog,
    _reference, _safe_requirement, _solve, _tags, _target, publish_catalog,
    _CATALOG_WHEELS, _CATALOG_PAYLOAD_BYTES, _CATALOG_EXPANDED_BYTES)
from .inference import infer_repository
from .models import DependencyUnavailable, PolicyRejected, SourceRejected
from .profiles import WheelPin

INDEX = 'https://pypi.org/simple/'
VERSIONS_PER_REQUIREMENT = 3
INDEX_BYTES = 16 * 1024 * 1024
MAX_PROJECTS = 256
MAX_REQUESTS = 4096
ACQUISITION_BYTES = 2 * 1024 * 1024


def _index_files(raw, name, profile, max_wheel_bytes):
    """Use index hashes and target tags, never the controller's Python/platform."""
    value = json.loads(raw)
    if (not isinstance(value, dict) or canonicalize_name(value.get('name', '')) != name
            or not str(value.get('meta', {}).get('api-version', '')).startswith('1.')
            or not isinstance(value.get('files'), list) or len(value['files']) > 100000):
        raise PolicyRejected('invalid trusted package index snapshot')
    ranks = {tag: rank for rank, tag in reversed(tuple(enumerate(profile.compatible_tags)))}
    files = {}; identities = {}
    for item in value['files']:
        filename = item.get('filename', '')
        if not isinstance(filename, str) or not filename.endswith('.whl') or item.get('yanked', False):
            continue
        size = item.get('size')
        if size is not None:
            if type(size) is not int or size < 0:
                raise PolicyRejected('invalid indexed wheel size')
            if size > max_wheel_bytes:
                continue
        _tags('-'.join(filename[:-4].split('-')[-3:]))
        package, version, build, tags = parse_wheel_filename(filename)
        compatible = {str(tag) for tag in tags}.intersection(ranks)
        if package != name:
            raise PolicyRejected('index wheel distribution name mismatch')
        if not compatible or not SpecifierSet(item.get('requires-python') or '').contains(
                profile.interpreter_version, prereleases=True):
            continue
        digest = item.get('hashes', {}).get('sha256', '')
        if not isinstance(digest, str) or not re.fullmatch(r'[0-9a-f]{64}', digest):
            raise PolicyRejected('trusted index wheel lacks an exact SHA256')
        url = item.get('url', '')
        parsed = urlsplit(url)
        if (parsed.scheme != 'https' or parsed.netloc != 'files.pythonhosted.org'
                or not parsed.path.startswith('/packages/') or parsed.query or parsed.fragment
                or unquote(parsed.path.rsplit('/', 1)[-1]) != filename):
            raise PolicyRejected('wheel download is outside the trusted PyPI artifact origin')
        pin = WheelPin(name=name, version=str(version), filename=filename, sha256=digest)
        if filename in identities and identities[filename] != pin:
            raise PolicyRejected('trusted package index has conflicting wheel hashes')
        identities[filename] = pin
        files[filename] = (pin, url, version, build, min(ranks[tag] for tag in compatible))
    ordered = sorted(files.values(), key=lambda item: (item[0].filename, item[0].sha256))
    ordered.sort(key=lambda item: item[3], reverse=True)
    ordered.sort(key=lambda item: item[4])
    ordered.sort(key=lambda item: item[2], reverse=True)
    return ordered


def prepare_catalog(runtime, source, pins, *, dependency_sources=()):
    """Acquire/cache declaration-backed choices, then freeze this task's snapshot.

    Shared heads may grow only during preparation. Immutable preparation locks,
    index snapshots and wheel hashes keep existing tasks independent of that head.
    Additional historical declarations seed supply, never candidate acceptance.
    """
    store, state, policy = runtime.store, runtime.engine.state, runtime.policy
    if len(dependency_sources) > 32:
        raise PolicyRejected('catalog dependency-source count bound exceeded')
    profile = policy.profile
    environment = _target(profile)
    metadata = [infer_repository(source)]; skipped_sources = []
    for tree in dependency_sources:
        try:
            inferred = infer_repository(tree)
            for field in ('build_requirements', 'requirements', 'constraints'):
                for value in inferred[field]:
                    _safe_requirement(value)
            metadata.append(inferred)
        except (PolicyRejected, SourceRejected):
            # Historical metadata is a supply hint, not a condition that an
            # independent candidate must reproduce or make executable.
            skipped_sources.append({'source': tree.tree_sha256, 'reason': 'historical declarations could not be inferred safely'})
    implementation = hashlib.sha256(Path(__file__).read_bytes() +
        Path(__file__).with_name('candidates.py').read_bytes()).hexdigest()
    inputs = {'implementation': implementation, 'policy': policy.model_dump(mode='json'),
              'declarations': [{field: item[field] for field in
                  ('build_requirements', 'requirements', 'constraints', 'dependency_hashes')}
                  for item in metadata],
              'baseline': [pin.model_dump(mode='json') for pin in pins],
              'dependency_sources': [tree.tree_sha256 for tree in dependency_sources],
              'extra_catalog': runtime.dependency_catalog.model_dump(mode='json')
                  if runtime.dependency_catalog is not None else None}
    key = hashlib.sha256(canonical_json(inputs)).hexdigest()
    lock_name = 'prepared-catalog-' + key + '.json'
    # Sharing depends on worker compatibility and admission bounds, not repository.
    target = {'python': profile.interpreter_version, 'tags': profile.compatible_tags,
              'policy': policy.model_dump(mode='json', exclude={'profile', 'image'})}
    pool_name = 'shared-catalog-' + hashlib.sha256(canonical_json(target)).hexdigest() + '.json'
    deadline = time.monotonic() + runtime.engine.image_seconds

    with state.lock():
        runtime.engine._require_clean_owned_state()
        try:
            locked = state.read(lock_name)
        except FileNotFoundError:
            locked = None
        if locked is not None:
            if locked.get('inputs') != inputs:
                raise PolicyRejected('prepared dependency catalog inputs changed')
            ref = ArtifactRef.model_validate_json(canonical_json(locked['catalog']))
            _load_catalog(store, ref, policy)
            evidence = ArtifactRef.model_validate_json(canonical_json(locked['evidence']))
            if (evidence.kind != 'dependency-catalog-preparation' or evidence.visibility != Visibility.AUTHORING
                    or evidence.encoding != 'bytes'):
                raise PolicyRejected('invalid catalog preparation evidence reference')
            retained = json.loads(_bytes(store, evidence, ACQUISITION_BYTES))
            if retained.get('identity') != key or retained.get('catalog') != ref.model_dump(mode='json'):
                raise PolicyRejected('catalog preparation evidence binding changed')
            return ref, evidence

        records = {}; prior = {}; total = expanded = 0
        unavailable = list(skipped_sources); unusable = set()
        omitted = attempts = 0

        def note_unavailable(value):
            nonlocal omitted
            if len(unavailable) < 256:
                unavailable.append(value)
            else:
                omitted += 1

        def retain(wheel):
            nonlocal total, expanded
            previous = records.get(wheel.item.pin.filename)
            if previous is not None:
                if previous.item != wheel.item:
                    raise PolicyRejected('cached package filename changed its pinned artifact')
                return previous
            total += wheel.size; expanded += wheel.expanded_size
            if (len(records) >= _CATALOG_WHEELS or total > _CATALOG_PAYLOAD_BYTES
                    or expanded > _CATALOG_EXPANDED_BYTES):
                raise PolicyRejected('shared dependency catalog exceeds its fixed bounds')
            records[wheel.item.pin.filename] = wheel
            return wheel

        try:
            head = state.read(pool_name)
            shared = ArtifactRef.model_validate_json(canonical_json(head['catalog']))
            prior_evidence = ArtifactRef.model_validate_json(canonical_json(head['evidence']))
        except FileNotFoundError:
            shared = None
            prior_evidence = None
        if shared is not None:
            prior = {wheel.item.pin.filename: wheel for wheel in _load_catalog(store, shared, policy)[1]}
            if (prior_evidence.kind != 'dependency-catalog-preparation'
                    or prior_evidence.visibility != Visibility.AUTHORING or prior_evidence.encoding != 'bytes'
                    or json.loads(_bytes(store, prior_evidence, ACQUISITION_BYTES)).get('catalog')
                        != shared.model_dump(mode='json')):
                raise PolicyRejected('shared catalog acquisition evidence binding changed')
        if runtime.dependency_catalog is not None:
            for wheel in _load_catalog(store, runtime.dependency_catalog, policy)[1]:
                retain(wheel)
        specs = {canonicalize_name(pin.name): pin for pin in profile.dependencies}
        for pin in pins:
            retain(_inspect_wheel(store, CatalogWheel(pin=specs[canonicalize_name(pin.name)],
                                                      artifact=pin.artifact), policy))

        indexes = {}; refreshed = set(); index_refs = set(); failed_indexes = set()

        def fetch(url, cap, accept):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise PolicyRejected('dependency catalog preparation deadline exceeded')
            return BoundedHttpFetcher(max_bytes=cap, timeout_seconds=min(30.0, remaining)).fetch(
                url, edit_history='not_applicable', accept=accept).body

        def index(name, *, refresh=False):
            if name in failed_indexes:
                return ()
            if not refresh and name in indexes:
                return indexes[name]
            if name not in indexes and len(indexes) + len(failed_indexes) >= MAX_PROJECTS:
                raise PolicyRejected('dependency catalog package-name bound exceeded')
            index_name = 'package-index-' + hashlib.sha256(name.encode()).hexdigest() + '.json'
            try:
                ref = ArtifactRef.model_validate_json(canonical_json(state.read(index_name))) if not refresh else None
            except FileNotFoundError:
                ref = None
            if ref is None:
                try:
                    raw = fetch(INDEX + name + '/', INDEX_BYTES, 'application/vnd.pypi.simple.v1+json')
                except SourceFetchError as exc:
                    note_unavailable({'package': name, 'reason': str(exc)[:1024]})
                    failed_indexes.add(name)
                    return ()
                options = _index_files(raw, name, profile, policy.max_staging_bytes)
                ref = store.put_bytes(raw, 'package-index', Visibility.AUTHORING)
                state.write(index_name, ref.model_dump(mode='json'))
                refreshed.add(name)
            else:
                if ref.kind != 'package-index' or ref.visibility != Visibility.AUTHORING or ref.encoding != 'bytes':
                    raise PolicyRejected('invalid cached package index reference')
                options = _index_files(_bytes(store, ref, INDEX_BYTES), name, profile, policy.max_staging_bytes)
            index_refs.add(ref)
            indexes[name] = options
            return options

        def acquire(option):
            nonlocal attempts
            pin, url, *_ = option
            if pin.filename in unusable:
                return None
            if pin.filename in records:
                wheel = records[pin.filename]
                if wheel.item.pin != pin:
                    raise PolicyRejected('package index disagrees with an immutable cached wheel')
                return wheel
            if pin.filename in prior:
                wheel = prior[pin.filename]
                if wheel.item.pin != pin:
                    raise PolicyRejected('package index disagrees with an immutable shared wheel')
                return retain(wheel)
            cache_name = 'package-wheel-' + pin.sha256 + '.json'
            try:
                item = CatalogWheel.model_validate_json(canonical_json(state.read(cache_name)))
            except FileNotFoundError:
                attempts += 1
                if attempts > _CATALOG_WHEELS:
                    raise PolicyRejected('dependency catalog wheel acquisition attempt bound exceeded')
                try:
                    data = fetch(url, policy.max_staging_bytes, 'application/octet-stream')
                except SourceFetchError as exc:
                    note_unavailable({'filename': pin.filename, 'sha256': pin.sha256, 'reason': str(exc)[:1024]})
                    unusable.add(pin.filename)
                    return None
                if hashlib.sha256(data).hexdigest() != pin.sha256:
                    raise PolicyRejected('downloaded wheel differs from trusted index SHA256')
                item = CatalogWheel(pin=pin, artifact=store.put_bytes(data, 'dependency-wheel', Visibility.AUTHORING))
            else:
                # Cache entries may come from a repository with larger limits.
                # Verify their immutable bytes before applying this task's caps.
                _reference(item.artifact, 'dependency-wheel')
                data = _bytes(store, item.artifact, 64 * 1024 * 1024)
                if hashlib.sha256(data).hexdigest() != pin.sha256:
                    raise PolicyRejected('cached wheel differs from trusted index SHA256')
            if item.pin != pin:
                raise PolicyRejected('shared wheel cache pin changed')
            try:
                wheel = _inspect_wheel(store, item, policy)
            except PolicyRejected as exc:
                note_unavailable({'filename': pin.filename, 'sha256': pin.sha256, 'reason': str(exc)[:1024]})
                unusable.add(pin.filename)
                return None
            wheel = retain(wheel)
            state.write(cache_name, item.model_dump(mode='json'))
            return wheel

        pending = deque(); requested = set(); broadened = set(); contexts = {}

        def enqueue(requirement, context, extra=''):
            requirement = _safe_requirement(str(requirement))
            if requirement.marker and not requirement.marker.evaluate({**environment, 'extra': extra}):
                return
            signature = (str(requirement), context)
            if signature in requested:
                return
            if len(requested) >= MAX_REQUESTS:
                raise PolicyRejected('dependency catalog requirement bound exceeded')
            requested.add(signature); pending.append((requirement, context))

        empty_context = hashlib.sha256(canonical_json([{}, {}])).hexdigest()
        contexts[empty_context] = ({}, {})
        for item in metadata:
            constraints = {}
            for value in item['constraints']:
                requirement = _safe_requirement(value)
                if requirement.marker is None or requirement.marker.evaluate(environment):
                    constraints.setdefault(canonicalize_name(requirement.name), []).append(str(requirement))
            context = hashlib.sha256(canonical_json([constraints, item['dependency_hashes']])).hexdigest()
            contexts[context] = (constraints, item['dependency_hashes'])
            for value in (*item['build_requirements'], *item['requirements']):
                enqueue(value, context)
        for pin in profile.dependencies:
            enqueue(pin.name + '==' + pin.version, empty_context)

        while pending:
            if time.monotonic() >= deadline:
                raise PolicyRejected('dependency catalog preparation deadline exceeded')
            requirement, context = pending.popleft()
            name = canonicalize_name(requirement.name)
            if name == canonicalize_name(profile.project_name):
                note_unavailable({'package': name, 'reason': 'target project is not a catalog dependency'})
                continue
            constraints, hashes = contexts[context]
            limits = [requirement, *(_safe_requirement(value) for value in constraints.get(name, ()))]
            allowed_hashes = hashes.get(name)
            extras = tuple(sorted(canonicalize_name(extra) for extra in requirement.extras))

            def usable(wheel):
                if not set(extras) <= wheel.extras:
                    return False
                try:
                    for dependency in wheel.requirements:
                        _safe_requirement(str(dependency))
                except SourceRejected:
                    return False
                return True

            def choices(options):
                explicit_pre = any(value.specifier.prereleases for value in limits)
                for option in options:
                    pin, _, version, *_ = option
                    if (version.is_prerelease and not explicit_pre
                            or allowed_hashes and pin.sha256 not in allowed_hashes
                            or not all(value.specifier.contains(version, prereleases=True) for value in limits)):
                        continue
                    yield option

            options = tuple(choices(index(name)))
            if not options and name not in refreshed:
                options = tuple(choices(index(name, refresh=True)))
            # Existing pinned bytes remain usable even when an index removes or
            # yanks that historical wheel; the candidate still uses its hash.
            matching = [wheel for wheel in tuple(records.values()) if wheel.name == name
                and wheel.tags.intersection(profile.compatible_tags)
                and wheel.requires_python.contains(profile.interpreter_version, prereleases=True)
                and (not allowed_hashes or wheel.item.pin.sha256 in allowed_hashes)
                and all(value.specifier.contains(wheel.version, prereleases=True) for value in limits)]
            selected_versions = set()
            for option in options:
                if option[2] in selected_versions:
                    continue
                wheel = acquire(option)
                if wheel is None or not usable(wheel):
                    continue
                selected_versions.add(wheel.version)
                if wheel not in matching:
                    matching.append(wheel)
                if len(selected_versions) == VERSIONS_PER_REQUIREMENT:
                    break
            # Reuse retained bytes even if the index is temporarily unavailable
            # or has since removed a version. Select only a bounded set here.
            cached = sorted(prior.values(), key=lambda wheel: (wheel.version, wheel.item.pin.filename), reverse=True)
            for wheel in cached:
                if (wheel.name == name and len(selected_versions) < VERSIONS_PER_REQUIREMENT
                        and wheel.version not in selected_versions
                        and wheel.tags.intersection(profile.compatible_tags)
                        and wheel.requires_python.contains(profile.interpreter_version, prereleases=True)
                        and (not allowed_hashes or wheel.item.pin.sha256 in allowed_hashes)
                        and all(value.specifier.contains(wheel.version, prereleases=True) for value in limits)
                        and usable(wheel)):
                    matching.append(retain(wheel)); selected_versions.add(wheel.version)
            for wheel in matching:
                if not usable(wheel):
                    continue
                for extra in ('', *sorted(requirement.extras)):
                    for dependency in wheel.requirements:
                        enqueue(dependency, context, extra)
            # Widen every encountered package independently of historical pins;
            # prior repositories' supplies remain available in the shared cache.
            if (name, extras) not in broadened:
                broadened.add((name, extras))
                enqueue(name + ('[' + ','.join(extras) + ']' if extras else ''), empty_context)

        rules = AllowedChanges(source_roots=profile.source_roots, forbidden_paths=(),
            dependencies='pinned_allowlist', dependency_artifacts=(), additional_artifact_types=())
        # Historical declarations only seed available supply. Candidate closure
        # and behavior, not the historical dependency choice, determine grading.
        _solve(tuple(records.values()), metadata[0], profile, rules,
               SimpleNamespace(dependencies=pins), environment, policy)
        # A task snapshot is bounded; old immutable wheel/index cache entries are
        # retained even when every prior wheel cannot fit in the next snapshot.
        for wheel in sorted(prior.values(), key=lambda value: (value.name, value.version, value.item.pin.filename)):
            if (wheel.item.pin.filename not in records and len(records) < _CATALOG_WHEELS
                    and total + wheel.size <= _CATALOG_PAYLOAD_BYTES
                    and expanded + wheel.expanded_size <= _CATALOG_EXPANDED_BYTES):
                retain(wheel)
        baseline_versions = {(canonicalize_name(pin.name), Version(pin.version)) for pin in pins}
        broader = False
        for wheel in records.values():
            if (wheel.name, wheel.version) in baseline_versions or wheel.name == canonicalize_name(profile.project_name):
                continue
            if time.monotonic() >= deadline:
                raise PolicyRejected('dependency catalog preparation deadline exceeded')
            alternative = {'build_requirements': [wheel.name + '==' + str(wheel.version)],
                           'requirements': [], 'constraints': [], 'dependency_hashes': {}}
            try:
                closure = _solve(tuple(records.values()), alternative, profile, rules,
                                 SimpleNamespace(dependencies=pins), environment, policy)
            except DependencyUnavailable:
                continue
            if all(item.name != canonicalize_name(profile.project_name) for item in closure):
                broader = True
                break
        if not broader:
            raise DependencyUnavailable('preparation could not freeze any safe compatible package/version '
                                        'choice beyond the baseline; no baseline-only catalog was published')
        for wheel in (*prior.values(), *records.values()):
            state.write('package-wheel-' + wheel.item.pin.sha256 + '.json', wheel.item.model_dump(mode='json'))
        ref = publish_catalog(store, policy, tuple(wheel.item for wheel in records.values()))
        evidence_value = {'identity': key, 'catalog': ref.model_dump(mode='json'),
            'previous': prior_evidence.model_dump(mode='json') if prior_evidence is not None else None,
            'extra_catalog': inputs['extra_catalog'],
            'unavailable': unavailable, 'unavailable_details_omitted': omitted,
            'indexes': [item.model_dump(mode='json') for item in sorted(index_refs, key=lambda value: value.sha256)]}
        if len(canonical_json(evidence_value)) > ACQUISITION_BYTES:
            raise PolicyRejected('dependency catalog acquisition evidence byte bound exceeded')
        evidence = runtime.publish(evidence_value,
            'dependency-catalog-preparation', Visibility.AUTHORING)
        state.write(pool_name, {'catalog': ref.model_dump(mode='json'), 'evidence': evidence.model_dump(mode='json')})
        state.write(lock_name, {'inputs': inputs, 'catalog': ref.model_dump(mode='json'),
                               'evidence': evidence.model_dump(mode='json')})
        return ref, evidence
