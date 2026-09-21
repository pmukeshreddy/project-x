"""Offline source builds and fresh executions for frozen command toolchains."""
import hashlib
import json
import time

from typing import Annotated, Literal
from pydantic import Field
from feature_rl.artifacts import canonical_json
from feature_rl.contracts import AllowedChanges, ArtifactRef, CommandSpec, CostRecord, DependencyPin, StrictModel, Visibility
from .archive import SourceArchive, SourceFile
from .command_profiles import CommandRuntimeProfile
from .command_resolution import dependency_supply
from .models import (CommandBuildResult, CpuBudgetExceeded, DockerUnavailable, EvidencePublicationFailed,
                     ExecutionResult, PolicyRejected, SourceRejected)
from .products import BuildProduct, CAPTURE_PRODUCT, STAGE_PRODUCT


PREPARE_EXECUTION = CommandSpec(argv=('/usr/local/bin/python', '-I', '-c',
    "import pathlib,shutil;shutil.copytree('/opt/feature-rl/toolchain-cache','/workspace/toolchain-cache');"
    "pathlib.Path('/workspace/home').mkdir();pathlib.Path('/workspace/tmp').mkdir()"), working_directory='/workspace', timeout_seconds=30.0)


def command_image_context(store, pins, policy):
    files = {'toolchain-cache/' + name: entry for name, entry in dependency_supply(store, pins, policy).files.items()}
    files['profile.json'] = SourceFile(canonical_json(policy.profile.model_dump(mode='json')), False)
    files['Dockerfile'] = SourceFile(f'''FROM --platform={policy.platform} {policy.image}
USER 0:0
ENV SOURCE_DATE_EPOCH=946684800 PYTHONDONTWRITEBYTECODE=1 PYTHONHASHSEED=0 LANG=C.UTF-8 LC_ALL=C.UTF-8 TZ=UTC
COPY profile.json /opt/feature-rl/profile.json
COPY toolchain-cache/ /opt/feature-rl/toolchain-cache/
USER 65534:65534
WORKDIR /workspace
'''.encode(), False)
    # Empty dependency closures still provide a writable cache root at execution.
    files['toolchain-cache/.feature-rl-supply'] = SourceFile(policy.profile.dependency_sha256.encode(), False)
    data = SourceArchive(files).to_tar()
    if len(data) > policy.max_staging_bytes:
        raise PolicyRejected('toolchain runtime image context exceeds staging bound')
    return data


def validate_resolution(recipe, policy, store):
    profile = policy.profile
    ref = profile.resolution
    if (ref is None or ref.kind != 'repository-runtime-resolution' or ref.visibility != Visibility.AUTHORING
            or ref not in recipe.provenance.inputs):
        raise PolicyRejected('command runtime lost its frozen repository resolution')
    value = json.loads(store.get_bytes(ref, max_envelope_bytes=2*1024*1024, max_payload_bytes=1024*1024))
    observed = value.get('observed', {})
    declaration = value.get('inputs', {}).get('declaration', {})
    if (value.get('resolved_image') != policy.image or value.get('cleanup_verified') is not True
            or observed.get('interpreter_version') != profile.interpreter_version
            or observed.get('harness_interpreter_version') != profile.harness_interpreter_version
            or observed.get('system_packages') != [pin.model_dump(mode='json') for pin in profile.system_packages]
            or observed.get('sha256') != profile.dependency_sha256
            or declaration.get('manifest_hashes') != profile.manifest_hashes
            or declaration.get('build_commands') != [c.model_dump(mode='json') for c in profile.build_commands]
            or declaration.get('entry_points') != list(profile.entry_points)
            or declaration.get('services') != [service.model_dump(mode='json') for service in profile.services]
            or len(recipe.dependencies) != 1 or value.get('dependency') != recipe.dependencies[0].model_dump(mode='json')):
        raise PolicyRejected('command runtime resolution/profile/supply identity mismatch')
    dependency_supply(store, recipe.dependencies, policy)


class CommandCandidateResolution(StrictModel):
    version: Literal['command-candidate-resolution-v1'] = 'command-candidate-resolution-v1'
    identity: Annotated[str, Field(pattern=r'^[0-9a-f]{64}$')]
    source_tree_sha256: Annotated[str, Field(pattern=r'^[0-9a-f]{64}$')]
    profile: CommandRuntimeProfile
    dependencies: tuple[DependencyPin, ...]


