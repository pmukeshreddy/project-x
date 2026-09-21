"""Inert candidate declarations, dependency solving and frozen-input validation.

The controller supplies a lazy trusted registry provider during resolution.
Archived resolutions use only exact retained artifacts. Submitted source and
wheel code are never executed here.
"""
from dataclasses import dataclass
from email.parser import BytesParser
import hashlib
import io
import math
from pathlib import Path
import re
import stat
import tomllib
from typing import Annotated, Literal
import zipfile
import zlib

from packaging import __version__ as packaging_version
from packaging.requirements import InvalidRequirement, Requirement
from packaging.specifiers import SpecifierSet
from packaging.tags import parse_tag
from packaging.utils import canonicalize_name, canonicalize_version, parse_wheel_filename
from packaging.version import InvalidVersion, Version
from pydantic import Field, ValidationError

from feature_rl.artifacts import ArtifactError, canonical_json
from feature_rl.contracts import AllowedChanges, ArtifactRef, DependencyPin, StrictModel, Visibility
from .archive import SourceArchive, safe_path
from .inference import infer_repository
from .models import DependencyUnavailable, PolicyRejected, SourceRejected
from .profiles import RuntimeProfile, WheelPin, metadata_digest


_SOLVER_VERSION = 'candidate-wheel-solver-v3'
_METADATA_BYTES = 1024 * 1024
_MARKER_KEYS = frozenset({
    'implementation_name', 'implementation_version', 'os_name', 'platform_machine',
    'platform_python_implementation', 'platform_release', 'platform_system',
    'platform_version', 'python_full_version', 'python_version', 'sys_platform',
})
_DIGEST = Annotated[str, Field(pattern=r'^[0-9a-f]{64}$')]


class CatalogWheel(StrictModel):
    pin: WheelPin
    artifact: ArtifactRef


class DependencyResolution(StrictModel):
    """Reusable controller result bound to an immutable package-index snapshot."""
    version: Literal['dependency-resolution-v1'] = 'dependency-resolution-v1'
    request_identity: _DIGEST
    index_snapshot: ArtifactRef
    identity: _DIGEST
    wheels: tuple[CatalogWheel, ...]
    failure: Literal['no-compatible-closure'] | None = None


class CandidateResolution(StrictModel):
    version: Literal['candidate-dependencies-v1'] = 'candidate-dependencies-v1'
    recipe: ArtifactRef
    policy: ArtifactRef
    resolution: ArtifactRef
    identity: _DIGEST
    manifest_hashes: dict[str, str]
    metadata_sha256: _DIGEST
    profile: RuntimeProfile
    dependencies: Annotated[tuple[DependencyPin, ...], Field(min_length=1)]


@dataclass(frozen=True)
class _Wheel:
    item: CatalogWheel
    name: str
    version: Version
    build: tuple
    tags: frozenset[str]
    requires_python: SpecifierSet
    requirements: tuple[Requirement, ...]
    extras: frozenset[str]
    size: int
    expanded_size: int


