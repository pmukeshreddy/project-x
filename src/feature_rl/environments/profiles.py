"""Inert, pinned Python repository profiles shared by runtime and packaging."""
import hashlib
import re
import tomllib
from typing import Annotated, Literal

from pydantic import Field, model_validator
from feature_rl.contracts import CommandSpec, NeutralRepair, SeedPolicy, StrictModel


class WheelPin(StrictModel):
    name: Annotated[str, Field(pattern=r'^[A-Za-z0-9][A-Za-z0-9._-]*$')]
    version: Annotated[str, Field(pattern=r'^[A-Za-z0-9][A-Za-z0-9.!+_-]*$')]
    filename: Annotated[str, Field(pattern=r'^[A-Za-z0-9][A-Za-z0-9._+!-]*\.whl$')]
    sha256: Annotated[str, Field(pattern=r'^[0-9a-f]{64}$')]


class SourceMapping(StrictModel):
    source: str
    wheel: str


class SystemPackagePin(StrictModel):
    """Required Debian userspace package, supplied by the digest-pinned base."""
    name: Annotated[str, Field(pattern=r'^[a-z0-9][a-z0-9+.-]*$')]
    version: Annotated[str, Field(pattern=r'^[0-9][A-Za-z0-9.+:~_-]*$')]


class RuntimeProfile(StrictModel):
    version: Literal['python-wheel-profile-v1'] = 'python-wheel-profile-v1'
    profile_id: Annotated[str, Field(pattern=r'^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$')]
    project_name: Annotated[str, Field(pattern=r'^[A-Za-z0-9][A-Za-z0-9._-]*$')]
    project_version: Annotated[str, Field(pattern=r'^[A-Za-z0-9][A-Za-z0-9.!+_-]*$')]
    interpreter_version: Annotated[str, Field(pattern=r'^3\.[0-9]+\.[0-9]+$')]
    manifest_path: str = 'pyproject.toml'
    manifest_sha256: Annotated[str, Field(pattern=r'^[0-9a-f]{64}$')] | None = None
    build_backend: Annotated[str, Field(min_length=1, max_length=256)]
    build_requirements: Annotated[tuple[str, ...], Field(min_length=1, max_length=64)]
    source_roots: Annotated[tuple[str, ...], Field(min_length=1, max_length=32)]
    source_mappings: Annotated[tuple[SourceMapping, ...], Field(min_length=1, max_length=32)]
    import_modules: Annotated[tuple[str, ...], Field(min_length=1, max_length=32)]
    entry_points: Annotated[tuple[str, ...], Field(min_length=1, max_length=64)]
    supported_observables: Annotated[tuple[Literal['JSON return value', 'CLI exit code',
        'standard output', 'standard error', 'combined terminal output'], ...], Field(min_length=1)]
    dependencies: Annotated[tuple[WheelPin, ...], Field(min_length=1, max_length=64)]
    system_packages: Annotated[tuple[SystemPackagePin, ...], Field(max_length=128)] = Field(
        default=(), exclude_if=lambda value: not value)
    neutral_repairs: Annotated[tuple[NeutralRepair, ...], Field(max_length=2)] = ()

    @model_validator(mode='after')
    def canonical_profile(self):
        from .archive import safe_path
        for path in (self.manifest_path, *self.source_roots,
                     *(m.source for m in self.source_mappings), *(m.wheel for m in self.source_mappings)):
            if safe_path(path) != path or any(part in {'.pytest_cache', 'tests', 'test', 'build', 'dist', '.venv'}
                                               for part in path.split('/')):
                raise ValueError('profile contains a noncanonical or protected source path')
        if self.manifest_path != 'pyproject.toml':
            raise ValueError('runtime profiles currently support pyproject.toml wheel builds')
        for values in (self.source_roots, self.import_modules, self.entry_points,
                       tuple(p.name for p in self.dependencies), tuple(p.filename for p in self.dependencies),
                       tuple(p.name for p in self.system_packages),
                       tuple(m.source for m in self.source_mappings), tuple(m.wheel for m in self.source_mappings)):
            if len(values) != len(set(values)):
                raise ValueError('duplicate runtime profile entry')
        for value in (*self.import_modules, *self.entry_points):
            if not re.fullmatch(r'[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*', value):
                raise ValueError('profile imports must be dotted Python identifiers')
        for entry in self.entry_points:
            if not any(entry == module or entry.startswith(module+'.') for module in self.import_modules):
                raise ValueError('entry point is outside the declared import modules')
        for mapping in self.source_mappings:
            if not any(mapping.source == root or mapping.source.startswith(root+'/') for root in self.source_roots):
                raise ValueError('wheel mapping is outside the source roots')
            if '.dist-info' in mapping.wheel or '.data' in mapping.wheel:
                raise ValueError('source cannot replace wheel metadata or install schemes')
            if mapping.source != mapping.wheel and not mapping.source.endswith('/'+mapping.wheel):
                raise ValueError('wheel source mappings must preserve the Python import path')
        for attribute in ('source', 'wheel'):
            names = [getattr(m, attribute) for m in self.source_mappings]
            if any(a != b and a.startswith(b+'/') for a in names for b in names):
                raise ValueError('overlapping wheel source mappings')
        if any(self.manifest_path == root or self.manifest_path.startswith(root+'/') for root in self.source_roots):
            raise ValueError('build manifest must be immutable')
        normalized = lambda value: re.sub(r'[-_.]+', '-', value).lower()
        names = [normalized(pin.name) for pin in self.dependencies]
        if len(names) != len(set(names)) or normalized(self.project_name) in names:
            raise ValueError('dependency roster duplicates or supplies the target project')
        return self

    @property
    def wheel_stem(self):
        return re.sub(r'[-_.]+', '_', self.project_name).lower()+'-'+self.project_version

    @property
    def wheel_filename(self):
        return self.wheel_stem+'-py3-none-any.whl'

    @property
    def environment(self):
        return (('PYTHONPATH', '/workspace/site:/workspace/deps'), ('PYTHONSAFEPATH', '1'),
                ('PYTEST_DISABLE_PLUGIN_AUTOLOAD', '1'))

    @property
    def development_environment(self):
        parents = tuple(dict.fromkeys(m.source[:-len(m.wheel)].rstrip('/') for m in self.source_mappings))
        paths = tuple('/workspace/source'+('/'+parent if parent else '') for parent in parents)
        return (('PYTHONPATH', ':'.join((*paths, '/workspace/deps'))), *self.environment[1:])

    @property
    def setup(self):
        from .runtime import BUILD
        from .images import LINK_DEPS
        install = CommandSpec(argv=('python', '-I', '-m', 'pip', '--isolated', 'install',
            '--no-index', '--no-deps', '--no-compile', '--target', '/workspace/site',
            '/workspace/built/'+self.wheel_filename), working_directory='/workspace', timeout_seconds=30.0)
        return LINK_DEPS, BUILD, install


    def validate_source(self, source):
        from .models import PolicyRejected
        try:
            data = source.files[self.manifest_path].data
            manifest = tomllib.loads(data.decode())
        except (KeyError, ValueError, UnicodeError) as exc:
            raise PolicyRejected('invalid repository build manifest') from exc
        if self.manifest_sha256 is not None and hashlib.sha256(data).hexdigest() != self.manifest_sha256:
            raise PolicyRejected('repository build manifest digest mismatch')
        build = manifest.get('build-system', {})
        if build != {'requires': list(self.build_requirements), 'build-backend': self.build_backend}:
            raise PolicyRejected('repository build-system identity differs from profile')
        project = manifest.get('project', {})
        if (('name' in project and project['name'] != self.project_name)
                or ('version' in project and project['version'] != self.project_version)):
            raise PolicyRejected('declared project identity differs from profile')
        if self.manifest_sha256 is None and (project.get('name') != self.project_name or
                                            project.get('version') != self.project_version):
            raise PolicyRejected('repository project identity differs from profile; dynamic metadata requires a pinned manifest')
        if any(not any(name == m.source or name.startswith(m.source+'/') for name in source.files)
               for m in self.source_mappings):
            raise PolicyRejected('profile wheel source mapping is absent from the source')

    def validate_allowed_changes(self, rules):
        from .models import SourceRejected
        from .archive import safe_path
        if rules.dependencies != 'forbidden' or rules.dependency_artifacts or rules.additional_artifact_types:
            raise SourceRejected('runtime profiles require fixed dependencies and Python source changes')
        for root in rules.source_roots:
            if safe_path(root) != root or not any(root == allowed or root.startswith(allowed+'/')
                                                for allowed in self.source_roots):
                raise SourceRejected('source change root is outside the repository profile')
        for path in rules.forbidden_paths:
            if safe_path(path) != path:
                raise SourceRejected('noncanonical forbidden source path')


