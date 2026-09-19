"""Trusted packaging diagnostics; no historical code, model, approval or reward.

The six wheel pins are replaced only in this test module with inert diagnostic
bytes. These packages cannot serve as real M3 runtime or feature evidence.
"""
from datetime import datetime, timezone
import hashlib
import importlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile

import pytest

from feature_rl.artifacts import ArtifactStore, canonical_json
from feature_rl import contracts as c
from feature_rl.environments import SourceArchive, SourceFile, SandboxPolicy, PreparedEnvironment
from feature_rl.environments.runtime import WHEELS, DEPS, BUILD, INSTALL, ENV, REPAIR
from feature_rl.registry import Registry
from test_contracts_examples import examples


def api():
    assert importlib.util.find_spec('feature_rl.pipeline') is not None, 'M6 task builder is missing'
    return importlib.import_module('feature_rl.pipeline')


def diagnostic_pins():
    return {name: (version, filename, hashlib.sha256(('DIAGNOSTIC wheel ' + name).encode()).hexdigest())
            for name, (version, filename, _) in WHEELS.items()}


def fixture(tmp_path, monkeypatch, *, archive=None, files=None):
    m = api()
    import feature_rl.pipeline.packaging as packaging
    monkeypatch.setattr(packaging, 'WHEELS', diagnostic_pins())
    store = ArtifactStore(tmp_path.resolve() / 'objects', c.ActorRole.CONTROLLER)
    reg = Registry(tmp_path.resolve() / 'registry', store)
    policy = SandboxPolicy()
    baseline_files = {'src/click/__init__.py': SourceFile(b'# DIAGNOSTIC baseline, no feature\n', False),
                      'pyproject.toml': SourceFile(b'[project]\nname="click"\nversion="8.3.3"\n[build-system]\nrequires=["flit_core>=3.11,<4"]\nbuild-backend="flit_core.buildapi"\n', False)}
    baseline = store.put_bytes(archive or SourceArchive(baseline_files).to_tar(), 'source-archive', c.Visibility.AUTHORING)
    reference = store.put_bytes(SourceArchive({'src/click/__init__.py': SourceFile(b'# PRIVATE_H_SENTINEL_DIAGNOSTIC\n', False),
        'docs/change.rst': SourceFile(b'private diagnostic H documentation', False)}).to_tar(), 'source-archive', c.Visibility.PRIVATE)
    private = store.put_bytes(b'PRIVATE_ORACLE_PROVENANCE_SENTINEL', 'diagnostic-private', c.Visibility.PRIVATE)
    public_check = store.put_bytes(b'# DIAGNOSTIC public check; not run\n', 'public-check', c.Visibility.PUBLIC)
    data = examples()
    published = {}

    def replace(value):
        if isinstance(value, dict):
            if {'sha256', 'kind', 'encoding', 'visibility', 'schema_version'} <= value.keys():
                if value['sha256'] != 'a' * 64:
                    # Explicit references below have actual CAS identities.
                    try:
                        ref = c.ArtifactRef.model_validate_json(json.dumps(value))
                        if (store.root / (ref.sha256 + '.json')).exists():
                            return value
                    except ValueError:
                        pass
                if value['encoding'] == 'json':
                    return published[value['kind']].model_dump(mode='json')
                return private.model_dump(mode='json')
            return {key: replace(item) for key, item in value.items()}
        if isinstance(value, list):
            return [replace(item) for item in value]
        return value

    data['CandidateRecord']['screening']['disposition'] = 'success'
    data['CandidateRecord']['license']['status'] = 'verified'
    data['CandidateRecord']['license']['license_text'] = private.model_dump(mode='json')
    data['SourcePair'].update(baseline=baseline.model_dump(mode='json'), reference=reference.model_dump(mode='json'))
    data['RequirementContract'].update(provenance_label='reconstructed_specification',
        visible_request='DIAGNOSTIC ONLY packaging request', capability='DIAGNOSTIC frozen package',
        public_checks=[public_check.model_dump(mode='json')])
    data['RequirementContract']['allowed_changes'].update(source_roots=['src'], forbidden_paths=[])
    for kind in ('CandidateRecord', 'SourcePair', 'RequirementContract', 'ScenarioPlan', 'VerifierBundle'):
        if kind == 'VerifierBundle':
            data[kind]['permissions']['submission_policy'] = data['RequirementContract']['allowed_changes']
            data[kind]['worker_adapter']['code'] = private.model_dump(mode='json')
            data[kind]['permissions']['worker_inputs'] = [private.model_dump(mode='json')]
        published[kind] = store.put_artifact(c.ARTIFACT_TYPES[kind].model_validate_json(json.dumps(replace(data[kind]))))

    policy_ref = store.put_bytes(canonical_json(policy.model_dump(mode='json')), 'sandbox-policy', c.Visibility.AUTHORING)
    repair = store.put_bytes(canonical_json(REPAIR), 'neutral-environment-repair', c.Visibility.AUTHORING)
    evidence = c.EvidenceRecord(producer='DIAGNOSTIC fixture only', command=('inert',),
        recorded_at=datetime(2026, 9, 19, tzinfo=timezone.utc), exit_status=0, artifacts=(repair,),
        revision='a' * 40, scope='unit_diagnostic')
    pins = tuple(c.DependencyPin(name=name, version=value[0], sha256=value[2],
        artifact=store.put_bytes(('DIAGNOSTIC wheel ' + name).encode(), 'dependency-wheel', c.Visibility.AUTHORING))
        for name, value in diagnostic_pins().items())
    cost = store.get_artifact(published['RequirementContract']).costs[0]
    limits = c.ResourceLimits(wall_seconds=policy.lifecycle_seconds, cpu_seconds=policy.cpu_seconds,
        memory_bytes=policy.memory_bytes, pids=policy.pids, output_bytes=policy.output_bytes,
        disk_bytes=policy.disk_bytes, tool_calls=100, input_tokens=1, output_tokens=1)
    recipe = c.EnvironmentRecipe(kind='EnvironmentRecipe', schema_version=1, visibility=c.Visibility.AUTHORING,
        provenance=c.Provenance(producer='DIAGNOSTIC M3 recipe shape', producer_version='1',
            created_at=evidence.recorded_at, inputs=(baseline, policy_ref, *(p.artifact for p in pins)), evidence=(evidence,)),
        costs=(cost,), image_digest=policy.image, interpreter_version='3.12.14', dependencies=pins,
        setup=(DEPS, BUILD, INSTALL), reset=(DEPS, BUILD, INSTALL), services=(), limits=limits,
        neutral_repairs=(c.NeutralRepair(description='DIAGNOSTIC shape only', patch=repair, neutrality_evidence=(evidence,)),),
        locale='C.UTF-8', timezone='UTC', environment=tuple(c.EnvironmentVariable(name=k, value=v) for k, v in ENV),
        randomness=c.SeedPolicy(algorithm='PYTHONHASHSEED', seeds=(0,), same_cases_within_group=True),
        network_policy='none', baseline=baseline)
    prepared = PreparedEnvironment(recipe=store.put_artifact(recipe), policy=policy_ref)
    inputs = m.BuildInputs(source_pair=published['SourcePair'], contract=published['RequirementContract'],
        scenario_plan=published['ScenarioPlan'], verifier=published['VerifierBundle'], environment=prepared,
        baseline_files=tuple(sorted(files if files is not None else baseline_files)), invocation='diagnostic-build')
    builder = m.TaskBuilder(store=store, registry=reg, revision='a' * 40)
    return m, store, reg, builder, inputs, baseline, reference, private


