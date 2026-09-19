"""Positive solver projection from frozen inputs. No source is executed/extracted."""
from dataclasses import dataclass
import hashlib
import io
import json
import tarfile

from feature_rl.artifacts import ArtifactStore, canonical_json
from feature_rl import contracts as c
from feature_rl.environments import SourceArchive, SourceFile, SandboxPolicy
from feature_rl.environments.archive import safe_path
from feature_rl.environments.runtime import WHEELS, DEPS, BUILD, INSTALL, ENV, DEV_ENV, REPAIR, EnvironmentRuntime
from .models import BuildInputs, BuildRejected, InventoryEntry, SolverInventory

MAX_DOCUMENT = 1024 * 1024
MAX_PUBLIC_CHECK = 256 * 1024


def document(value):
    return value.model_dump(mode='json')


def checked(model, value):
    return model.model_validate_json(canonical_json(document(value)))


def read_bytes(store, ref, cap, *, kind=None, public=False):
    if ref.encoding != 'bytes' or (kind is not None and ref.kind != kind) or (public and ref.visibility != c.Visibility.PUBLIC):
        raise BuildRejected('unexpected byte artifact kind/encoding/visibility')
    return store.get_bytes(ref, max_envelope_bytes=4 * ((cap + 2) // 3) + 4096, max_payload_bytes=cap)


def read_record(store, ref, model, kind, *, public=False):
    raw = read_bytes(store, ref, MAX_DOCUMENT, kind=kind, public=public)
    value = model.model_validate_json(raw)
    if canonical_json(document(value)) != raw:
        raise BuildRejected('noncanonical or duplicate-key record')
    return value


def typed(store, ref, model):
    value = store.get_artifact(ref, max_envelope_bytes=MAX_DOCUMENT)
    if type(value) is not model:
        raise BuildRejected('expected exact ' + model.__name__ + ' reference')
    return value


@dataclass(frozen=True)
class Resolved:
    pair: c.SourcePair
    candidate: c.CandidateRecord
    contract: c.RequirementContract
    plan: c.ScenarioPlan
    verifier: c.VerifierBundle
    recipe: c.EnvironmentRecipe
    policy: SandboxPolicy
    source: SourceArchive
    dependencies: tuple[c.ArtifactRef, ...]


def source_path(name):
    if name != safe_path(name):
        raise BuildRejected('noncanonical package path')
    parts = name.casefold().split('/')
    forbidden = {'.git', '.hg', '.svn', '__pycache__', '.pytest_cache', '.mypy_cache', '.ruff_cache',
                 '.tox', '.venv', 'venv', 'node_modules', 'build', 'dist', '.ds_store',
                 'reference', 'controller_checks', 'authoring_sessions'}
    if any(part in forbidden for part in parts) or name.casefold().endswith(('.pyc', '.pyo', '.whl', '.egg', '.so', '.dylib', '.dll')):
        raise BuildRejected('history, private material, cache or build product in source allowlist')
    return name


def resolve(store, inputs):
    inputs = checked(BuildInputs, inputs)
    pair = typed(store, inputs.source_pair, c.SourcePair)
    candidate = typed(store, pair.candidate, c.CandidateRecord)
    contract = typed(store, inputs.contract, c.RequirementContract)
    plan = typed(store, inputs.scenario_plan, c.ScenarioPlan)
    verifier = typed(store, inputs.verifier, c.VerifierBundle)
    recipe = typed(store, inputs.environment.recipe, c.EnvironmentRecipe)
    policy = read_record(store, inputs.environment.policy, SandboxPolicy, 'sandbox-policy')
    if (candidate.partition == c.Partition.UNASSIGNED or candidate.screening.disposition != c.Disposition.SUCCESS
            or candidate.license.status != 'verified' or candidate.license.license_text is None):
        raise BuildRejected('candidate partition, screening or license prerequisite is unresolved')
    if not (pair.provenance_label == candidate.provenance_label == contract.provenance_label) or pair.relationship != candidate.commits:
        raise BuildRejected('source provenance/commit relationship mismatch')
    if (pair.baseline != recipe.baseline or pair.baseline == pair.reference
            or pair.baseline.visibility not in (c.Visibility.AUTHORING, c.Visibility.PUBLIC)
            or recipe.visibility != c.Visibility.AUTHORING or inputs.environment.policy.visibility != c.Visibility.AUTHORING):
        raise BuildRejected('B-only source/recipe/visibility mismatch')
    if (plan.contract != inputs.contract or verifier.contract != inputs.contract
            or verifier.scenario_plan != inputs.scenario_plan):
        raise BuildRejected('contract/scenario/verifier identity mismatch')
    requirements = contract.requirements + contract.compatibility_obligations
    ids = {r.requirement_id for r in requirements}
    mandatory = {r.requirement_id for r in requirements if r.mandatory}
    if (set(plan.mandatory_requirement_ids) != mandatory
            or any(not set(s.requirement_ids) <= ids for s in plan.scenarios)
            or any(not set(case.requirement_ids) <= ids for case in verifier.cases)
            or not mandatory <= {r for case in verifier.cases if case.mandatory for r in case.requirement_ids}):
        raise BuildRejected('requirement coverage/identity mismatch')
    if any(a.disposition == 'unresolved' or (a.disposition == 'clarified' and a.resolution is None) for a in contract.ambiguities):
        raise BuildRejected('unresolved contract ambiguity')
    allowed = contract.allowed_changes
    if (allowed != verifier.permissions.submission_policy or allowed.dependencies != 'forbidden'
            or allowed.dependency_artifacts or allowed.additional_artifact_types
            or verifier.permissions.controller_role != c.ActorRole.CONTROLLER):
        raise BuildRejected('unsupported submission/controller policy')
    for path in (*allowed.source_roots, *allowed.forbidden_paths):
        source_path(path)
    if any(path != 'src' and not path.startswith('src/') for path in allowed.source_roots):
        raise BuildRejected('supported change roots must be beneath src')
    if (inputs.environment.policy not in recipe.provenance.inputs or recipe.image_digest != policy.image
            or policy.image != REPAIR['image_manifest'] or recipe.interpreter_version != '3.12.14'
            or recipe.services or recipe.network_policy != 'none'
            or recipe.setup != (DEPS, BUILD, INSTALL) or recipe.reset != recipe.setup
            or tuple((item.name, item.value) for item in recipe.environment) != ENV
            or recipe.locale != 'C.UTF-8' or recipe.timezone != 'UTC'
            or recipe.randomness != c.SeedPolicy(algorithm='PYTHONHASHSEED', seeds=(0,), same_cases_within_group=True)):
        raise BuildRejected('unsupported pinned M3 runtime recipe')
    if len(recipe.neutral_repairs) != 1 or read_bytes(store, recipe.neutral_repairs[0].patch, 16384, kind='neutral-environment-repair') != canonical_json(REPAIR):
        raise BuildRejected('unapproved neutral image repair')
    for field in ('cpu_seconds', 'memory_bytes', 'pids', 'disk_bytes', 'output_bytes'):
        if getattr(recipe.limits, field) != getattr(policy, field):
            raise BuildRejected('runtime resource policy drift')
    if recipe.limits.wall_seconds != policy.lifecycle_seconds:
        raise BuildRejected('runtime wall policy drift')
    if len(recipe.dependencies) != len(WHEELS) or {pin.name for pin in recipe.dependencies} != set(WHEELS):
        raise BuildRejected('exact approved dependency pins required')
    total = 0
    for pin in recipe.dependencies:
        version, _, digest = WHEELS[pin.name]
        data = read_bytes(store, pin.artifact, 2 * 1024 * 1024, kind='dependency-wheel')
        total += len(data)
        if pin.version != version or pin.sha256 != digest or hashlib.sha256(data).hexdigest() != digest or total > policy.max_staging_bytes:
            raise BuildRejected('dependency raw bytes/pin mismatch')
    raw = read_bytes(store, pair.baseline, policy.max_archive_bytes, kind='source-archive')
    source = SourceArchive.read(raw, policy)
    # M3 bounds and rejects links; additionally reject ambiguous regular-file names
    # before normalization, and reject case variants of excluded package material.
    with tarfile.open(fileobj=io.BytesIO(raw), mode='r:') as archive:
        for member in archive:
            if member.isfile():
                source_path(member.name)
    allowlist = inputs.baseline_files
    if len(set(allowlist)) != len(allowlist) or set(allowlist) != set(source.files):
        raise BuildRejected('explicit allowlist must cover exactly the complete B file inventory')
    for name in allowlist:
        source_path(name)
    if len({name.casefold() for name in allowlist}) != len(allowlist):
        raise BuildRejected('case-colliding source paths')
    EnvironmentRuntime.validate_click_manifest(source)
    read_bytes(store, pair.reference, policy.max_archive_bytes, kind='source-archive')
    dependencies = tuple(dict.fromkeys((inputs.source_pair, pair.candidate, inputs.contract, inputs.scenario_plan,
        inputs.verifier, inputs.environment.recipe, inputs.environment.policy, pair.baseline, pair.reference)))
    return Resolved(pair, candidate, contract, plan, verifier, recipe, policy, source, dependencies)


def public_values(store, resolved):
    contract, recipe = resolved.contract, resolved.recipe
    instruction = ['# Feature task', '', contract.visible_request, '', '## Capability', contract.capability,
                   '', '## Entry points', *('- ' + entry for entry in contract.entry_points), '', '## Requirements']
    for item in contract.requirements + contract.compatibility_obligations:
        instruction.append(f'- {item.requirement_id} ({"mandatory" if item.mandatory else "optional"}): {item.statement}; observable: {item.observable}')
    instruction.extend(['', '## Clarifications'])
    instruction.extend(f'- {item.question}: {item.resolution or "excluded"} ({item.disposition})' for item in contract.ambiguities)
    instruction.extend(['', '## Allowed changes', canonical_json(document(contract.allowed_changes)).decode(),
                        '', '## Episode limits', canonical_json(document(contract.episode_limits)).decode(), ''])
    runtime = {'version': 'm6-solver-runtime-v1', 'image_digest': recipe.image_digest,
        'interpreter_version': recipe.interpreter_version,
        'dependencies': [{'name': p.name, 'version': p.version, 'sha256': p.sha256} for p in recipe.dependencies],
        'setup': [document(command) for command in recipe.setup], 'reset': [document(command) for command in recipe.reset],
        'environment': [document(item) for item in recipe.environment],
        'development_environment': [{'name': key, 'value': value} for key, value in DEV_ENV],
        'limits': document(recipe.limits), 'locale': recipe.locale, 'timezone': recipe.timezone,
        'randomness': document(recipe.randomness), 'network_policy': recipe.network_policy}
    checks = tuple(dict.fromkeys((*contract.public_checks, *resolved.verifier.public_examples)))
    if len(checks) > 64:
        raise BuildRejected('public-check count limit')
    check_data = []
    for ref in checks:
        if ref.kind not in ('public-check', 'public-example'):
            raise BuildRejected('unsupported declared public-check kind')
        data = read_bytes(store, ref, MAX_PUBLIC_CHECK, public=True)
        data.decode('utf-8')
        check_data.append(data)
    return '\n'.join(instruction).encode(), canonical_json(runtime), checks, tuple(check_data)


def package_files(source, instruction, runtime, checks):
    files = {'instruction.md': SourceFile(instruction, False), 'runtime_manifest.json': SourceFile(runtime, False)}
    files.update(('workspace/' + name, value) for name, value in sorted(source.files.items()))
    files.update(('public_checks/' + str(index).zfill(4), SourceFile(data, False)) for index, data in enumerate(checks))
    return files


def inventory_entries(files):
    return tuple(InventoryEntry(path=name, sha256=hashlib.sha256(value.data).hexdigest(), size=len(value.data), executable=value.executable)
                 for name, value in sorted(files.items()))


def assemble(store, registry, inputs):
    resolved = resolve(store, inputs)
    for ref in resolved.dependencies:
        registry.register(ref)
        registry.assert_usable(ref)
    instruction, runtime, checks, check_data = public_values(store, resolved)
    files = package_files(resolved.source, instruction, runtime, check_data)
    if len(files) > 2100 or sum(len(entry.data) for entry in files.values()) > resolved.policy.max_staging_bytes:
        raise BuildRejected('aggregate solver package bounds')
    package = SourceArchive(files).to_tar()
    if len(package) > resolved.policy.max_archive_bytes:
        raise BuildRejected('solver package archive byte limit')
    # Only these explicitly constructed payloads receive PUBLIC visibility.
    instruction_ref = store.put_bytes(instruction, 'm6-instruction', c.Visibility.PUBLIC)
    workspace_ref = store.put_bytes(resolved.source.to_tar(), 'source-archive', c.Visibility.PUBLIC)
    runtime_ref = store.put_bytes(runtime, 'm6-runtime-manifest', c.Visibility.PUBLIC)
    package_ref = store.put_bytes(package, 'm6-solver-package', c.Visibility.PUBLIC)
    inventory = SolverInventory(archive=package_ref, archive_sha256=hashlib.sha256(package).hexdigest(),
        components=(instruction_ref, workspace_ref, runtime_ref, *checks), files=inventory_entries(files))
    inventory_ref = store.put_bytes(canonical_json(document(inventory)), 'm6-solver-inventory', c.Visibility.PUBLIC)
    view = c.SolverView(instruction=instruction_ref, workspace=workspace_ref, runtime_manifest=runtime_ref,
                        public_checks=checks, inventory=inventory_ref)
    return view, package_ref, resolved.dependencies


def inspect_package(store, inputs, view, package_ref):
    resolved = resolve(store, inputs)
    instruction, runtime, checks, check_data = public_values(store, resolved)
    if (read_bytes(store, view.instruction, MAX_DOCUMENT, kind='m6-instruction', public=True) != instruction
            or read_bytes(store, view.runtime_manifest, MAX_DOCUMENT, kind='m6-runtime-manifest', public=True) != runtime
            or view.public_checks != checks
            or read_bytes(store, view.workspace, resolved.policy.max_archive_bytes, kind='source-archive', public=True) != resolved.source.to_tar()):
        raise BuildRejected('solver components differ from frozen inputs')
    inventory = read_record(store, view.inventory, SolverInventory, 'm6-solver-inventory', public=True)
    package = read_bytes(store, package_ref, resolved.policy.max_archive_bytes, kind='m6-solver-package', public=True)
    expected = package_files(resolved.source, instruction, runtime, check_data)
    if (inventory.archive != package_ref or inventory.archive_sha256 != hashlib.sha256(package).hexdigest()
            or inventory.components != (view.instruction, view.workspace, view.runtime_manifest, *view.public_checks)
            or inventory.files != inventory_entries(expected) or package != SourceArchive(expected).to_tar()):
        raise BuildRejected('solver inventory/package bytes mismatch')
    return package