def candidate_resolution(runtime, prepared, recipe, policy, source, rules):
    rules = AllowedChanges.model_validate(rules)
    profile = policy.profile
    profile.validate_allowed_changes(rules)
    try:
        profile.validate_source(source)
    except PolicyRejected as exc:
        raise SourceRejected(str(exc)) from exc
    dependency_supply(runtime.store, recipe.dependencies, policy)
    identity = hashlib.sha256(canonical_json({'recipe': prepared.recipe.model_dump(mode='json'),
        'policy': prepared.policy.model_dump(mode='json'), 'source_tree_sha256': source.tree_sha256,
        'rules': rules.model_dump(mode='json')})).hexdigest()
    return CommandCandidateResolution(identity=identity, source_tree_sha256=source.tree_sha256,
                                      profile=profile, dependencies=recipe.dependencies)


def capture_product(runtime, session):
    profile = runtime.profile
    command = CommandSpec(argv=('/usr/local/bin/python', '-I', '-c', CAPTURE_PRODUCT, str(runtime.policy.max_staging_bytes),
        str(runtime.policy.max_files)), working_directory='/workspace', timeout_seconds=30.0)
    result = session.execute(command, canonical_json({'language': profile.language, 'entry_points': profile.entry_points}),
                             output_limit=runtime.policy.max_staging_bytes, artifact_capture=True)
    if result.reason == 'monitor_failure':
        raise DockerUnavailable('build product capture monitor failed')
    if result.reason != 'exited' or result.exit_code != 0:
        raise SourceRejected('build product capture failed: ' + result.stderr.decode(errors='replace')[-1000:])
    product = BuildProduct.read(result.stdout, runtime.policy)
    for entry in profile.entry_points:
        if entry not in product.files or (profile.language in ('go', 'rust') and not product.files[entry].executable):
            raise SourceRejected('declared entry point is absent from the built product: ' + entry)
    return result.stdout, product


def _run_build(runtime, session, source):
    from .runtime import StageFailure
    runtime.stage(session, source)
    for index, command in enumerate(runtime.profile.setup):
        result = session.execute(command, environment=runtime.profile.environment)
        if result.reason != 'exited' or result.exit_code != 0:
            infrastructure = index == 0 or result.reason == 'monitor_failure'
            reason = 'infrastructure_failure' if result.reason == 'monitor_failure' else ('setup_failed' if index == 0 else ('command_failed' if result.reason == 'exited' else result.reason))
            raise StageFailure('offline toolchain ' + ('setup' if index == 0 else 'build') + ' failed: '
                + result.stderr.decode(errors='replace')[-1000:], reason, 'infrastructure' if infrastructure else 'candidate')
        if index == 0:
            from .services import start_services
            start_services(runtime, session, session.record.binding['runtime_image'])
    return capture_product(runtime, session)


def validate_command_baseline(runtime, image, baseline, source):
    session = runtime.engine.session(binding={'purpose': 'runtime-image-construction',
        'context': image.context_sha256, 'tree': source.tree_sha256}, saved_source={}, image=image.image_digest)
    error = None
    try:
        with session:
            data, product = _run_build(runtime, session, source)
    except BaseException as exc:
        error = exc
        raise
    finally:
        evidence = runtime.evidence(session, 'runtime-image-construction', {
            'image_digest': image.image_digest, 'error': repr(error) if error else None})
    return runtime.publish({'baseline': baseline.model_dump(mode='json'), 'image_digest': image.image_digest,
        'context_sha256': image.context_sha256, 'source_tree_sha256': source.tree_sha256,
        'product_sha256': hashlib.sha256(data).hexdigest(), 'product_tree_sha256': product.tree_sha256,
        'cleanup_verified': session.cleanup_verified, 'execution_sha256': evidence.sha256},
        'runtime-image-construction-summary', Visibility.AUTHORING)


def _cost(start, session, category):
    return CostRecord(category=category, wall_seconds=time.monotonic()-start, cpu_seconds=session.maximum_cpu_seconds,
        gpu_seconds=None, input_tokens=None, output_tokens=None, human_minutes=None, usd=None, measurement='partial',
        note='Measured isolated toolchain operation including staging, capture and verified cleanup')