def mutate(store, ref, **changes):
    old = store.get_artifact(ref)
    data = old.model_dump(mode='json') | {k: v.model_dump(mode='json') if hasattr(v, 'model_dump') else v for k, v in changes.items()}
    return store.put_artifact(type(old).model_validate_json(json.dumps(data)))


def test_build_freezes_complete_safe_package_and_idempotent_root(tmp_path, monkeypatch):
    m, store, reg, builder, inputs, baseline, reference, private = fixture(tmp_path, monkeypatch)
    result = builder.build(inputs, owner='diagnostic', claim_key='build-1')
    assert result.operation == 'construct' and result.disposition == c.Disposition.SUCCESS
    task_ref, = result.artifacts
    task = store.get_artifact(task_ref)
    assert task.state == c.TaskState.BUILT and task.qualification is None and task.baseline == baseline
    package = builder.solver_package(task_ref)
    files = SourceArchive.read(package, SandboxPolicy()).files
    assert set(files) == {'instruction.md', 'runtime_manifest.json', 'workspace/src/click/__init__.py',
                         'workspace/pyproject.toml', 'public_checks/0000'}
    assert files['workspace/src/click/__init__.py'].data == b'# DIAGNOSTIC baseline, no feature\n'
    solver = ArtifactStore(store.root, c.ActorRole.SOLVER)
    for ref in (task.solver_view.instruction, task.solver_view.workspace, task.solver_view.runtime_manifest, task.solver_view.inventory, *task.solver_view.public_checks):
        data = solver.get_bytes(ref)
        for forbidden in (reference.sha256.encode(), private.sha256.encode(), b'PRIVATE_H_SENTINEL', b'PRIVATE_ORACLE_PROVENANCE', b'private_oracle', b'author_provenance'):
            assert forbidden not in data
    before = reg.events()
    assert builder.build(inputs, owner='diagnostic', claim_key='ignored-completed-replay') == result
    assert reg.events() == before
    reg.quarantine(reference, notice_id='late-H-defect', reason='DIAGNOSTIC known lineage defect', evidence=(private,))
    assert task_ref in reg.trace(reference).artifacts
    with pytest.raises(m.BuildRejected):
        builder.solver_package(task_ref)


