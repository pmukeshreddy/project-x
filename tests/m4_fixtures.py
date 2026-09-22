"""Explicitly hand-authored DIAGNOSTIC artifacts, never a feature contract.

The ordinary echo preservation probe exercises Click's public CLI. It says
nothing about the requested missing feature, H, model generation or admission.
"""
from datetime import datetime, timezone
import base64
import csv
import hashlib
import io
import json
import zipfile
from feature_rl import contracts as c
from feature_rl.artifacts import canonical_json
from feature_rl.environments import SourceArchive, SourceFile


# Explicit Click inputs for retained diagnostics; no production defaults.
WHEELS={
 'pytest':('9.0.2','pytest-9.0.2-py3-none-any.whl','711ffd45bf766d5264d487b917733b453d917afd2b0ad65223959f59089f875b'),
 'iniconfig':('2.3.0','iniconfig-2.3.0-py3-none-any.whl','f631c04d2c48c52b84d0d0549c99ff3859c98df65b3101406327ecc7d53fbf12'),
 'packaging':('26.0','packaging-26.0-py3-none-any.whl','b36f1fef9334a5588b4166f8bcd26a14e521f2b55e6b9de3aaa80d3ff7a37529'),
 'pluggy':('1.6.0','pluggy-1.6.0-py3-none-any.whl','e920276dd6813095e9377c0bc5566d94c932c33b27a3e3945d8389c374dd4746'),
 'pygments':('2.20.0','pygments-2.20.0-py3-none-any.whl','81a9e26dd42fd28a23a2d169d86d7ac03b46e2f8b59ed4698fb4785f946d0176'),
 'flit-core':('3.11.0','flit_core-3.11.0-py3-none-any.whl','fe464c086f630f106c0fc5001ee377980f45938f03f8f0d03da08a4841748541'),
}

def runtime_policy(**overrides):
    from feature_rl.environments import SandboxPolicy, RuntimeProfile, SourceMapping, WheelPin
    profile = RuntimeProfile(profile_id='click-8.3.3', project_name='click', project_version='8.3.3',
        interpreter_version='3.12.14', build_backend='flit_core.buildapi',
        build_requirements=('flit_core>=3.11,<4',), source_roots=('src',),
        source_mappings=(SourceMapping(source='src/click', wheel='click'),),
        import_modules=('click',), entry_points=('click.Group', 'click.group', 'click.command', 'click.testing.CliRunner'),
        supported_observables=('CLI exit code', 'combined terminal output'),
        dependencies=tuple(WheelPin(name=name, version=version, filename=filename, sha256=digest)
                           for name, (version, filename, digest) in WHEELS.items()))
    return SandboxPolicy(**(dict(image='python@sha256:eb5be8e5b4d0a159c237946bbdd06356dda5d19c30fc4f7843e8046d3a590333',
                                platform='linux/arm64', profile=profile) | overrides))


def diagnostic_wheel(name, version, *, extra_files=None):
    """Valid, deterministic metadata-only wheel; no implementation or runtime claim."""
    directory = name.replace('-', '_') + '-' + version + '.dist-info'
    files = {
        directory + '/METADATA': (
            f'Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n'
            'Summary: Synthetic unit diagnostic only; not the named package implementation\n\n'
        ).encode(),
        directory + '/WHEEL': (
            'Wheel-Version: 1.0\nGenerator: feature-rl-unit-diagnostic\n'
            'Root-Is-Purelib: true\nTag: py3-none-any\n'
        ).encode(),
    }
    files.update(extra_files or {})
    records = io.StringIO(newline='')
    writer = csv.writer(records, lineterminator='\n')
    for path, data in files.items():
        digest = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b'=').decode()
        writer.writerow((path, 'sha256=' + digest, len(data)))
    writer.writerow((directory + '/RECORD', '', ''))
    files[directory + '/RECORD'] = records.getvalue().encode()
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w', compression=zipfile.ZIP_STORED) as archive:
        for path, data in sorted(files.items()):
            entry = zipfile.ZipInfo(path, date_time=(2000, 1, 1, 0, 0, 0))
            entry.external_attr = 0o100644 << 16
            archive.writestr(entry, data)
    return output.getvalue()