def dependency_files(store, pins, policy):
    from .archive import SourceFile
    from .models import PolicyRejected
    expected = {p.name: p for p in policy.profile.dependencies}
    if len(pins) != len(expected) or {p.name for p in pins} != set(expected):
        raise PolicyRejected('exact profile dependency pins required')
    result = {}; total = 0
    for pin in pins:
        spec = expected[pin.name]
        if pin.version != spec.version or pin.sha256 != spec.sha256 or pin.artifact.kind != 'dependency-wheel':
            raise PolicyRejected('dependency identity differs from profile')
        cap = policy.max_staging_bytes
        data = store.get_bytes(pin.artifact, max_envelope_bytes=4*((cap+2)//3)+4096, max_payload_bytes=cap)
        total += len(data)
        if hashlib.sha256(data).hexdigest() != spec.sha256 or total > cap:
            raise PolicyRejected('dependency bytes/aggregate mismatch')
        result[spec.filename] = SourceFile(data, False)
    requirements = ''.join(f'{p.name}=={p.version} --hash=sha256:{p.sha256}\n'
                           for p in sorted(expected.values(), key=lambda p: p.name)).encode()
    result['requirements.txt'] = SourceFile(requirements, False)
    return result


def validate_recipe_profile(recipe, policy, store):
    from .models import PolicyRejected
    from .images import validate_runtime_image
    profile = policy.profile
    validate_runtime_image(recipe, policy, store)
    setup = profile.setup
    if (recipe.interpreter_version != profile.interpreter_version
            or recipe.services or recipe.network_policy != 'none' or recipe.setup != setup
            or recipe.reset != recipe.setup or tuple((v.name, v.value) for v in recipe.environment) != profile.environment
            or recipe.locale != 'C.UTF-8' or recipe.timezone != 'UTC'
            or recipe.randomness != SeedPolicy(algorithm='PYTHONHASHSEED', seeds=(0,), same_cases_within_group=True)):
        raise PolicyRejected('recipe differs from pinned runtime profile')
    if recipe.neutral_repairs != profile.neutral_repairs:
        raise PolicyRejected('neutral repairs differ from configured profile')
    for repair in recipe.neutral_repairs:
        store.get_bytes(repair.patch, max_envelope_bytes=131072, max_payload_bytes=65536)
    for field in ('cpu_seconds', 'memory_bytes', 'pids', 'disk_bytes', 'output_bytes'):
        if getattr(recipe.limits, field) != getattr(policy, field):
            raise PolicyRejected('runtime resource policy drift')
    if recipe.limits.wall_seconds != policy.lifecycle_seconds:
        raise PolicyRejected('runtime wall policy drift')
    return profile