@pytest.mark.parametrize('drift', ['missing', 'baseline', 'policy', 'scenario', 'public-check'])
def test_prerequisite_failure_is_typed_and_never_builds_a_root(tmp_path, monkeypatch, drift):
    m, store, reg, builder, inputs, baseline, reference, private = fixture(tmp_path, monkeypatch)
    if drift == 'missing':
        (store.root / (inputs.contract.sha256 + '.json')).unlink()
    elif drift == 'baseline':
        changed = mutate(store, inputs.environment.recipe, baseline=store.put_bytes(b'other B', 'source-archive', c.Visibility.AUTHORING))
        inputs = inputs.model_copy(update={'environment': PreparedEnvironment(recipe=changed, policy=inputs.environment.policy)})
    elif drift == 'policy':
        changed = mutate(store, inputs.environment.recipe, setup=[{'argv': ['sh', '-c', 'unsafe'], 'working_directory': '/workspace', 'timeout_seconds': 2.0}])
        inputs = inputs.model_copy(update={'environment': PreparedEnvironment(recipe=changed, policy=inputs.environment.policy)})
    elif drift == 'scenario':
        inputs = inputs.model_copy(update={'scenario_plan': mutate(store, inputs.scenario_plan, contract=inputs.environment.recipe.model_dump(mode='json') | {'kind': 'RequirementContract'})})
    else:
        inputs = inputs.model_copy(update={'contract': mutate(store, inputs.contract, public_checks=[private.model_dump(mode='json')])})
    result = builder.build(inputs, owner='diagnostic', claim_key='bad-1')
    assert result.disposition != c.Disposition.SUCCESS
    assert all(ref.kind != 'TaskBundle' for ref in result.artifacts)
    if drift == 'missing':
        assert result.disposition == c.Disposition.BLOCKED
    completed = next(event for event in reg.events() if event.action == 'complete')
    record = reg.job(completed.data['claim']['job_id'])
    assert record.result == result
    assert reg.accounting(record.job_id).unobserved_attempts == ()


@pytest.mark.parametrize('bad_path', ['.git/config', '.GIT/config', 'dist/click.whl', 'src/__pycache__/leak.pyc', '../escape'])
def test_explicit_allowlist_cannot_admit_forbidden_source_members(tmp_path, monkeypatch, bad_path):
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode='w') as archive:
        item = tarfile.TarInfo(bad_path)
        item.size = 7
        archive.addfile(item, io.BytesIO(b'PRIVATE'))
    m, store, reg, builder, inputs, *_ = fixture(tmp_path, monkeypatch, archive=output.getvalue(), files=(bad_path,))
    result = builder.build(inputs, owner='diagnostic', claim_key='bad-member')
    assert result.disposition != c.Disposition.SUCCESS
    assert not any(ref.kind == 'TaskBundle' for ref in result.artifacts)