def build_snapshot(runtime, workspace):
    from .runtime import BuildFailed, failure
    start = time.monotonic()
    value, prepared, recipe, saved, source = workspace
    rules = AllowedChanges.model_validate_json(canonical_json(value['allowed_changes']))
    resolution_ref, resolution = runtime.candidate_dependencies(prepared, source, rules)
    binding = {**runtime.binding(prepared, saved, 'build', value), 'dependency_resolution': resolution_ref.sha256}
    session = runtime.engine.session(binding=binding, saved_source=saved.model_dump(mode='json'), image=recipe.image_digest)
    error = data = product = None
    try:
        with session:
            data, product = _run_build(runtime, session, source)
    except BaseException as exc:
        error = exc
    reason, category = failure(error) if error else ('completed', 'none')
    extra = {'dependency_resolution': resolution_ref.model_dump(mode='json'), 'error': repr(error) if error else None,
        'reason': reason, 'failure_category': category, 'source_tree_sha256': source.tree_sha256,
        'product_sha256': hashlib.sha256(data).hexdigest() if data is not None else None,
        'product_tree_sha256': product.tree_sha256 if product is not None else None,
        'saved_source': saved.model_dump(mode='json')}
    evidence = runtime.evidence(session, 'build', extra)
    if error:
        if not isinstance(error, Exception):
            raise error
        raise BuildFailed(str(error), evidence, saved, reason=reason, failure_category=category,
                          cleanup_verified=session.cleanup_verified) from error
    try:
        ref = runtime.store.put_bytes(data, 'snapshot-build-product', Visibility.PRIVATE)
    except Exception as exc:
        pending = EvidencePublicationFailed('built product publication failed after verified cleanup',
            payload=data, kind='snapshot-build-product', visibility=Visibility.PRIVATE)
        pending.cleanup_verified = session.cleanup_verified
        pending.saved_source = saved.model_dump(mode='json')
        pending.build_evidence = evidence
        raise pending from exc
    return CommandBuildResult(source=saved.artifact, recipe=prepared.recipe, policy=prepared.policy,
        dependency_resolution=resolution_ref, product=ref, product_sha256=extra['product_sha256'],
        product_tree_sha256=product.tree_sha256, source_tree_sha256=source.tree_sha256,
        evidence=evidence, cost=_cost(start, session, 'construction'))