def diagnostic_environment(store, *, baseline, limits, provenance, costs):
    """Structurally complete image receipts, explicitly synthetic unit evidence."""
    from feature_rl.environments import WheelPin
    from feature_rl.environments.images import image_context, RuntimeImage, HostRequirements

    pins, wheel_specs = [], []
    for name, (version, filename, _) in WHEELS.items():
        data = diagnostic_wheel(name, version)
        digest = hashlib.sha256(data).hexdigest()
        ref = store.put_bytes(data, 'dependency-wheel', c.Visibility.AUTHORING)
        pins.append(c.DependencyPin(name=name, version=version, sha256=digest, artifact=ref))
        wheel_specs.append(WheelPin(name=name, version=version, filename=filename, sha256=digest))
    profile = runtime_policy().profile.model_copy(update={'dependencies': tuple(wheel_specs)})
    policy = runtime_policy(profile=profile)
    policy_ref = store.put_bytes(canonical_json(policy.model_dump(mode='json')),
                                 'sandbox-policy', c.Visibility.AUTHORING)
    payload = image_context(store, tuple(pins), policy)
    context = store.put_bytes(payload, 'runtime-image-context', c.Visibility.AUTHORING)
    image = RuntimeImage(image_digest='unit-diagnostic.invalid/runtime@sha256:' + 'd' * 64,
        base_image=policy.image, context_sha256=hashlib.sha256(payload).hexdigest(),
        context=context, host_requirements=HostRequirements(platform=policy.platform))
    image_ref = store.put_bytes(canonical_json(image.model_dump(mode='json')),
                                'runtime-image', c.Visibility.AUTHORING)
    summary = store.put_bytes(canonical_json(dict(
        baseline=baseline.model_dump(mode='json'), image_digest=image.image_digest,
        context_sha256=image.context_sha256, cleanup_verified=True,
        scope='unit_diagnostic', note='Synthetic receipts only; no Docker build or execution occurred.')),
        'runtime-image-construction-summary', c.Visibility.AUTHORING)
    evidence = c.EvidenceRecord(producer='Synthetic runtime fixture; no build or execution',
        command=('unit-diagnostic-runtime-fixture',), recorded_at=provenance.created_at,
        exit_status=0, artifacts=(image_ref, context, summary), revision='a' * 40,
        scope='unit_diagnostic')
    recipe = c.EnvironmentRecipe(kind='EnvironmentRecipe', schema_version=1,
        visibility=c.Visibility.AUTHORING,
        provenance=c.Provenance(producer='Synthetic unit runtime fixture', producer_version='1',
            created_at=provenance.created_at,
            inputs=(baseline, policy_ref, image_ref, context, *(pin.artifact for pin in pins)),
            evidence=(evidence,)), costs=costs,
        runtime_image=image_ref, image_digest=image.image_digest,
        interpreter_version=profile.interpreter_version, dependencies=tuple(pins),
        setup=profile.setup, reset=profile.setup, services=(), limits=limits,
        neutral_repairs=profile.neutral_repairs, locale='C.UTF-8', timezone='UTC',
        environment=tuple(c.EnvironmentVariable(name=k, value=v) for k, v in profile.environment),
        randomness=c.SeedPolicy(algorithm='PYTHONHASHSEED', seeds=(0,), same_cases_within_group=True),
        network_policy='none', baseline=baseline)
    return store.put_artifact(recipe)