def test_allowlist_must_cover_exact_B_and_controller_role_is_required(tmp_path, monkeypatch):
    m, store, reg, builder, inputs, *_ = fixture(tmp_path, monkeypatch)
    changed = inputs.model_copy(update={'baseline_files': ('src/click/__init__.py',)})
    assert builder.build(changed, owner='diagnostic', claim_key='omitted-B').disposition != c.Disposition.SUCCESS
    with pytest.raises(TypeError):
        m.TaskBuilder(store=ArtifactStore(store.root, c.ActorRole.SOLVER), registry=reg, revision='a' * 40)


def test_post_freeze_root_publication_reuses_exact_snapshot_and_costs(tmp_path, monkeypatch):
    m, store, reg, builder, inputs, *_ = fixture(tmp_path, monkeypatch)
    original = store.put_artifact
    published = []
    def lost_reply(artifact):
        ref = original(artifact)
        if isinstance(artifact, c.TaskBundle):
            published.append(ref)
            raise OSError('DIAGNOSTIC reply lost after real CAS publication')
        return ref
    with monkeypatch.context() as patch:
        patch.setattr(store, 'put_artifact', lost_reply)
        with pytest.raises(m.BuildRecoveryRequired) as caught:
            builder.build(inputs, owner='diagnostic', claim_key='recover-1')
    claim = caught.value.claim
    frozen_costs = reg.accounting(claim.job_id).observations[0].observation.costs
    result = builder.recover(claim)
    assert result.artifacts == (published[0],) and result.costs == frozen_costs
    assert builder.recover(claim) == result
    assert len(reg.attempts(claim.job_id)) == 1
    assert sum(event.action == 'complete' for event in reg.events()) == 1


def test_pending_freeze_receipt_is_replayable_without_reassembly(tmp_path, monkeypatch):
    m, store, reg, builder, inputs, *_ = fixture(tmp_path, monkeypatch)
    original = store.put_bytes
    def fail_receipt(data, kind, visibility):
        if kind == 'm6-frozen-build':
            raise OSError('DIAGNOSTIC frozen receipt publication outage')
        return original(data, kind, visibility)
    with monkeypatch.context() as patch:
        patch.setattr(store, 'put_bytes', fail_receipt)
        with pytest.raises(m.BuildPublicationFailed) as caught:
            builder.build(inputs, owner='diagnostic', claim_key='pending-1')
    pending = caught.value
    assert pending.claim.attempt_id in {a.claim.attempt_id for a in reg.attempts(pending.claim.job_id)}
    original_payload = pending.payload
    changed = json.loads(pending.payload)
    changed['recorded_at'] = '2026-09-19T00:00:00Z'
    pending.payload = canonical_json(changed)
    with pytest.raises(m.BuildRejected):
        builder.retry_publication(pending)
    pending.payload = original_payload
    result = builder.retry_publication(pending)
    assert result.disposition == c.Disposition.SUCCESS
    assert builder.recover(pending.claim) == result


def test_changed_contract_requires_new_complete_root_and_keeps_raw_H(tmp_path, monkeypatch):
    m, store, reg, builder, inputs, baseline, reference, private = fixture(tmp_path, monkeypatch)
    first = builder.build(inputs, owner='diagnostic', claim_key='original')
    first_bytes = builder.solver_package(first.artifacts[0])
    contract = mutate(store, inputs.contract, visible_request='DIAGNOSTIC changed public request')
    plan = mutate(store, inputs.scenario_plan, contract=contract)
    verifier = mutate(store, inputs.verifier, contract=contract, scenario_plan=plan)
    changed = inputs.model_copy(update={'contract': contract, 'scenario_plan': plan, 'verifier': verifier})
    second = builder.build(changed, owner='diagnostic', claim_key='changed')
    assert second.disposition == c.Disposition.SUCCESS and first.artifacts != second.artifacts
    for result in (first, second):
        task = store.get_artifact(result.artifacts[0])
        assert task.reference_solution == reference and task.source_pair == inputs.source_pair
        assert task.state == c.TaskState.BUILT and task.qualification is None
    assert builder.solver_package(first.artifacts[0]) == first_bytes
    assert builder.solver_package(second.artifacts[0]) != first_bytes