def execute(runtime, request, workspace, *, build, development):
    from .runtime import StageFailure, failure
    value, prepared, recipe, saved, source = workspace
    rules = AllowedChanges.model_validate_json(canonical_json(value['allowed_changes']))
    if development:
        resolution_ref, resolution = runtime.candidate_dependencies(prepared, source, rules)
    else:
        build = CommandBuildResult.model_validate(build)
        resolution_ref = build.dependency_resolution
        resolution = runtime.read_candidate_dependencies(resolution_ref, prepared, source, rules)
        if (build.source, build.recipe, build.policy, build.source_tree_sha256) != (
                saved.artifact, prepared.recipe, prepared.policy, source.tree_sha256):
            raise PolicyRejected('command build/source/recipe/policy mismatch')
        if (build.product.kind != 'snapshot-build-product' or build.product.visibility != Visibility.PRIVATE
                or build.evidence.kind != 'environment-execution'):
            raise PolicyRejected('command build artifact kind/visibility mismatch')
        data = runtime.read_bytes(build.product, runtime.policy.max_staging_bytes)
        product = BuildProduct.read(data, runtime.policy)
        if hashlib.sha256(data).hexdigest() != build.product_sha256 or product.tree_sha256 != build.product_tree_sha256:
            raise SourceRejected('command build product raw/tree hash mismatch')
        receipt = json.loads(runtime.read_bytes(build.evidence, 32*1024*1024))
        binding = receipt.get('record', {}).get('binding', {})
        extra = receipt.get('extra', {})
        if (receipt.get('phase') != 'build' or receipt.get('cleanup_verified') is not True
                or extra.get('reason') != 'completed' or extra.get('error') is not None
                or extra.get('product_sha256') != build.product_sha256
                or extra.get('product_tree_sha256') != build.product_tree_sha256
                or extra.get('source_tree_sha256') != source.tree_sha256
                or extra.get('dependency_resolution') != resolution_ref.model_dump(mode='json')
                or any(binding.get(key) != val for key, val in {'recipe': prepared.recipe.sha256,
                    'policy': prepared.policy.sha256, 'source': saved.artifact.sha256,
                    'dependency_resolution': resolution_ref.sha256}.items())):
            raise PolicyRejected('successful command build receipt binding mismatch')
    if len(request.stdin) > runtime.policy.stdin_bytes:
        raise PolicyRejected('command stdin cap')
    phase = 'development' if development else 'execute'
    binding = {**runtime.binding(prepared, saved, phase, value), 'dependency_resolution': resolution_ref.sha256}
    session = runtime.engine.session(binding=binding, saved_source=saved.model_dump(mode='json'),
        cpu_seconds=request.remaining_cpu_seconds, image=recipe.image_digest)
    start = time.monotonic()
    result = error = None
    next_saved, save_status = saved, 'last_confirmed'
    try:
        with session:
            runtime.stage(session, source)
            if development:
                setup = (runtime.profile.setup[0],)
            else:
                captured = session.execute(CommandSpec(argv=('/usr/local/bin/python', '-I', '-c', STAGE_PRODUCT,
                    str(runtime.policy.max_staging_bytes)), working_directory='/workspace', timeout_seconds=30.0),
                    data, staging=True)
                if captured.reason != 'exited' or captured.exit_code != 0:
                    raise StageFailure('build product staging failed', 'setup_failed', 'infrastructure')
                setup = (PREPARE_EXECUTION,)
            for command in setup:
                observed = session.execute(command, environment=runtime.profile.environment)
                if observed.reason == 'cpu_limit':
                    raise CpuBudgetExceeded('CPU budget exhausted during toolchain execution setup')
                if observed.reason != 'exited' or observed.exit_code != 0:
                    raise StageFailure('toolchain execution setup failed', 'setup_failed', 'infrastructure')
            from .services import start_services
            start_services(runtime, session, recipe.image_digest)
            result = session.execute(request.command, request.stdin, environment=runtime.profile.environment)
            if result.reason == 'exited' and request.save_source:
                captured = session.export_source().without_pytest_cache(source)
                captured.validate_changes(source, rules.source_roots, rules.forbidden_paths)
                if captured.tree_sha256 == source.tree_sha256:
                    save_status = 'unchanged'
                else:
                    next_saved = runtime.saved(captured, saved.version+1)
                    value['saved'] = next_saved.model_dump(mode='json')
                    value['generation'] += 1
                    runtime.engine.state.write('workspace-'+value['workspace_id']+'.json', value)
                    save_status = 'saved'
    except BaseException as exc:
        error = exc
    if error:
        reason, category = failure(error)
    elif result.reason == 'monitor_failure':
        reason, category = 'infrastructure_failure', 'infrastructure'
    elif result.reason == 'exited':
        reason, category = ('command_failed', 'candidate') if result.exit_code else ('completed', 'none')
    else:
        reason, category = result.reason, 'candidate'
    evidence = runtime.evidence(session, phase, {'dependency_resolution': resolution_ref.model_dump(mode='json'),
        'error': repr(error) if error else None, 'reason': reason, 'failure_category': category,
        'saved_source': next_saved.model_dump(mode='json'), 'save_status': save_status,
        'build_evidence': build.evidence.model_dump(mode='json') if build else None,
        'import_policy': 'isolated source and frozen dependency cache' if development else 'fresh exact-source retained build product'})
    if error and not isinstance(error, Exception):
        raise error
    return ExecutionResult(operation_id=session.record.operation_id, reason=reason, failure_category=category,
        exit_code=result.exit_code if result else None, stdout=result.stdout if result else b'',
        stderr=result.stderr if result else b'', container_exit_code=session.container_state.get('ExitCode'),
        oom_killed=bool(session.container_state.get('OOMKilled')) or session.memory_oom_events > 0,
        maximum_memory_bytes=session.maximum_memory_bytes, cleanup_verified=session.cleanup_verified,
        saved_source=next_saved, save_status=save_status, evidence=evidence, cost=_cost(start, session, 'execution'))
