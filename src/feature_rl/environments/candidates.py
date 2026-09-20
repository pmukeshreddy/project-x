"""Pure, bounded resolution of candidate declarations against immutable wheels.

Only repository text and ZIP metadata are inspected on the controller.  The
catalog is frozen when a task is prepared; this module never downloads, builds,
imports a project, or writes candidate state.
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


_SOLVER_VERSION = 'offline-wheel-solver-v1'
_CATALOG_WHEELS = 2048
_CATALOG_BYTES = 4 * 1024 * 1024
_CATALOG_PAYLOAD_BYTES = 512 * 1024 * 1024
_CATALOG_EXPANDED_BYTES = 1024 * 1024 * 1024
_METADATA_BYTES = 1024 * 1024
_REQUIREMENTS = 4096
_SOLVER_STEPS = 200000
_SOLVER_BRANCHES = 4096
_MARKER_KEYS = frozenset({
    'implementation_name', 'implementation_version', 'os_name', 'platform_machine',
    'platform_python_implementation', 'platform_release', 'platform_system',
    'platform_version', 'python_full_version', 'python_version', 'sys_platform',
})
_DIGEST = Annotated[str, Field(pattern=r'^[0-9a-f]{64}$')]


class CatalogWheel(StrictModel):
    pin: WheelPin
    artifact: ArtifactRef


class DependencyCatalog(StrictModel):
    version: Literal['offline-wheel-catalog-v1'] = 'offline-wheel-catalog-v1'
    wheels: Annotated[tuple[CatalogWheel, ...], Field(min_length=1, max_length=_CATALOG_WHEELS)]


class CandidateResolution(StrictModel):
    version: Literal['candidate-dependencies-v1'] = 'candidate-dependencies-v1'
    recipe: ArtifactRef
    policy: ArtifactRef
    catalog: ArtifactRef
    identity: _DIGEST
    manifest_hashes: dict[str, str]
    metadata_sha256: _DIGEST
    profile: RuntimeProfile
    dependencies: Annotated[tuple[DependencyPin, ...], Field(min_length=1, max_length=64)]


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
        raise PolicyRejected('dependency catalog wheel payload hash mismatch')
    try:
        if safe_path(pin.filename) != pin.filename or '/' in pin.filename:
            raise PolicyRejected('noncanonical dependency wheel filename')
        _tags('-'.join(pin.filename[:-4].split('-')[-3:]))
        name, version, build, tags = parse_wheel_filename(pin.filename)
        if name != canonicalize_name(pin.name) or version != Version(pin.version):
            raise PolicyRejected('dependency catalog filename/pin identity mismatch')
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
                if any(part.endswith('.dist-info') and part != metadata_root for part in path.split('/')):
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
            if len(python_headers) > 1 or len(requires) > _REQUIREMENTS or len(extras) > 256:
                raise PolicyRejected('dependency wheel requirement metadata cap')
            if any(not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]*', extra) for extra in extras):
                raise PolicyRejected('invalid dependency wheel extra')
            return _Wheel(item, name, version, build, frozenset(declared_tags),
                          SpecifierSet(python_headers[0] if python_headers else ''),
                          tuple(Requirement(value) for value in requires),
                          frozenset(canonicalize_name(extra) for extra in extras), len(data), expanded)
    except (SourceRejected, zipfile.BadZipFile, ValueError, OSError, RuntimeError,
            NotImplementedError, EOFError, zlib.error) as exc:
        raise PolicyRejected('invalid frozen dependency wheel: ' + str(exc)) from exc


def _inspect_catalog(store, catalog, policy):
    records, names, artifacts = [], set(), set()
    compressed = expanded = 0
    for item in catalog.wheels:
        if item.pin.filename in names or item.artifact in artifacts:
            raise PolicyRejected('duplicate dependency catalog wheel filename/artifact')
        names.add(item.pin.filename)
        artifacts.add(item.artifact)
        wheel = _inspect_wheel(store, item, policy)
        compressed += wheel.size
        expanded += wheel.expanded_size
        if compressed > _CATALOG_PAYLOAD_BYTES or expanded > _CATALOG_EXPANDED_BYTES:
            raise PolicyRejected('aggregate dependency catalog byte cap')
        records.append(wheel)
    return tuple(records)


def _load_catalog(store, ref, policy):
    _reference(ref, 'dependency-catalog')
    try:
        catalog = DependencyCatalog.model_validate_json(_bytes(store, ref, _CATALOG_BYTES))
    except ValidationError as exc:
        raise PolicyRejected('invalid frozen dependency catalog') from exc
    return catalog, _inspect_catalog(store, catalog, policy)


def read_catalog(store, ref, policy) -> DependencyCatalog:
    """Read and validate a bounded authoring/public catalog and every wheel."""
    return _load_catalog(store, ref, policy)[0]


def freeze_catalog(store, policy, pins, extra_catalog=None) -> ArtifactRef:
    """Freeze baseline wheels plus an optional controller-supplied wheel catalog."""
    if policy.profile is None:
        raise PolicyRejected('dependency catalog requires a resolved baseline profile')
    try:
        pins = tuple(DependencyPin.model_validate(pin) for pin in pins)
        by_name = {canonicalize_name(pin.name): pin for pin in pins}
        expected = {canonicalize_name(pin.name): pin for pin in policy.profile.dependencies}
        if len(by_name) != len(pins) or set(by_name) != set(expected):
            raise PolicyRejected('catalog baseline dependency roster mismatch')
        items = []
        for name, spec in sorted(expected.items()):
            pin = by_name[name]
            if Version(pin.version) != Version(spec.version) or pin.sha256 != spec.sha256:
                raise PolicyRejected('catalog baseline dependency identity mismatch')
            items.append(CatalogWheel(pin=spec, artifact=pin.artifact))
        if extra_catalog is not None:
            items.extend(read_catalog(store, extra_catalog, policy).wheels)
        unique = {}
        for item in items:
            previous = unique.setdefault(item.pin.filename, item)
            if previous != item:
                raise PolicyRejected('conflicting dependency catalog wheel filename')
        catalog = DependencyCatalog(wheels=tuple(sorted(unique.values(), key=lambda item: (
            canonicalize_name(item.pin.name), Version(item.pin.version), item.pin.filename,
            item.pin.sha256, item.artifact.sha256))))
        _inspect_catalog(store, catalog, policy)
        data = canonical_json(catalog.model_dump(mode='json'))
        if len(data) > _CATALOG_BYTES:
            raise PolicyRejected('dependency catalog serialized byte cap')
        return store.put_bytes(data, 'dependency-catalog', Visibility.AUTHORING)
    except (ArtifactError, ValidationError, InvalidVersion) as exc:
        raise PolicyRejected('cannot freeze dependency catalog') from exc


def _baseline_entries(recipe, policy, catalog):
    if policy.profile is None:
        raise PolicyRejected('dependency catalog requires a runtime profile')
    expected = {canonicalize_name(pin.name): pin for pin in policy.profile.dependencies}
    pins = {canonicalize_name(pin.name): pin for pin in recipe.dependencies}
    if len(pins) != len(recipe.dependencies) or set(pins) != set(expected):
        raise PolicyRejected('recipe/profile dependency roster mismatch')
    available = {(canonicalize_name(item.pin.name), item.pin.version, item.pin.filename,
                  item.pin.sha256, item.artifact) for item in catalog.wheels}
    if any(item.artifact not in recipe.provenance.inputs for item in catalog.wheels):
        raise PolicyRejected('recipe dependency catalog wheel lost provenance')
    for name, spec in expected.items():
        pin = pins[name]
        if (pin.version != spec.version or pin.sha256 != spec.sha256
                or (name, spec.version, spec.filename, pin.sha256, pin.artifact) not in available):
            raise PolicyRejected('frozen catalog omits or changes a baseline dependency')


def _catalog_binding(recipe):
    ref = recipe.dependency_catalog
    _reference(ref, 'dependency-catalog')
    if ref not in recipe.provenance.inputs:
        raise PolicyRejected('recipe dependency catalog lost provenance')
    return ref


def validate_catalog(recipe, policy, store):
    """Verify the recipe retains its catalog and complete baseline wheel inputs."""
    catalog = read_catalog(store, _catalog_binding(recipe), policy)
    _baseline_entries(recipe, policy, catalog)
    return catalog


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
        if not isinstance(values, list) or len(values) > _REQUIREMENTS:
            raise SourceRejected('candidate manifest requires a bounded dependency list')
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
    catalog = _catalog_binding(recipe)
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
    for filename in ('candidates.py', 'inference.py', 'profiles.py'):
        try:
            implementation[filename] = hashlib.sha256(Path(__file__).with_name(filename).read_bytes()).hexdigest()
        except OSError as exc:
            raise PolicyRejected('candidate resolver implementation identity is unavailable') from exc
    identity = hashlib.sha256(canonical_json({
        'solver': _SOLVER_VERSION, 'implementation': implementation, 'packaging': packaging_version,
        'recipe': prepared.recipe.model_dump(mode='json'), 'policy': prepared.policy.model_dump(mode='json'),
        'catalog': catalog.model_dump(mode='json'), 'rules': rules.model_dump(mode='json'),
        'manifest_hashes': manifests, 'metadata_sha256': digest,
        'profile_inputs': {'manifest_path': metadata['manifest_path'], 'source_roots': metadata['source_roots']},
    })).hexdigest()
    return metadata, rules, environment, manifests, digest, identity


def candidate_identity(store, prepared, recipe, policy, source, rules) -> str:
    """Compute the candidate's cache identity without reading or searching wheels."""
    return _candidate_inputs(store, prepared, recipe, policy, source, rules)[-1]


