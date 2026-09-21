"""Source-free acquisition for npm, Cargo and Go, frozen before offline builds."""
import hashlib
import json

from feature_rl.artifacts import canonical_json
from feature_rl.contracts import CommandSpec, DependencyPin, Visibility
from .archive import SourceArchive, SourceFile
from .command_profiles import CommandRuntimeProfile
from .models import PolicyRejected, SandboxPolicy
from .profiles import SystemPackagePin
from .toolchains import infer_toolchain


ROOT = '/opt/feature-rl-toolchain-resolution'
RESOLVE_COMMAND = r'''
import hashlib,io,json,os,pathlib,platform,re,subprocess,tarfile
root=pathlib.Path('/opt/feature-rl-toolchain-resolution');settings=json.loads((root/'input.json').read_text())
manifest=root/'manifests';cache=root/'cache';cache.mkdir();os.chdir(manifest)
def run(argv,**kwargs):return subprocess.run(argv,check=True,**kwargs)
def output(argv):return run(argv,capture_output=True,text=True).stdout.strip()
language=settings['language'];env=dict(os.environ)
packages=[]
if settings['system_requirements']:
 policy=pathlib.Path('/usr/sbin/policy-rc.d');policy.write_text('#!/bin/sh\nexit 101\n');policy.chmod(0o755)
 run(['apt-get','update']);run(['apt-get','install','-y','--no-install-recommends',*settings['system_requirements']])
 for declaration in settings['system_requirements']:
  name=declaration.split('=')[0]
  version=output(['dpkg-query','-W','-f=${Version}',name]);packages.append(dict(name=name,version=version))
if language=='node':
 version=output(['node','--version']).removeprefix('v');manager=output(['npm','--version'])
 if settings['manager_version'] and manager!=settings['manager_version']:raise RuntimeError('declared npm version mismatch')
 for tool,constraint in settings['constraints'].items():
  if tool not in ('node','npm'):raise RuntimeError('unsupported Node engine constraint')
  run(['node','-e',"const semver=require('/usr/local/lib/node_modules/npm/node_modules/semver');if(!semver.satisfies(process.argv[1],process.argv[2]))process.exit(1)",version if tool=='node' else manager,constraint])
 run(['npm','ci','--ignore-scripts','--no-audit','--no-fund','--cache',str(cache/'npm')])
elif language=='rust':
 version=output(['rustc','--version']).split()[1];manager=output(['cargo','--version']).split()[1]
 env.update(CARGO_HOME=str(cache/'cargo'),CARGO_REGISTRIES_CRATES_IO_PROTOCOL='sparse')
 run(['cargo','fetch','--locked'],env=env)
elif language=='go':
 version=output(['go','version']).split()[2].removeprefix('go');manager=version
 env.update(GOMODCACHE=str(cache/'go'),GOTOOLCHAIN='local',GOPROXY='https://proxy.golang.org',GOSUMDB='sum.golang.org')
 before={path.name:path.read_bytes() for path in manifest.iterdir()}
 run(['go','mod','download'],env=env)
 if {path.name:path.read_bytes() for path in manifest.iterdir()}!=before:raise RuntimeError('Go dependency acquisition changed the frozen module declarations')
else:raise RuntimeError('unsupported toolchain')
selector=settings['toolchain_selector']
if version!=selector and not version.startswith(selector+'.'):raise RuntimeError('declared toolchain version mismatch')
out=io.BytesIO();total=0;count=0
with tarfile.open(fileobj=out,mode='w',format=tarfile.PAX_FORMAT) as archive:
 for path in sorted(cache.rglob('*')):
  if path.is_symlink():raise RuntimeError('dependency cache contains symlink')
  if path.is_dir():continue
  if not path.is_file():raise RuntimeError('dependency cache contains nonregular data')
  data=path.read_bytes();total+=len(data);count+=1
  if total>settings['max_bytes'] or count>settings['max_files']:raise RuntimeError('dependency supply cap')
  item=tarfile.TarInfo(str(path.relative_to(cache)));item.size=len(data);item.mode=0o644;archive.addfile(item,io.BytesIO(data))
payload=out.getvalue()
if len(payload)>settings['max_bytes']:raise RuntimeError('dependency supply archive cap')
(root/'supply.tar').write_bytes(payload)
(root/'observed.json').write_text(json.dumps(dict(interpreter_version=version,harness_interpreter_version=platform.python_version(),manager_version=manager,system_packages=packages,sha256=hashlib.sha256(payload).hexdigest()),sort_keys=True,separators=(',',':')))
'''


