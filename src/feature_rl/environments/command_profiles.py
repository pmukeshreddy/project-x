"""Pinned command toolchains sharing the existing isolated runtime boundary."""
import hashlib
from typing import Annotated, Literal

from pydantic import Field, model_validator
from feature_rl.artifacts import canonical_json
from feature_rl.contracts import ArtifactRef, CommandSpec, StrictModel
from .profiles import SystemPackagePin
from .services import LocalService


PREPARE_SITE = CommandSpec(argv=('/usr/local/bin/python', '-I', '-c',
    "import pathlib,shutil;shutil.copytree('/workspace/source','/workspace/site');"
    "shutil.copytree('/opt/feature-rl/toolchain-cache','/workspace/toolchain-cache');"
    "pathlib.Path('/workspace/home').mkdir();pathlib.Path('/workspace/tmp').mkdir()"), working_directory='/workspace', timeout_seconds=30.0)


class CommandRuntimeProfile(StrictModel):
    version: Literal['command-runtime-profile-v1'] = 'command-runtime-profile-v1'
    profile_id: Annotated[str, Field(pattern=r'^toolchain-[0-9a-f]{32}$')]
    language: Literal['node', 'rust', 'go']
    dependency_manager: Literal['npm', 'cargo', 'go']
    project_name: Annotated[str, Field(min_length=1, max_length=256)]
    project_version: str
    interpreter_version: Annotated[str, Field(pattern=r'^[0-9]+\.[0-9]+\.[0-9]+$')]
    harness_interpreter_version: Annotated[str, Field(pattern=r'^3\.[0-9]+\.[0-9]+$')]
    toolchain_selector: Annotated[str, Field(pattern=r'^[0-9]+(?:\.[0-9]+){0,2}$')]
    manager_version: str
    manifest_path: str
    manifest_paths: Annotated[tuple[str, ...], Field(min_length=1, max_length=256)]
    manifest_hashes: dict[str, str | None]
    source_roots: Annotated[tuple[str, ...], Field(min_length=1, max_length=2000)]
    entry_points: Annotated[tuple[str, ...], Field(min_length=1, max_length=64)]
    build_commands: Annotated[tuple[CommandSpec, ...], Field(min_length=1, max_length=16)]
    dependency_sha256: Annotated[str, Field(pattern=r'^[0-9a-f]{64}$')]
    system_requirements: tuple[str, ...] = ()
    system_packages: tuple[SystemPackagePin, ...] = ()
    services: Annotated[tuple[LocalService, ...], Field(max_length=4)] = ()
    resolution: ArtifactRef | None = None
    supported_observables: tuple[Literal['JSON return value', 'CLI exit code', 'standard output',
                                         'standard error', 'combined terminal output'], ...] = (
        'JSON return value', 'CLI exit code', 'standard output', 'standard error', 'combined terminal output')
    neutral_repairs: tuple[()] = ()

    @model_validator(mode='after')
    def validate_profile(self):
        from .archive import safe_path
        if self.dependency_manager != {'node': 'npm', 'rust': 'cargo', 'go': 'go'}[self.language]:
            raise ValueError('toolchain/dependency manager mismatch')
        if {value.split('=')[0] for value in self.system_requirements} != {pin.name for pin in self.system_packages}:
            raise ValueError('resolved system packages differ from declarations')
        if not (self.interpreter_version == self.toolchain_selector
                or self.interpreter_version.startswith(self.toolchain_selector + '.')):
            raise ValueError('resolved toolchain differs from its declared selector')
        for path in (self.manifest_path, *self.manifest_paths, *self.manifest_hashes, *self.source_roots, *self.entry_points):
            if safe_path(path) != path:
                raise ValueError('noncanonical toolchain profile path')
        if (set(self.manifest_paths) != {path for path, digest in self.manifest_hashes.items() if digest is not None}
                or self.manifest_path not in self.manifest_paths
                or any(digest is not None and (len(digest) != 64 or any(c not in '0123456789abcdef' for c in digest))
                       for digest in self.manifest_hashes.values())):
            raise ValueError('toolchain manifest/hash roster mismatch')
        for command in self.build_commands:
            if command.working_directory != '/workspace/site' or command.timeout_seconds > 120:
                raise ValueError('toolchain build command exceeds its sandbox declaration')
        return self

    @classmethod
    def from_plan(cls, plan, *, interpreter_version, harness_interpreter_version, dependency_sha256,
                  system_packages=(), resolution=None):
        fields = {key: plan[key] for key in ('language', 'dependency_manager', 'project_name', 'project_version',
                   'toolchain_selector', 'manager_version', 'manifest_path', 'manifest_paths', 'manifest_hashes',
                   'source_roots', 'entry_points', 'build_commands', 'system_requirements', 'services')}
        identity = canonical_json({**fields, 'build_commands': [c.model_dump(mode='json') for c in fields['build_commands']],
                                   'services': [service.model_dump(mode='json') for service in fields['services']],
                                   'interpreter_version': interpreter_version, 'dependency_sha256': dependency_sha256})
        return cls(**fields, profile_id='toolchain-' + hashlib.sha256(identity).hexdigest()[:32],
                   interpreter_version=interpreter_version, harness_interpreter_version=harness_interpreter_version,
                   dependency_sha256=dependency_sha256, system_packages=system_packages, resolution=resolution)

    @property
    def setup(self):
        return PREPARE_SITE, *self.build_commands

    @property
    def environment(self):
        common = (('HOME', '/workspace/home'), ('TMPDIR', '/workspace/tmp'),
                  ('PATH', '/workspace/site/node_modules/.bin:/workspace/site/bin:/workspace/site/target/release:/usr/local/go/bin:/usr/local/cargo/bin:/usr/local/bin:/usr/bin:/bin'))
        return common + {
            'node': (('npm_config_cache', '/workspace/toolchain-cache/npm'), ('npm_config_offline', 'true'),
                     ('npm_config_update_notifier', 'false'), ('NODE_PATH', '/workspace/site/node_modules')),
            'rust': (('CARGO_HOME', '/workspace/toolchain-cache/cargo'), ('RUSTUP_HOME', '/usr/local/rustup'),
                     ('CARGO_NET_OFFLINE', 'true'), ('CARGO_BUILD_JOBS', '1')),
            'go': (('GOMODCACHE', '/workspace/toolchain-cache/go'), ('GOCACHE', '/workspace/go-build-cache'),
                   ('GOPATH', '/workspace/go'), ('GOPROXY', 'off'), ('GOSUMDB', 'off'),
                   ('GOTOOLCHAIN', 'local'), ('GOFLAGS', '-mod=readonly -p=1'), ('GOMAXPROCS', '1')),
        }[self.language]

    @property
    def development_environment(self):
        return self.environment

    @property
    def entry_point_paths(self):
        return {entry: '/workspace/site/' + entry for entry in self.entry_points}

    def service_recipes(self, image):
        return tuple(service.recipe(image) for service in self.services)

    def validate_source(self, source):
        from .models import PolicyRejected
        from .toolchains import infer_toolchain
        plan = infer_toolchain(source)
        if plan['language'] != self.language or plan['manifest_hashes'] != self.manifest_hashes:
            raise PolicyRejected('candidate toolchain manifest/lock differs from the frozen dependency supply')
        if (plan['build_commands'] != self.build_commands or plan['entry_points'] != self.entry_points
                or plan['services'] != self.services or plan['system_requirements'] != self.system_requirements):
            raise PolicyRejected('candidate toolchain build/entry-point declarations differ from the frozen profile')

    def validate_allowed_changes(self, rules):
        from .models import SourceRejected
        from .archive import safe_path
        if rules.dependency_artifacts:
            raise SourceRejected('command toolchain dependency changes require a new frozen repository resolution')
        for root in rules.source_roots:
            if safe_path(root) != root or not any(root == allowed or root.startswith(allowed + '/') for allowed in self.source_roots):
                raise SourceRejected('source change root is outside the repository toolchain profile')
        for path in rules.forbidden_paths:
            safe_path(path)