def test_source_link_is_rejected_without_controller_extraction(tmp_path, monkeypatch):
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode='w') as archive:
        item = tarfile.TarInfo('src/click/linked.py')
        item.type = tarfile.SYMTYPE
        item.linkname = '/private/controller'
        archive.addfile(item)
    m, store, reg, builder, inputs, *_ = fixture(tmp_path, monkeypatch, archive=output.getvalue(), files=('src/click/linked.py',))
    result = builder.build(inputs, owner='diagnostic', claim_key='link')
    assert result.disposition != c.Disposition.SUCCESS
    assert not (tmp_path / 'src').exists()


@pytest.mark.parametrize('point', ['before_freeze', 'after_freeze'])
def test_real_process_crash_keeps_unknown_or_resumes_frozen_publication(tmp_path, monkeypatch, point):
    m, store, reg, builder, inputs, *_ = fixture(tmp_path, monkeypatch)
    code = r'''
import os, sys
from pathlib import Path
from feature_rl.artifacts import ArtifactStore
from feature_rl.contracts import ActorRole, TaskBundle
from feature_rl.registry import Registry
from feature_rl.pipeline import TaskBuilder, BuildInputs
from feature_rl.pipeline import packaging
from test_factory import diagnostic_pins
root, inputs_json, point = sys.argv[1:]
store = ArtifactStore(Path(root) / 'objects', ActorRole.CONTROLLER)
reg = Registry(Path(root) / 'registry', store)
builder = TaskBuilder(store=store, registry=reg, revision='a' * 40)
packaging.WHEELS = diagnostic_pins()
if point == 'before_freeze':
    packaging.assemble = lambda *args: os._exit(77)
else:
    original = store.put_artifact
    def crash(artifact):
        if isinstance(artifact, TaskBundle): os._exit(77)
        return original(artifact)
    store.put_artifact = crash
builder.build(BuildInputs.model_validate_json(inputs_json), owner='diagnostic', claim_key='crash')
raise AssertionError('requested crash point was not reached')
'''
    child = subprocess.run([sys.executable, '-c', code, str(tmp_path.resolve()), inputs.model_dump_json(), point],
        env=os.environ | {'PYTHONPATH': os.pathsep.join((str(Path('src').resolve()), str(Path('tests').resolve())))},
        capture_output=True, timeout=20)
    assert child.returncode == 77, child.stderr.decode()
    from feature_rl.registry import Claim
    event = next(event for event in reg.events() if event.action == 'claim')
    claim = Claim.model_validate_json(canonical_json(event.data['claim']))
    observed = reg.accounting(claim.job_id)
    if point == 'before_freeze':
        assert all(cost.wall_seconds is None for cost in observed.observations[0].observation.costs)
        before = reg.events()
        with pytest.raises(m.BuildRecoveryRequired):
            builder.recover(claim)
        assert reg.events() == before and reg.job(claim.job_id).result is None
        reg.abandon(claim, reason='DIAGNOSTIC actual process exit 77 before freeze', evidence=observed.observations[0].observation.receipts)
        reg.retry(claim.job_id, reason='DIAGNOSTIC explicit restart after observed process death', evidence=observed.observations[0].observation.receipts)
        result = builder.build(inputs, owner='diagnostic', claim_key='explicit-retry')
        assert result.disposition == c.Disposition.SUCCESS
        assert len(reg.attempts(claim.job_id)) == 2
        assert any(all(cost.wall_seconds is None for cost in item.observation.costs) for item in reg.accounting(claim.job_id).observations)
    else:
        costs = observed.observations[0].observation.costs
        result = builder.recover(claim)
        assert result.costs == costs and result.disposition == c.Disposition.SUCCESS
        assert builder.recover(claim) == result
        assert len(reg.attempts(claim.job_id)) == 1