def _bytes(store, ref, cap):
    try:
        return store.get_bytes(ref, max_envelope_bytes=4 * ((cap + 2) // 3) + 4096,
                               max_payload_bytes=cap)
    except (ArtifactError, ValueError, OSError) as exc:
        raise PolicyRejected('unavailable or corrupt frozen dependency input') from exc


def _reference(ref, kind):
    if (not isinstance(ref, ArtifactRef) or ref.kind != kind or ref.encoding != 'bytes'
            or ref.visibility not in {Visibility.AUTHORING, Visibility.PUBLIC}):
        raise PolicyRejected(kind + ' requires authoring/public immutable bytes')


def _tags(value):
    fields = value.split('-')
    if (len(value) > 255 or len(fields) != 3
            or any(not re.fullmatch(r'[A-Za-z0-9_]+(?:\.[A-Za-z0-9_]+)*', field) for field in fields)
            or math.prod(len(field.split('.')) for field in fields) > 1024):
        raise PolicyRejected('invalid or oversized dependency wheel tags')
    return frozenset(str(tag) for tag in parse_tag(value))


def _inspect_wheel(store, item, policy):
    """Validate the complete ZIP without extracting or importing its contents."""
    _reference(item.artifact, 'dependency-wheel')
    pin = item.pin
    data = _bytes(store, item.artifact, policy.max_staging_bytes)
    if hashlib.sha256(data).hexdigest() != pin.sha256:
        raise PolicyRejected('dependency wheel payload hash mismatch')
    try:
        if safe_path(pin.filename) != pin.filename or '/' in pin.filename:
            raise PolicyRejected('noncanonical dependency wheel filename')
        _tags('-'.join(pin.filename[:-4].split('-')[-3:]))
        name, version, build, tags = parse_wheel_filename(pin.filename)
        if name != canonicalize_name(pin.name) or version != Version(pin.version):
            raise PolicyRejected('dependency wheel filename/pin identity mismatch')
        metadata_root = '-'.join(pin.filename.split('-')[:2]) + '.dist-info'
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            infos = archive.infolist()
            expanded = sum(info.file_size for info in infos)
            if len(infos) > policy.max_files or expanded > policy.disk_bytes:
                raise PolicyRejected('dependency wheel expanded byte/member cap')
            seen, files, metadata = set(), set(), {}
            for info in infos:
                path = safe_path(info.filename)
                if info.filename != path + ('/' if info.is_dir() else '') or path in seen:
                    raise PolicyRejected('duplicate or noncanonical dependency wheel member')
                seen.add(path)
                mode = stat.S_IFMT(info.external_attr >> 16)
                if (mode not in (0, stat.S_IFDIR if info.is_dir() else stat.S_IFREG)
                        or info.flag_bits & 1 or info.file_size < 0 or info.compress_size < 0):
                    raise PolicyRejected('linked, special, encrypted or invalid dependency wheel member')
                root = path.split('/', 1)[0]
                if root.endswith('.dist-info') and root != metadata_root:
                    raise PolicyRejected('dependency wheel contains foreign distribution metadata')
                if info.is_dir():
                    continue
                files.add(path)
                capture = path in {metadata_root + '/METADATA', metadata_root + '/WHEEL'}
                if capture and info.file_size > _METADATA_BYTES:
                    raise PolicyRejected('dependency wheel metadata byte cap')
                chunks, count = [], 0
                with archive.open(info) as member:
                    while True:
                        chunk = member.read(min(65536, info.file_size + 1 - count))
                        if not chunk:
                            break
                        count += len(chunk)
                        if count > info.file_size:
                            raise PolicyRejected('dependency wheel member size mismatch')
                        if capture:
                            chunks.append(chunk)
                if count != info.file_size:
                    raise PolicyRejected('truncated dependency wheel member')
                if capture:
                    metadata[path] = b''.join(chunks)
            if any('/'.join(path.split('/')[:n]) in files
                   for path in seen for n in range(1, len(path.split('/')))):
                raise PolicyRejected('dependency wheel file/directory collision')
            if not all(metadata_root + '/' + value in files for value in ('METADATA', 'WHEEL', 'RECORD')):
                raise PolicyRejected('dependency wheel lacks required identity metadata')
            package = BytesParser().parsebytes(metadata[metadata_root + '/METADATA'])
            wheel = BytesParser().parsebytes(metadata[metadata_root + '/WHEEL'])
            if (package.defects or wheel.defects or len(package.get_all('Name', [])) != 1
                    or len(package.get_all('Version', [])) != 1
                    or canonicalize_name(package['Name']) != name or Version(package['Version']) != version
                    or wheel.get_all('Wheel-Version', []) != ['1.0']
                    or wheel.get_all('Root-Is-Purelib', []) not in (['true'], ['false'])):
                raise PolicyRejected('dependency wheel metadata/pin identity mismatch')
            declared_tags = set()
            tag_headers = wheel.get_all('Tag', [])
            if len(tag_headers) > 1024:
                raise PolicyRejected('dependency wheel tag count cap')
            for value in tag_headers:
                declared_tags.update(_tags(value))
            if declared_tags != {str(tag) for tag in tags}:
                raise PolicyRejected('dependency wheel metadata/filename tags mismatch')
            python_headers = package.get_all('Requires-Python', [])
            requires = package.get_all('Requires-Dist', [])
            extras = package.get_all('Provides-Extra', [])
            if len(python_headers) > 1:
                raise PolicyRejected('ambiguous dependency wheel Requires-Python metadata')
            if any(not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]*', extra) for extra in extras):
                raise PolicyRejected('invalid dependency wheel extra')
            return _Wheel(item, name, version, build, frozenset(declared_tags),
                          SpecifierSet(python_headers[0] if python_headers else ''),
                          tuple(Requirement(value) for value in requires),
                          frozenset(canonicalize_name(extra) for extra in extras), len(data), expanded)
    except (SourceRejected, zipfile.BadZipFile, ValueError, OSError, RuntimeError,
            NotImplementedError, EOFError, zlib.error) as exc:
        raise PolicyRejected('invalid frozen dependency wheel: ' + str(exc)) from exc


def _safe_requirement(value):
    try:
        if (not isinstance(value, str) or len(value) > 16384 or '\x00' in value
                or '\n' in value or '\r' in value or any(token in value for token in ('`', '$', '--'))):
            raise SourceRejected('unsafe candidate dependency declaration')
        requirement = Requirement(value)
    except InvalidRequirement as exc:
        raise SourceRejected('invalid candidate dependency declaration') from exc
    if requirement.url:
        raise SourceRejected('URL and local dependency requirements are unsupported')
    if requirement.marker:
        # Quoted literals are not environment variable references.
        expression = re.sub(r'''"[^"]*"|'[^']*' ''', '', str(requirement.marker), flags=re.X)
        variables = set(re.findall(r'\b[a-z_]+\b', expression)) - {'and', 'or', 'in', 'not'}
        if variables - (_MARKER_KEYS | {'extra'}) or variables & {'platform_release', 'platform_version'}:
            raise SourceRejected('unsupported or host-kernel-dependent requirement marker')
    return requirement


def _repository_policy(source):
    """Reject explicit transport overrides that inert inference need not use."""
    entry = source.files.get('pyproject.toml')
    if entry is None:
        return
    if len(entry.data) > _METADATA_BYTES:
        raise DependencyUnavailable('candidate manifest exceeds the static parsing byte bound')
    try:
        manifest = tomllib.loads(entry.data.decode('utf-8-sig'))
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise SourceRejected('invalid candidate pyproject.toml') from exc

    def table(value):
        if not isinstance(value, dict):
            raise SourceRejected('candidate manifest requires a static metadata table')
        return value

    def requirements(values):
        if not isinstance(values, list):
            raise SourceRejected('candidate manifest requires a static dependency list')
        for value in values:
            _safe_requirement(value)

    project = table(manifest.get('project', {}))
    requirements(project.get('dependencies', []))
    requirements(table(manifest.get('build-system', {})).get('requires', []))
    for values in table(project.get('optional-dependencies', {})).values():
        requirements(values)
    tools = table(manifest.get('tool', {}))
    default_indexes = {'https://pypi.org/simple', 'https://pypi.python.org/simple'}

    def index(url):
        if not isinstance(url, str) or url.rstrip('/') not in default_indexes:
            raise SourceRejected('candidate declares an alternate dependency index')

    for name, index_key in (('uv', 'index'), ('poetry', 'source'), ('pdm', 'source')):
        settings = table(tools.get(name, {}))
        declarations = settings.get(index_key, [])
        if not isinstance(declarations, list):
            raise SourceRejected('candidate index declarations must be a static list')
        for declaration in declarations:
            declaration = table(declaration)
            if 'url' in declaration:
                index(declaration['url'])
            elif str(declaration.get('name', '')).lower() != 'pypi':
                raise SourceRejected('candidate index declaration has no default registry')
        for key in ('index-url', 'extra-index-url'):
            values = settings.get(key, [])
            values = [values] if isinstance(values, str) else values
            if not isinstance(values, list):
                raise SourceRejected('candidate index URLs must be static strings')
            for value in values:
                index(value)
        if settings.get('find-links') or name == 'uv' and settings.get('sources'):
            raise SourceRejected('candidate declares local/direct dependency source overrides')
        if name == 'poetry':
            for value in table(settings.get('dependencies', {})).values():
                if isinstance(value, dict) and {'git', 'path', 'url', 'file', 'source', 'directory'} & value.keys():
                    raise SourceRejected('candidate declares a direct or alternate-index Poetry dependency')


def _requirement_key(requirement):
    specifiers = []
    for specifier in requirement.specifier:
        version = specifier.version
        if specifier.operator != '===' and '*' not in version:
            try:
                version = canonicalize_version(version, strip_trailing_zero=specifier.operator != '~=')
            except InvalidVersion:
                pass
        specifiers.append((specifier.operator, version))
    return (canonicalize_name(requirement.name), tuple(sorted(canonicalize_name(extra) for extra in requirement.extras)),
            tuple(sorted(specifiers)), str(requirement.marker) if requirement.marker else '')


def _semantic_dependencies(metadata):
    result = {field: sorted(set(_requirement_key(_safe_requirement(value)) for value in metadata[field]))
              for field in ('build_requirements', 'requirements', 'constraints')}
    result.update(build_backend=metadata['build_backend'],
                  dependency_hashes={canonicalize_name(name): sorted(set(values))
                                     for name, values in metadata['dependency_hashes'].items()},
                  system_requirements=sorted(set(metadata['system_requirements'])))
    return result


def _target(profile):
    environment = dict(profile.marker_environment)
    if (not _MARKER_KEYS <= environment.keys() or any(not isinstance(value, str) for value in environment.values())
            or environment.get('python_full_version') != profile.interpreter_version
            or environment.get('python_version') != '.'.join(profile.interpreter_version.split('.')[:2])
            or not profile.compatible_tags):
        raise DependencyUnavailable('pinned worker marker environment or compatibility tags are unavailable')
    if len(profile.compatible_tags) > 32768:
        raise PolicyRejected('pinned worker compatibility tag count cap')
    for tag in profile.compatible_tags:
        if _tags(tag) != frozenset({tag}):
            raise PolicyRejected('pinned worker tags must be individual canonical tags')
    return {**environment, 'extra': ''}


def _candidate_inputs(store, prepared, recipe, policy, source, rules):
    profile = policy.profile
    if profile is None:
        raise DependencyUnavailable('candidate has no fixed repository runtime')
    if prepared.policy not in recipe.provenance.inputs:
        raise PolicyRejected('candidate recipe/policy provenance mismatch')
    try:
        rules = AllowedChanges.model_validate(rules)
    except ValidationError as exc:
        raise SourceRejected('invalid candidate change rules') from exc
    profile.validate_allowed_changes(rules)
    _repository_policy(source)
    try:
        metadata = infer_repository(source)
    except PolicyRejected as exc:
        # Inference uses one error type for malformed text and unsupported
        # dynamic declarations. A missing reproducible interpretation is not
        # evidence of an incorrect candidate; explicit unsafe forms are checked
        # separately above and below.
        raise DependencyUnavailable('candidate metadata cannot be resolved safely: ' + str(exc)) from exc
    if canonicalize_name(metadata['project_name']) != canonicalize_name(profile.project_name):
        raise SourceRejected('candidate changes the project distribution name')
    for field in ('build_requirements', 'requirements', 'constraints'):
        for value in metadata[field]:
            _safe_requirement(value)
    if rules.dependencies == 'forbidden':
        _reference(recipe.baseline, 'source-archive')
        try:
            baseline = SourceArchive.read(_bytes(store, recipe.baseline, policy.max_archive_bytes), policy)
            baseline_metadata = infer_repository(baseline)
            baseline_semantics = _semantic_dependencies(baseline_metadata)
        except (SourceRejected, PolicyRejected) as exc:
            raise PolicyRejected('invalid baseline dependency declarations') from exc
        if _semantic_dependencies(metadata) != baseline_semantics:
            raise SourceRejected('candidate changes forbidden dependency or build requirements')
    environment = _target(profile)
    if (not SpecifierSet(metadata['requires_python']).contains(profile.interpreter_version, prereleases=True)
            or metadata['python_versions'] and not any(profile.interpreter_version == version
                or profile.interpreter_version.startswith(version + '.') for version in metadata['python_versions'])):
        raise DependencyUnavailable('candidate requires an interpreter outside the fixed task runtime')
    packages = {package.name: package.version for package in profile.system_packages}
    for requirement in metadata['system_requirements']:
        name, separator, version = requirement.partition('=')
        if name not in packages or separator and packages[name] != version:
            raise DependencyUnavailable('candidate system package is unavailable in the fixed task runtime: ' + requirement)
    manifests = dict(sorted(metadata['evidence'].items()))
    digest = metadata_digest(metadata)
    # Hash the inert solver/inference sources, not a repository revision supplied
    # by a candidate. No imported dependency or submitted source is executed.
    implementation = {}
    for filename in ('candidates.py', 'catalog.py', 'inference.py', 'profiles.py'):
        try:
            implementation[filename] = hashlib.sha256(Path(__file__).with_name(filename).read_bytes()).hexdigest()
        except OSError as exc:
            raise PolicyRejected('candidate resolver implementation identity is unavailable') from exc
    identity = hashlib.sha256(canonical_json({
        'solver': _SOLVER_VERSION, 'implementation': implementation, 'packaging': packaging_version,
        'recipe': prepared.recipe.model_dump(mode='json'), 'policy': prepared.policy.model_dump(mode='json'),
        'rules': rules.model_dump(mode='json'),
        'manifest_hashes': manifests, 'metadata_sha256': digest,
        'profile_inputs': {'manifest_path': metadata['manifest_path'], 'source_roots': metadata['source_roots']},
    })).hexdigest()
    # The canonical dependency declarations identify the manifest for package
    # selection. Unrelated source/build metadata edits reuse this lock; the exact
    # submitted manifest hashes still bind the candidate profile above. Task and
    # historical source identities never select packages. Restricted tasks retain
    # their explicit artifact policy.
    allowed = _allowed_artifacts(rules, recipe)
    dependency_identity = hashlib.sha256(canonical_json({
        'solver': _SOLVER_VERSION, 'implementation': implementation, 'packaging': packaging_version,
        'manifest_dependencies': _semantic_dependencies(metadata),
        'python': profile.interpreter_version, 'markers': environment, 'tags': profile.compatible_tags,
        'system_packages': [pin.model_dump(mode='json') for pin in profile.system_packages],
        'dependency_policy': rules.dependencies,
        'artifact_policy': {'max_staging_bytes': policy.max_staging_bytes,
                            'disk_bytes': policy.disk_bytes, 'max_files': policy.max_files},
        'allowed_artifacts': None if allowed is None else sorted(
            (ref.model_dump(mode='json') for ref in allowed), key=lambda item: item['sha256']),
    })).hexdigest()
    return metadata, rules, environment, manifests, digest, identity, dependency_identity


def _allowed_artifacts(rules, recipe):
    if rules.dependencies == 'forbidden' or rules.dependency_artifacts:
        return {pin.artifact for pin in recipe.dependencies} | set(rules.dependency_artifacts)
    return None


def _solve(options, metadata, profile, rules, recipe, environment, policy, *, check_deadline=lambda: None):
    """Iterative backtracking over lazy choices, without package/version count caps.

    ``options`` supplies every compatible version from one retained registry view,
    or just the archived wheels during offline verification. Byte/disk and
    controller deadline limits constrain resources, never the version roster.
    """
    allowed = _allowed_artifacts(rules, recipe)
    roots = tuple(_safe_requirement(value) for value in (*metadata['build_requirements'], *metadata['requirements']))
    constraints = {}

    def active(requirement, extra=''):
        check_deadline()
        try:
            return requirement.marker is None or requirement.marker.evaluate({**environment, 'extra': extra})
        except (ValueError, KeyError) as exc:
            raise SourceRejected('candidate dependency marker cannot be evaluated safely') from exc

    for value in metadata['constraints']:
        requirement = _safe_requirement(value)
        if active(requirement):
            constraints.setdefault(canonicalize_name(requirement.name), []).append(requirement)

    def accepts(wheel, requirements, extras):
        check_deadline()
        hashes = metadata['dependency_hashes'].get(wheel.name)
        return (extras <= wheel.extras and (not hashes or wheel.item.pin.sha256 in hashes)
                and (allowed is None or wheel.item.artifact in allowed)
                and wheel.tags.intersection(profile.compatible_tags)
                and wheel.requires_python.contains(profile.interpreter_version, prereleases=True)
                and all(requirement.specifier.contains(wheel.version, prereleases=True)
                        for requirement in (*requirements, *constraints.get(wheel.name, ()))))

    def closure(selected):
        needed, extras, expanded, known = {}, {}, set(), set()
        pending = [(requirement, '', False) for requirement in roots]
        while pending:
            check_deadline()
            requirement, extra, vendor = pending.pop()
            try:
                applicable = active(requirement, extra)
            except SourceRejected:
                if vendor:
                    return None
                raise
            if not applicable:
                continue
            name = canonicalize_name(requirement.name)
            key = (name, _requirement_key(requirement))
            if key not in known:
                known.add(key)
                needed.setdefault(name, []).append(requirement)
            requested = extras.setdefault(name, set())
            requested.update(canonicalize_name(value) for value in requirement.extras)
            wheel = selected.get(name)
            if wheel is None:
                continue
            if not accepts(wheel, needed[name], requested):
                return None
            for enabled in ('', *sorted(requested)):
                if (name, enabled) in expanded:
                    continue
                expanded.add((name, enabled))
                for dependency in wheel.requirements:
                    pending.append((dependency, enabled, True))
        return needed, extras

    def extend(selected, name, requirements, extras):
        requirements = (*requirements, *constraints.get(name, ()))
        hashes = metadata['dependency_hashes'].get(name)
        # The existing forbidden-dependency policy uses B's pins, never H's.
        if rules.dependencies == 'forbidden':
            baseline = next((pin for pin in recipe.dependencies if canonicalize_name(pin.name) == name), None)
            if baseline is None:
                return
            requirements = (*requirements, Requirement(name + '==' + baseline.version))
            hashes = {baseline.sha256} if not hashes else set(hashes) & {baseline.sha256}
            if not hashes:
                return
        prereleases = any(requirement.specifier.prereleases for requirement in requirements)
        for wheel in options(name, requirements, hashes, prefer_prereleases=prereleases):
            check_deadline()
            if wheel.name != name:
                raise PolicyRejected('dependency provider returned a different package name')
            try:
                for requirement in wheel.requirements:
                    _safe_requirement(str(requirement))
            except SourceRejected:
                continue
            if not accepts(wheel, requirements, extras):
                continue
            next_selected = {**selected, name: wheel}
            if (sum(item.size for item in next_selected.values()) > policy.max_staging_bytes
                    or sum(item.expanded_size for item in next_selected.values()) > policy.disk_bytes):
                continue
            yield next_selected

    # Iterator frames avoid Python's recursion limit on package closure size and
    # acquire each wheel only when its branch is actually explored.
    stack = [iter(({},))]
    while stack:
        check_deadline()
        try:
            selected = next(stack[-1])
        except StopIteration:
            stack.pop()
            continue
        result = closure(selected)
        if result is None:
            continue
        needed, extras = result
        missing = sorted(set(needed) - set(selected))
        if not missing:
            return tuple(selected[name] for name in sorted(selected))
        name = missing[0]
        stack.append(extend(selected, name, needed[name], extras[name]))
    return None


def resolution_identity(request_identity, snapshot):
    return hashlib.sha256(canonical_json({'request': request_identity,
        'package_index_snapshot': snapshot.model_dump(mode='json')})).hexdigest()


def read_dependency_resolution(store, ref, request_identity, metadata, rules, recipe, policy, environment):
    """Check a retained lock offline, without choosing any alternative artifacts."""
    from .catalog import validate_snapshot
    _reference(ref, 'dependency-resolution')
    try:
        value = DependencyResolution.model_validate_json(_bytes(store, ref, policy.max_staging_bytes))
        if (value.request_identity != request_identity
                or value.identity != resolution_identity(request_identity, value.index_snapshot)):
            raise PolicyRejected('dependency resolution/cache identity mismatch')
        validate_snapshot(store, value.index_snapshot, policy.profile, policy, value.wheels)
        if value.failure is not None:
            if value.wheels:
                raise PolicyRejected('failed dependency resolution contains selected wheels')
            raise DependencyUnavailable('trusted registry snapshot has no compatible complete dependency closure')
        records = tuple(_inspect_wheel(store, item, policy) for item in value.wheels)
        by_name = {wheel.name: wheel for wheel in records}
        if len(by_name) != len(records) or not records:
            raise PolicyRejected('frozen dependency resolution has a duplicate or empty package roster')

        def options(name, requirements, hashes, *, prefer_prereleases=False):
            if name in by_name:
                yield by_name[name]

        selected = _solve(options, metadata, policy.profile, rules, recipe, environment, policy)
        if selected is None or tuple(wheel.item for wheel in selected) != value.wheels:
            raise PolicyRejected('frozen dependency resolution is not the exact declared closure')
        return value, selected
    except (SourceRejected, ValidationError) as exc:
        raise PolicyRejected('invalid frozen dependency resolution') from exc


def _resolution(prepared, policy, metadata, manifests, digest, identity, resolution, selected):
    pins = tuple(DependencyPin(name=wheel.name, version=str(wheel.version), artifact=wheel.item.artifact,
                               sha256=wheel.item.pin.sha256) for wheel in selected)
    values = policy.profile.model_dump()
    values.update(project_name=metadata['project_name'], project_version=metadata['project_version'],
                  manifest_path=metadata['manifest_path'], metadata_sha256=digest, resolution=None,
                  build_backend=metadata['build_backend'], build_requirements=tuple(metadata['build_requirements']),
                  source_roots=tuple(metadata['source_roots']),
                  source_mappings=tuple(metadata['source_mappings']),
                  import_modules=tuple(metadata['import_modules']), entry_points=tuple(metadata['entry_points']),
                  system_packages=tuple(package for package in policy.profile.system_packages
                      if package.name in {requirement.split('=')[0] for requirement in metadata['system_requirements']}),
                  dependencies=tuple(WheelPin(name=wheel.name, version=str(wheel.version),
                      filename=wheel.item.pin.filename, sha256=wheel.item.pin.sha256) for wheel in selected))
    try:
        profile = RuntimeProfile.model_validate(values)
    except ValidationError as exc:
        raise DependencyUnavailable('candidate runtime metadata is outside the supported profile bounds') from exc
    return CandidateResolution(recipe=prepared.recipe, policy=prepared.policy, resolution=resolution,
                               identity=identity, manifest_hashes=manifests, metadata_sha256=digest,
                               profile=profile, dependencies=pins)


def validate_candidate_resolution(value, store, prepared, recipe, policy, source, rules) -> CandidateResolution:
    """Validate the candidate profile and frozen closure entirely offline."""
    try:
        value = CandidateResolution.model_validate(value)
        metadata, rules, environment, manifests, digest, identity, dependency_identity = _candidate_inputs(
            store, prepared, recipe, policy, source, rules)
        if value.identity != identity or value.recipe != prepared.recipe or value.policy != prepared.policy:
            raise PolicyRejected('archived candidate resolution identity mismatch')
        _, selected = read_dependency_resolution(store, value.resolution, dependency_identity,
                                                 metadata, rules, recipe, policy, environment)
        expected = _resolution(prepared, policy, metadata, manifests, digest, identity, value.resolution, selected)
        if value != expected:
            raise PolicyRejected('archived candidate resolution profile or selected closure mismatch')
        return value
    except (SourceRejected, DependencyUnavailable, ValidationError) as exc:
        raise PolicyRejected('invalid archived candidate dependency resolution') from exc