def diagnostic(store, *, baseline=None, environment=None, case_count=2):
    now=datetime(2026,9,19,tzinfo=timezone.utc)
    def raw(data,kind='m4-diagnostic',visibility=c.Visibility.PRIVATE):
        return store.put_bytes(data if isinstance(data,bytes) else canonical_json(data),kind,visibility)
    source=raw(b'DIAGNOSTIC ONLY: CLI echoes supplied word with a newline.',visibility=c.Visibility.PUBLIC)
    evidence=c.EvidenceRecord(producer='M4 diagnostic fixture, not qualification',command=('diagnostic-fixture',),
        recorded_at=now,exit_status=0,artifacts=(source,),revision='a'*40,scope='unit_diagnostic')
    cost=c.CostRecord(category='verifier',wall_seconds=None,cpu_seconds=None,gpu_seconds=None,input_tokens=None,
        output_tokens=None,human_minutes=None,usd=None,measurement='unknown',note='Hand-authored diagnostic fixture; no empirical qualification')
    common=dict(schema_version=1,visibility=c.Visibility.PRIVATE,provenance=c.Provenance(producer='diagnostic',
        producer_version='1',created_at=now,inputs=(source,),evidence=(evidence,)),costs=(cost,))
    origin=c.EvidenceLink(source=source,locator='entire diagnostic text',quote='CLI echoes supplied word with a newline.',provenance_label='reconstructed_specification')
    rules=c.AllowedChanges(source_roots=('src',),forbidden_paths=(),dependencies='forbidden',dependency_artifacts=(),additional_artifact_types=())
    limits=c.ResourceLimits(wall_seconds=120.0,cpu_seconds=60.0,memory_bytes=512*1024*1024,pids=64,
        output_bytes=2*1024*1024,disk_bytes=128*1024*1024,tool_calls=100,input_tokens=1,output_tokens=1)
    req=c.Requirement(requirement_id='echo',statement='DIAGNOSTIC ONLY echo supplied word',mandatory=True,evidence=(origin,),observable='CLI output')
    contract=c.RequirementContract(kind='RequirementContract',**common,visible_request='DIAGNOSTIC ONLY',capability='ordinary echo diagnostic',
        entry_points=('click.command','click.echo','click.testing.CliRunner.invoke'),requirements=(req,),compatibility_obligations=(),
        feature_files=(c.FeatureFile(path='src/click/__init__.py',requirement_ids=('echo',),rationale='Diagnostic echo implementation',evidence=(origin,)),),
        ambiguities=(),allowed_changes=rules,public_checks=(),episode_limits=limits,provenance_label='reconstructed_specification')
    contract_ref=store.put_artifact(contract)
    source_pair=c.ArtifactRef(sha256='0'*64,kind='SourcePair',schema_version=2,visibility=c.Visibility.PRIVATE,encoding='json')
    validation=raw(b'No reference executed; unit diagnostic only','reference-validation')
    cases=[]
    for i in range(case_count):
        word=('indigo','saffron','cobalt','amber')[i % 4]
        # Fixed command and expected bytes for a synthetic fixture only.
        code='import click; click.echo('+repr(word)+')'
        stdin=raw(b'', 'behavioral-stdin')
        inp={'mode':'output','command':{'argv':['/usr/local/bin/python','-c',code],
            'working_directory':'/workspace','timeout_seconds':2.0},'stdin':stdin.model_dump(mode='json'),
            'origin':source.model_dump(mode='json')}
        expected={'exit_code':0,'stdout_hex':(word+'\n').encode().hex(),'stderr_hex':''}
        cases.append(c.CaseDefinition(case_id='c'+str(i),requirement_ids=('echo',),
            inputs=raw(inp,'behavioral-input'),expected=raw(expected,'reference-output'),mandatory=True))
    verifier=c.VerifierBundle(kind='VerifierBundle',**(common|{'schema_version':2}),contract=contract_ref,
        source_pair=source_pair,validation=validation,cases=tuple(cases),
        completion_manifest=tuple(x.case_id for x in cases),public_examples=(),
        permissions=c.VerifierPermissions(controller_role=c.ActorRole.CONTROLLER,output_limit_bytes=65536,submission_policy=rules))
    verifier_ref=store.put_artifact(verifier)
    if baseline is None:
        baseline=raw(SourceArchive({'src/click/__init__.py':SourceFile(b'# diagnostic',False)}).to_tar(),'source-archive',c.Visibility.AUTHORING)
    if environment is None:
        environment=diagnostic_environment(store, baseline=baseline, limits=limits,
            provenance=common['provenance'], costs=common['costs'])
    # No real private SourcePair/H is inspected or loaded by this fixture.
    source_pair=c.ArtifactRef(sha256='0'*64,kind='SourcePair',schema_version=2,visibility=c.Visibility.PRIVATE,encoding='json')
    task=c.TaskBundle(kind='TaskBundle',**common,state=c.TaskState.BUILT,partition=c.Partition.DEVELOPMENT,
        repository_family='diagnostic-click',request_lineage=('diagnostic',),source_pair=source_pair,baseline=baseline,
        solver_view=c.SolverView(instruction=source,workspace=source,public_checks=(),runtime_manifest=source,inventory=source),
        contract=contract_ref,environment=environment,adapter_version='behavioral-command-v1',private_oracle=verifier_ref,
        reference_solution=raw(b'No reference used','diagnostic-no-reference'),qualification=None)
    return store.put_artifact(task)


def replace_artifact(store,ref,**updates):
    old=store.get_artifact(ref)
    payload=old.model_dump(mode='json')
    payload.update({k:v.model_dump(mode='json') if hasattr(v,'model_dump') else v for k,v in updates.items()})
    return store.put_artifact(type(old).model_validate_json(json.dumps(payload)))
