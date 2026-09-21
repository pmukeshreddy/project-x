"""Repository-profile discovery via an installed B wheel in the real sandbox."""
from dataclasses import dataclass
import json
from typing import Annotated, Literal
from pydantic import Field, TypeAdapter, model_validator

from feature_rl.artifacts import canonical_json
from feature_rl.contracts import ArtifactRef, CommandSpec, CostRecord, StrictModel, Visibility
from feature_rl.environments import ExecutionRequest
from .models import GroundedSource


DISCOVERY_CODE = r'''import importlib, importlib.metadata, inspect, json, platform, sys
settings=json.loads(sys.stdin.buffer.read())
modules={name:importlib.import_module(name) for name in settings['import_modules']}
def resolve(name):
 parts=name.split('.')
 for n in range(len(parts),0,-1):
  prefix='.'.join(parts[:n])
  try:value=importlib.import_module(prefix)
  except ModuleNotFoundError as error:
   if error.name!=prefix and not prefix.startswith(error.name+'.'):raise
   continue
  for attribute in parts[n:]:value=getattr(value,attribute)
  return value
 raise ImportError(name)
signatures={}
for entry in settings['entry_points']:
 value=resolve(entry)
 try:signatures[entry]=str(inspect.signature(value)) if callable(value) else None
 except (ValueError,TypeError):signatures[entry]=None
value={'project_name':settings['project_name'],
 'project_version':importlib.metadata.version(settings['project_name']),
 'interpreter_version':platform.python_version(),
 'module_files':{name:module.__file__ or next(iter(module.__path__)) for name,module in modules.items()},
 'entry_points':settings['entry_points'], 'supported_observables':settings['supported_observables'],
 'signatures':signatures}
print(json.dumps(value,sort_keys=True,separators=(',',':')),end='')
'''


COMMAND_DISCOVERY_CODE = r'''import json, pathlib, re, subprocess, sys
settings=json.loads(sys.stdin.buffer.read())
commands={'node':['/usr/local/bin/node','--version'],
 'rust':['/usr/local/cargo/bin/rustc','--version'],
 'go':['/usr/local/go/bin/go','version']}
version=subprocess.run(commands[settings['language']],check=True,capture_output=True,text=True,timeout=10).stdout
match=re.search(r'\b(?:v|go)?(\d+\.\d+\.\d+)\b',version)
if match is None:raise RuntimeError('toolchain did not report a complete version')
root=pathlib.Path('/workspace/site');files={}
for entry in settings['entry_points']:
 path=root/entry
 if not path.is_file() or not path.resolve().is_relative_to(root):
  raise RuntimeError('built entry point is missing or escapes its installation: '+entry)
 files[entry]=str(path)
value={key:settings[key] for key in ('language','project_name','project_version','entry_points','supported_observables')}
value.update(interpreter_version=match[1],entry_point_files=files)
print(json.dumps(value,sort_keys=True,separators=(',',':')),end='')
'''


class RuntimeProbeObservation(StrictModel):
    project_name: str
    project_version: str
    interpreter_version: str
    module_files: Annotated[dict[str, str], Field(min_length=1, max_length=32)]
    entry_points: Annotated[tuple[str, ...], Field(min_length=1, max_length=64)]
    supported_observables: Annotated[tuple[str, ...], Field(min_length=1)]
    signatures: Annotated[dict[str, str | None], Field(min_length=1, max_length=64)]


class RuntimeDiscoveryObservation(RuntimeProbeObservation):
    version: Literal['runtime-discovery-v1'] = 'runtime-discovery-v1'
    profile_sha256: Annotated[str, Field(pattern=r'^[0-9a-f]{64}$')]
    baseline: ArtifactRef
    recipe: ArtifactRef
    build_evidence_sha256: Annotated[str, Field(pattern=r'^[0-9a-f]{64}$')]
    execution_evidence_sha256: Annotated[str, Field(pattern=r'^[0-9a-f]{64}$')]

    @model_validator(mode='after')
    def exact_execution_binding(self):
        if self.baseline.kind != 'source-archive' or self.baseline.visibility is not Visibility.AUTHORING:
            raise ValueError('discovery must bind the authoring baseline')
        if self.recipe.kind != 'EnvironmentRecipe' or self.recipe.visibility is not Visibility.AUTHORING:
            raise ValueError('discovery must bind the authoring recipe')
        if set(self.signatures) != set(self.entry_points) or len(set(self.entry_points)) != len(self.entry_points):
            raise ValueError('discovery signature/entry-point mismatch')
        if any(not path.startswith('/workspace/site/') or '..' in path.split('/') for path in self.module_files.values()):
            raise ValueError('discovery imported outside the exact installed source wheel')
        return self


class CommandRuntimeProbeObservation(StrictModel):
    language: Literal['node', 'rust', 'go']
    project_name: str
    project_version: str
    interpreter_version: str
    entry_point_files: Annotated[dict[str, str], Field(min_length=1, max_length=64)]
    entry_points: Annotated[tuple[str, ...], Field(min_length=1, max_length=64)]
    supported_observables: Annotated[tuple[str, ...], Field(min_length=1)]

    @model_validator(mode='after')
    def exact_built_entries(self):
        from feature_rl.environments.archive import safe_path
        from feature_rl.environments import SourceRejected
        if len(set(self.entry_points)) != len(self.entry_points):
            raise ValueError('duplicate built entry points')
        try:
            expected = {entry: '/workspace/site/'+safe_path(entry) for entry in self.entry_points}
        except SourceRejected as error:
            raise ValueError('invalid built entry point path') from error
        if self.entry_point_files != expected:
            raise ValueError('discovery entry points differ from the built installation')
        return self