def resolution_context(plan, policy, base_image):
    from pydantic import TypeAdapter
    from .images import ImageDigest
    base_image = TypeAdapter(ImageDigest).validate_python(base_image)
    settings = {key: plan[key] for key in ('language', 'toolchain_selector', 'manager_version', 'constraints', 'system_requirements')}
    settings.update(max_bytes=policy.max_staging_bytes, max_files=policy.max_files)
    files = {'manifests/' + name: SourceFile(data, False) for name, data in plan['resolver_files'].items()}
    files['input.json'] = SourceFile(canonical_json(settings), False)
    files['resolve.py'] = SourceFile(RESOLVE_COMMAND.encode(), False)
    files['Dockerfile'] = SourceFile(f'''FROM --platform={policy.platform} {base_image}
USER 0:0
ENV SOURCE_DATE_EPOCH=946684800 LANG=C.UTF-8 LC_ALL=C.UTF-8 TZ=UTC PYTHONDONTWRITEBYTECODE=1
RUN apt-get update && apt-get install -y --no-install-recommends python3 ca-certificates && ln -s /usr/bin/python3 /usr/local/bin/python && rm -rf /var/lib/apt/lists/*
COPY manifests/ {ROOT}/manifests/
COPY input.json resolve.py {ROOT}/
RUN ["/usr/local/bin/python", "-I", "{ROOT}/resolve.py"]
USER 65534:65534
WORKDIR /workspace
'''.encode(), False)
    data = SourceArchive(files).to_tar()
    if len(data) > policy.max_staging_bytes:
        raise PolicyRejected('toolchain acquisition context exceeds bound')
    return data


CAPTURE_SUPPLY = r'''
import io,pathlib,sys,tarfile
root=pathlib.Path('/opt/feature-rl-toolchain-resolution');cap=int(sys.argv[1]);out=io.BytesIO()
with tarfile.open(fileobj=out,mode='w') as archive:
 for name in ('observed.json','supply.tar'):
  data=(root/name).read_bytes()
  if len(data)>cap:raise RuntimeError('toolchain resolution capture cap')
  item=tarfile.TarInfo(name);item.size=len(data);archive.addfile(item,io.BytesIO(data))
if len(out.getvalue())>cap:raise RuntimeError('toolchain resolution archive cap')
sys.stdout.buffer.write(out.getvalue())
'''