def _solve(records, metadata, profile, rules, recipe, environment, policy):
    steps = branches = 0

    def charge():
        nonlocal steps
        steps += 1
        if steps > _SOLVER_STEPS:
            raise DependencyUnavailable('offline dependency resolution exceeded its bounded work budget')

    baseline = {pin.artifact for pin in recipe.dependencies}
    allowed = None
    if rules.dependencies == 'forbidden' or rules.dependency_artifacts:
        allowed = baseline | set(rules.dependency_artifacts)
    ranks = {tag: rank for rank, tag in reversed(tuple(enumerate(profile.compatible_tags)))}
    options = {}
    for wheel in records:
        if (allowed is not None and wheel.item.artifact not in allowed
                or not wheel.tags.intersection(ranks)
                or not wheel.requires_python.contains(profile.interpreter_version, prereleases=True)):
            continue
        try:
            for requirement in wheel.requirements:
                charge()
                _safe_requirement(str(requirement))
        except SourceRejected:
            # Vendor metadata cannot turn a safe candidate into a rejection;
            # another safe version may still satisfy the complete closure.
            continue
        options.setdefault(wheel.name, []).append(wheel)
    for values in options.values():
        values.sort(key=lambda wheel: (wheel.item.pin.filename, wheel.item.pin.sha256, wheel.item.artifact.sha256))
        values.sort(key=lambda wheel: wheel.build, reverse=True)
        values.sort(key=lambda wheel: min(ranks[tag] for tag in wheel.tags if tag in ranks))
        values.sort(key=lambda wheel: wheel.version, reverse=True)
    roots = tuple(_safe_requirement(value) for value in (*metadata['build_requirements'], *metadata['requirements']))
    constraints = {}

    def active(requirement, extra=''):
        charge()
        try:
            return requirement.marker is None or requirement.marker.evaluate({**environment, 'extra': extra})
        except (ValueError, KeyError) as exc:
            raise SourceRejected('candidate dependency marker cannot be evaluated safely') from exc

    for value in metadata['constraints']:
        requirement = _safe_requirement(value)
        if active(requirement):
            constraints.setdefault(canonicalize_name(requirement.name), []).append(requirement)

    def accepts(wheel, requirements, extras):
        charge()
        hashes = metadata['dependency_hashes'].get(wheel.name)
        return (extras <= wheel.extras and (not hashes or wheel.item.pin.sha256 in hashes)
                and all(requirement.specifier.contains(wheel.version, prereleases=True)
                        for requirement in (*requirements, *constraints.get(wheel.name, ()))))

    def closure(selected):
        needed, extras, expanded, known = {}, {}, set(), set()
        pending = [(requirement, '', False) for requirement in roots]
        while pending:
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
            if len(needed) > 64 or len(known) > _REQUIREMENTS:
                raise DependencyUnavailable('candidate dependency closure exceeds the fixed task bounds')
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

    def search(selected):
        nonlocal branches
        branches += 1
        if branches > _SOLVER_BRANCHES:
            raise DependencyUnavailable('offline dependency resolution exceeded its bounded search budget')
        result = closure(selected)
        if result is None:
            return None
        needed, extras = result
        missing = sorted(set(needed) - set(selected))
        if not missing:
            return tuple(selected[name] for name in sorted(selected))
        choices = []
        for name in missing:
            candidates = [wheel for wheel in options.get(name, ()) if accepts(wheel, needed[name], extras[name])]
            if not candidates:
                return None
            if not any(requirement.specifier.prereleases for requirement in (*needed[name], *constraints.get(name, ()))):
                candidates.sort(key=lambda wheel: wheel.version.is_prerelease)
            # Preserve an already frozen usable baseline choice, while allowing
            # constraints or later transitive conflicts to select any alternative.
            candidates.sort(key=lambda wheel: wheel.item.artifact not in baseline)
            choices.append((len(candidates), name, candidates))
        _, name, candidates = min(choices, key=lambda choice: (choice[0], choice[1]))
        for wheel in candidates:
            next_selected = {**selected, name: wheel}
            if (sum(item.size for item in next_selected.values()) > policy.max_staging_bytes
                    or sum(item.expanded_size for item in next_selected.values()) > policy.disk_bytes):
                continue
            result = search(next_selected)
            if result is not None:
                return result
        return None

    selected = search({})
    if not selected:
        raise DependencyUnavailable('fixed offline wheel catalog has no compatible complete candidate dependency closure')
    return selected


