"""Repository-profile discovery via an installed B wheel in the real sandbox."""
from dataclasses import dataclass
import json
from typing import Annotated, Literal
from pydantic import Field, model_validator

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
 'module_files':{name:module.__file__ for name,module in modules.items()},
 'entry_points':settings['entry_points'], 'supported_observables':settings['supported_observables'],
 'signatures':signatures}
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


@dataclass(frozen=True)
class RuntimeDiscoveryResult:
    observation: RuntimeDiscoveryObservation
    context: GroundedSource
    private_evidence: tuple[ArtifactRef, ...]
    costs: tuple[CostRecord, ...]


def parse_discovery(ref, text):
    if ref.kind == 'runtime-discovery':
        return RuntimeDiscoveryObservation.model_validate_json(text)
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
        profile = self.runtime.profile
        recipe = self.runtime.recipe(prepared)
        handle = self.runtime.open_workspace(prepared, role='baseline')
        error = None
        build = execution = None
        try:
            build = self.runtime.build_snapshot(handle)
            settings = profile.model_dump(mode='json', include={'project_name', 'import_modules',
                'entry_points', 'supported_observables'})
            execution = self.runtime.execute(handle, ExecutionRequest(command=CommandSpec(
                argv=('python', '-c', DISCOVERY_CODE), working_directory='/workspace', timeout_seconds=30.0),
                stdin=canonical_json(settings), save_source=False), build=build)
            if (execution.reason != 'completed' or execution.failure_category != 'none' or execution.exit_code != 0
                    or not execution.cleanup_verified or execution.oom_killed or len(execution.stdout) > 65536):
                raise ValueError('installed repository discovery failed')
            decoded = json.loads(execution.stdout)
            # Exact canonical bytes also reject duplicate keys and nonfinite values.
            if canonical_json(decoded) != execution.stdout:
                raise ValueError('discovery output is not canonical JSON')
            probe = RuntimeProbeObservation.model_validate_json(execution.stdout)
            if (probe.project_name != profile.project_name or probe.project_version != profile.project_version
                    or probe.interpreter_version != profile.interpreter_version or probe.entry_points != profile.entry_points
                    or probe.supported_observables != profile.supported_observables
                    or set(probe.module_files) != set(profile.import_modules)):
                raise ValueError('installed runtime discovery differs from the pinned profile')
            for path in probe.module_files.values():
                relative = path.removeprefix('/workspace/site/')
                if not path.startswith('/workspace/site/') or not any(
                        relative == m.wheel or relative.startswith(m.wheel+'/') for m in profile.source_mappings):
                    raise ValueError('discovery module is outside the target wheel source mappings')
            observation = RuntimeDiscoveryObservation(**probe.model_dump(),
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