class CommandRuntimeDiscoveryObservation(CommandRuntimeProbeObservation):
    version: Literal['command-runtime-discovery-v1'] = 'command-runtime-discovery-v1'
    profile_sha256: Annotated[str, Field(pattern=r'^[0-9a-f]{64}$')]
    baseline: ArtifactRef
    recipe: ArtifactRef
    build_evidence_sha256: Annotated[str, Field(pattern=r'^[0-9a-f]{64}$')]
    execution_evidence_sha256: Annotated[str, Field(pattern=r'^[0-9a-f]{64}$')]

    @model_validator(mode='after')
    def exact_execution_binding(self):
        if self.baseline.kind != 'source-archive' or self.baseline.visibility is not Visibility.AUTHORING:
            raise ValueError('discovery must bind the authoring baseline')
        if self.recipe.kind != 'EnvironmentRecipe' or self.recipe.visibility is not Visibility.AUTHORING:
            raise ValueError('discovery must bind the authoring recipe')
        return self


@dataclass(frozen=True)
class RuntimeDiscoveryResult:
    observation: RuntimeDiscoveryObservation | CommandRuntimeDiscoveryObservation
    context: GroundedSource
    private_evidence: tuple[ArtifactRef, ...]
    costs: tuple[CostRecord, ...]


def parse_discovery(ref, text):
    if ref.kind == 'runtime-discovery':
        return TypeAdapter(Annotated[RuntimeDiscoveryObservation | CommandRuntimeDiscoveryObservation,
            Field(discriminator='version')]).validate_json(text)
    raise ValueError('unsupported runtime discovery kind')


def discovery_locator(ref):
    if ref.kind not in {'runtime-discovery'}:
        raise ValueError('unsupported runtime discovery kind')
    return 'm3:'+ref.kind+'-v1'


class RuntimeDiscoveryService:
    def __init__(self, *, runtime):
        self.runtime = runtime

    def discover(self, prepared):
        import hashlib
        recipe = self.runtime.recipe(prepared)
        profile = self.runtime.profile
        handle = self.runtime.open_workspace(prepared, role='baseline')
        error = None
        build = execution = None
        try:
            build = self.runtime.build_snapshot(handle)
            from feature_rl.environments.command_profiles import CommandRuntimeProfile
            command_profile = isinstance(profile, CommandRuntimeProfile)
            settings = profile.model_dump(mode='json', include={'project_name', 'project_version', 'language',
                'import_modules', 'entry_points', 'supported_observables'})
            code = COMMAND_DISCOVERY_CODE if command_profile else DISCOVERY_CODE
            execution = self.runtime.execute(handle, ExecutionRequest(command=CommandSpec(
                argv=('/usr/local/bin/python', '-c', code), working_directory='/workspace', timeout_seconds=30.0),
                stdin=canonical_json(settings), save_source=False), build=build)
            if (execution.reason != 'completed' or execution.failure_category != 'none' or execution.exit_code != 0
                    or not execution.cleanup_verified or execution.oom_killed or len(execution.stdout) > 65536):
                raise ValueError('installed repository discovery failed')
            decoded = json.loads(execution.stdout)
            # Exact canonical bytes also reject duplicate keys and nonfinite values.
            if canonical_json(decoded) != execution.stdout:
                raise ValueError('discovery output is not canonical JSON')
            probe_model = CommandRuntimeProbeObservation if command_profile else RuntimeProbeObservation
            probe = probe_model.model_validate_json(execution.stdout)
            if (probe.project_name != profile.project_name or probe.project_version != profile.project_version
                    or probe.interpreter_version != profile.interpreter_version or probe.entry_points != profile.entry_points
                    or probe.supported_observables != profile.supported_observables):
                raise ValueError('installed runtime discovery differs from the pinned profile')
            if command_profile:
                if probe.language != profile.language or probe.entry_point_files != profile.entry_point_paths:
                    raise ValueError('discovery differs from the toolchain installation')
            else:
                if set(probe.module_files) != set(profile.import_modules):
                    raise ValueError('discovery modules differ from the pinned profile')
                for path in probe.module_files.values():
                    relative = path.removeprefix('/workspace/site/')
                    from feature_rl.environments.wheels import mapped_path
                    if not path.startswith('/workspace/site/') or not mapped_path(relative,profile.source_mappings):
                        raise ValueError('discovery module is outside the target wheel source mappings')
            observation_model = CommandRuntimeDiscoveryObservation if command_profile else RuntimeDiscoveryObservation
            observation = observation_model(**probe.model_dump(),
                profile_sha256=hashlib.sha256(canonical_json(profile.model_dump(mode='json'))).hexdigest(),
                baseline=recipe.baseline, recipe=prepared.recipe, build_evidence_sha256=build.evidence.sha256,
                execution_evidence_sha256=execution.evidence.sha256)
            ref = self.runtime.publish(observation.model_dump(mode='json'), 'runtime-discovery', Visibility.AUTHORING)
            context = GroundedSource(context_id='M3_RUNTIME_DISCOVERY', role='baseline', source=ref,
                locator=discovery_locator(ref), text=canonical_json(observation.model_dump(mode='json')).decode(),
                provenance_label='existing_obligation')
            return RuntimeDiscoveryResult(observation, context, (build.evidence, execution.evidence),
                                          (build.cost, execution.cost))
        except BaseException as caught:
            error = caught
            if build is not None:
                caught.private_evidence = (build.evidence,) + ((execution.evidence,) if execution is not None else ())
                caught.costs = (build.cost,) + ((execution.cost,) if execution is not None else ())
            raise
        finally:
            try:
                self.runtime.close(handle)
            except Exception as cleanup:
                if error is None:
                    raise
                error.cleanup_error = cleanup