def resolve_command_repository(runtime, source, *, extra_roots=()):
    plan = infer_toolchain(source)
    engine = runtime.engine
    if engine.image_repository is None:
        raise PolicyRejected('automatic toolchain preparation requires an image repository')
    declaration = {key: value for key, value in plan.items() if key not in ('resolver_files', 'build_commands')}
    declaration['build_commands'] = [command.model_dump(mode='json') for command in plan['build_commands']]
    declaration['services'] = [service.model_dump(mode='json') for service in plan['services']]
    inputs = dict(declaration=declaration, platform=runtime.base_policy.platform,
                  sandbox=runtime.base_policy.model_dump(mode='json', exclude={'image', 'profile'}),
                  resolver_sha256=hashlib.sha256(RESOLVE_COMMAND.encode()).hexdigest(), extra_roots=sorted(set(extra_roots)))
    key = hashlib.sha256(canonical_json(inputs)).hexdigest()
    name = 'command-resolution-' + key + '.json'
    with engine.state.lock():
        engine._require_clean_owned_state()
        try:
            locked = engine.state.read(name)
        except FileNotFoundError:
            locked = None
        if locked is not None:
            if locked['inputs'] != inputs:
                raise PolicyRejected('toolchain resolution lock changed')
            policy = SandboxPolicy.model_validate_json(canonical_json(locked['policy']))
            pins = tuple(DependencyPin.model_validate_json(canonical_json(pin)) for pin in locked['pins'])
            dependency_supply(runtime.store, pins, policy)
            engine.ensure_image(policy.image)
            return policy, pins
    repository = {'node': 'node', 'rust': 'rust', 'go': 'golang'}[plan['language']]
    tag = 'docker.io/library/' + repository + ':' + plan['toolchain_selector'] + '-bookworm'
    engine.image_command(['pull', '--platform', runtime.base_policy.platform, tag])
    info = engine.inspect_image(tag)
    if len(info.get('RepoDigests') or ()) != 1:
        raise PolicyRejected('toolchain image did not resolve to one immutable digest')
    base = info['RepoDigests'][0]
    engine.ensure_image(base)
    context = resolution_context(plan, runtime.base_policy, base)
    context_ref = runtime.store.put_bytes(context, 'toolchain-resolution-context', Visibility.AUTHORING)
    tag = engine.image_repository + ':toolchain-' + key
    result = engine.image_command(['buildx', 'build', '--builder', 'default', '--platform', runtime.base_policy.platform,
        '--network', 'default', '--provenance=false', '--build-arg', 'SOURCE_DATE_EPOCH=946684800',
        '--output', 'type=docker', '--tag', tag, '-'], stdin=context, checked=False)
    from .docker import observation_json
    construction = runtime.publish({'inputs': inputs, 'base_image': base, 'context': context_ref.model_dump(mode='json'),
                                    'observation': observation_json(result)}, 'toolchain-resolution-build')
    if result.reason != 'exited' or result.exit_code != 0:
        error = PolicyRejected('source-free toolchain dependency acquisition failed: ' + result.stderr.decode(errors='replace')[-1200:])
        error.evidence = construction
        raise error
    built_id = engine.inspect_image(tag)['Id']
    engine.image_command(['push', tag])
    info = engine.inspect_image(tag)
    digests = [digest for digest in info.get('RepoDigests', ()) if digest.startswith(engine.image_repository + '@sha256:')]
    if len(digests) != 1 or info['Id'] != built_id:
        raise PolicyRejected('toolchain resolution image lacks its unique published digest')
    image = digests[0]
    if engine.ensure_image(image)['Id'] != built_id:
        raise PolicyRejected('published toolchain resolution image changed')
    bootstrap = runtime.base_policy.model_copy(update={'image': image, 'profile': None})
    engine.qualified = False
    engine.qualification = None
    engine.policy = bootstrap
    engine.qualify_boundary(image=image)
    session = engine.session(binding={'purpose': 'toolchain-resolution', 'inputs': key}, saved_source={}, image=image)
    try:
        with session:
            result = session.execute(CommandSpec(argv=('/usr/local/bin/python', '-I', '-c', CAPTURE_SUPPLY,
                str(runtime.base_policy.max_staging_bytes)), working_directory='/workspace', timeout_seconds=30.0),
                output_limit=runtime.base_policy.max_staging_bytes, artifact_capture=True)
            if result.reason != 'exited' or result.exit_code != 0:
                raise PolicyRejected('toolchain supply capture failed')
    finally:
        execution = runtime.evidence(session, 'toolchain-resolution')
    capture_policy = runtime.base_policy.model_copy(update={'max_archive_bytes': runtime.base_policy.max_staging_bytes,
                                                          'max_source_bytes': runtime.base_policy.max_staging_bytes})
    captured = SourceArchive.read(result.stdout, capture_policy)
    if set(captured.files) != {'supply.tar', 'observed.json'}:
        raise PolicyRejected('toolchain resolution capture roster mismatch')
    observed = json.loads(captured.files['observed.json'].data)
    supply = captured.files['supply.tar'].data
    digest = hashlib.sha256(supply).hexdigest()
    if observed['sha256'] != digest:
        raise PolicyRejected('toolchain dependency supply hash mismatch')
    SourceArchive.read(supply, capture_policy)
    artifact = runtime.store.put_bytes(supply, 'dependency-supply', Visibility.AUTHORING)
    pin = DependencyPin(name=plan['dependency_manager'] + '-locked-supply', version=digest, sha256=digest, artifact=artifact)
    evidence = runtime.publish({'inputs': inputs, 'base_image': base, 'resolved_image': image,
        'context': context_ref.model_dump(mode='json'), 'dependency': pin.model_dump(mode='json'), 'observed': observed,
        'construction': construction.model_dump(mode='json'), 'execution': execution.model_dump(mode='json'),
        'cleanup_verified': session.cleanup_verified}, 'repository-runtime-resolution', Visibility.AUTHORING)
    roots = set(plan['source_roots']) | set(extra_roots)
    plan['source_roots'] = tuple(sorted(root for root in roots if not any(root.startswith(parent + '/') for parent in roots if root != parent)))
    profile = CommandRuntimeProfile.from_plan(plan, interpreter_version=observed['interpreter_version'],
                                               harness_interpreter_version=observed['harness_interpreter_version'],
                                               system_packages=tuple(SystemPackagePin.model_validate(item) for item in observed['system_packages']),
                                               dependency_sha256=digest, resolution=evidence)
    policy = SandboxPolicy.model_validate(runtime.base_policy.model_copy(update={'image': image, 'profile': profile}))
    locked = {'inputs': inputs, 'policy': policy.model_dump(mode='json'), 'pins': [pin.model_dump(mode='json')]}
    with engine.state.lock():
        engine._require_clean_owned_state()
        try:
            prior = engine.state.read(name)
        except FileNotFoundError:
            engine.state.write(name, locked)
        else:
            if prior != locked:
                raise PolicyRejected('concurrent toolchain resolutions selected different supplies')
    return policy, (pin,)


def dependency_supply(store, pins, policy):
    profile = policy.profile
    if (len(pins) != 1 or pins[0].name != profile.dependency_manager + '-locked-supply'
            or pins[0].version != profile.dependency_sha256 or pins[0].sha256 != profile.dependency_sha256
            or pins[0].artifact.kind != 'dependency-supply' or pins[0].artifact.visibility != Visibility.AUTHORING):
        raise PolicyRejected('exact frozen toolchain dependency supply required')
    cap = policy.max_staging_bytes
    data = store.get_bytes(pins[0].artifact, max_envelope_bytes=4*((cap+2)//3)+4096, max_payload_bytes=cap)
    if hashlib.sha256(data).hexdigest() != profile.dependency_sha256:
        raise PolicyRejected('toolchain dependency supply raw hash mismatch')
    capture_policy = policy.model_copy(update={'max_archive_bytes': cap, 'max_source_bytes': cap})
    return SourceArchive.read(data, capture_policy)