def _resolution(prepared, recipe, policy, metadata, manifests, digest, identity, selected):
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
    return CandidateResolution(recipe=prepared.recipe, policy=prepared.policy, catalog=recipe.dependency_catalog,
                               identity=identity, manifest_hashes=manifests, metadata_sha256=digest,
                               profile=profile, dependencies=pins)


def candidate_resolution(store, prepared, recipe, policy, source, rules) -> CandidateResolution:
    """Resolve source-backed declarations without changing recipe, store or state."""
    metadata, rules, environment, manifests, digest, identity = _candidate_inputs(
        store, prepared, recipe, policy, source, rules)
    catalog, records = _load_catalog(store, _catalog_binding(recipe), policy)
    _baseline_entries(recipe, policy, catalog)
    selected = _solve(records, metadata, policy.profile, rules, recipe, environment, policy)
    return _resolution(prepared, recipe, policy, metadata, manifests, digest, identity, selected)


def validate_candidate_resolution(value, store, prepared, recipe, policy, source, rules) -> CandidateResolution:
    """Validate archived selected wheels and their closure, with no alternative search.

    The available roster is reduced to the archived artifacts before walking its
    closure. No other version can be chosen and no candidate state is written.
    """
    try:
        value = CandidateResolution.model_validate(value)
        metadata, rules, environment, manifests, digest, identity = _candidate_inputs(
            store, prepared, recipe, policy, source, rules)
        if (value.identity != identity or value.recipe != prepared.recipe or value.policy != prepared.policy
                or value.catalog != recipe.dependency_catalog):
            raise PolicyRejected('archived candidate resolution identity mismatch')
        catalog, records = _load_catalog(store, _catalog_binding(recipe), policy)
        _baseline_entries(recipe, policy, catalog)
        artifacts = {pin.artifact for pin in value.dependencies}
        selected_records = tuple(wheel for wheel in records if wheel.item.artifact in artifacts)
        if len(artifacts) != len(value.dependencies) or len(selected_records) != len(artifacts):
            raise PolicyRejected('archived candidate dependency artifact roster mismatch')
        selected = _solve(selected_records, metadata, policy.profile, rules, recipe, environment, policy)
        expected = _resolution(prepared, recipe, policy, metadata, manifests, digest, identity, selected)
        if value != expected:
            raise PolicyRejected('archived candidate resolution profile or selected closure mismatch')
        return value
    except (SourceRejected, DependencyUnavailable, ValidationError) as exc:
        raise PolicyRejected('invalid archived candidate dependency resolution') from exc
