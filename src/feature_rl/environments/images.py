"""Construction-only, content-addressed images for the existing Python profiles.

Only the generated Dockerfile and hash-checked wheels enter the build context.
Repository build hooks run later in the ordinary qualified sandbox, never in
the image builder or on the controller.
"""
import hashlib
import json
from typing import Annotated, Literal

from pydantic import Field
from feature_rl.artifacts import canonical_json
from feature_rl.contracts import ArtifactRef, CommandSpec, StrictModel, Visibility
from .archive import SourceArchive, SourceFile
from .models import PolicyRejected
from .profiles import dependency_files


ImageDigest = Annotated[str, Field(pattern=r'^[A-Za-z0-9][A-Za-z0-9._:/-]*@sha256:[0-9a-f]{64}$')]
ImageRepository = Annotated[str, Field(pattern=r'^[a-z0-9][a-z0-9.-]*(?::[0-9]+)?/[a-z0-9]+(?:[._/-][a-z0-9]+)*$')]
EPOCH = 946684800  # Fixed, ZIP-compatible 2000-01-01; never the build clock.
LABEL = 'feature-rl.runtime-context'
ROOT = '/opt/feature-rl'
LINK_DEPS = CommandSpec(argv=('python', '-I', '-c',
    "import os;os.symlink('/opt/feature-rl/deps','/workspace/deps')"),
    working_directory='/workspace', timeout_seconds=5.0)


class HostRequirements(StrictModel):
    platform: Literal['linux/arm64', 'linux/amd64']
    container_runtime: Literal['docker'] = 'docker'
    gpu: Literal['none'] = 'none'


class RuntimeImage(StrictModel):
    version: Literal['python-runtime-image-v1'] = 'python-runtime-image-v1'
    image_digest: ImageDigest
    base_image: ImageDigest
    context_sha256: Annotated[str, Field(pattern=r'^[0-9a-f]{64}$')]
    context: ArtifactRef
    host_requirements: HostRequirements


# Use the pinned image's packaging parser and target-platform marker environment,
# not libraries or marker values inherited from the controller. No imports from
# dependency wheels are needed to check the complete active dependency closure.
CHECK_CODE = r'''
import email.parser, importlib.metadata as metadata, json, os, pathlib, platform, subprocess, sys, zipfile
from pip._vendor.packaging.requirements import Requirement
from pip._vendor.packaging.specifiers import SpecifierSet
from pip._vendor.packaging.utils import canonicalize_name
root=pathlib.Path('/opt/feature-rl')
settings=json.loads(sys.stdin.buffer.read()) if len(sys.argv)>1 else json.loads((root/'profile.json').read_text())
version=platform.python_version()
if version!=settings['interpreter_version']:raise RuntimeError('interpreter version mismatch')
def requires_python(spec):
 if spec and not SpecifierSet(spec).contains(version,prereleases=True):
  raise RuntimeError('unsupported Requires-Python: '+spec)
expected={canonicalize_name(p['name']):p['version'] for p in settings['dependencies']}
installed={}
for dist in metadata.distributions(path=[str(root/'deps')]):
 name=canonicalize_name(dist.metadata['Name'])
 if name in installed:raise RuntimeError('duplicate installed distribution: '+name)
 installed[name]=dist
if {name:dist.version for name,dist in installed.items()}!=expected:
 raise RuntimeError('installed dependency identity differs from profile')
pending=[(name,'') for name in installed];seen=set()
def require(text,extra=''):
 req=Requirement(text)
 if req.marker and any(name in str(req.marker) for name in ('platform_release','platform_version')):
  raise RuntimeError('host-kernel-dependent requirements are unsupported: '+text)
 if req.marker and not req.marker.evaluate({'extra':extra}):return
 if req.url:raise RuntimeError('direct URL requirement is not reproducible: '+text)
 name=canonicalize_name(req.name)
 if name not in installed or not req.specifier.contains(installed[name].version,prereleases=True):
  raise RuntimeError('missing or incompatible pinned dependency: '+text)
 extras={canonicalize_name(x) for x in installed[name].metadata.get_all('Provides-Extra',[])}
 if not {canonicalize_name(x) for x in req.extras}<=extras:raise RuntimeError('unknown dependency extra: '+text)
 pending.extend((name,x) for x in ('',*sorted(req.extras)))
for requirement in settings['build_requirements']:require(requirement)
if len(sys.argv)>1:
 with zipfile.ZipFile(sys.argv[1]) as wheel:
  names=[name for name in wheel.namelist() if name.endswith('.dist-info/METADATA')]
  if len(names)!=1:raise RuntimeError('ambiguous project wheel metadata')
  project=email.parser.BytesParser().parsebytes(wheel.read(names[0]))
 if canonicalize_name(project['Name'])!=canonicalize_name(settings['project_name']) or project['Version']!=settings['project_version']:
  raise RuntimeError('project wheel identity mismatch')
 requires_python(project.get('Requires-Python'))
 for requirement in project.get_all('Requires-Dist',[]):require(requirement)
while pending:
 name,extra=pending.pop()
 if (name,extra) in seen:continue
 seen.add((name,extra));dist=installed[name]
 requires_python(dist.metadata.get('Requires-Python'))
 for requirement in dist.requires or ():require(requirement,extra)
for package in settings.get('system_packages',[]):
 result=subprocess.run(['dpkg-query','-W','-f=${Version}\n${Status}',package['name']],
  check=True,capture_output=True,text=True)
 if result.stdout!=package['version']+'\ninstall ok installed':
  raise RuntimeError('system package mismatch: '+package['name'])
if len(sys.argv)==1:
 for path in sorted(root.rglob('*'),reverse=True):os.utime(path,(946684800,946684800),follow_symlinks=False)
 os.utime(root,(946684800,946684800))
print('runtime dependency closure verified')
'''


def image_context(store, pins, policy):
    """Canonical, source-free recipe; the tar hash is also the cache key."""
    profile = policy.profile
    files = {'supply/'+name: entry for name, entry in dependency_files(store, pins, policy).items()}
    files['profile.json'] = SourceFile(canonical_json(profile.model_dump(mode='json',
        exclude={'neutral_repairs'})), False)
    files['check.py'] = SourceFile(CHECK_CODE.encode(), False)
    dockerfile = f'''FROM --platform={policy.platform} {policy.image}
USER 0:0
ENV SOURCE_DATE_EPOCH={EPOCH} PYTHONDONTWRITEBYTECODE=1 PYTHONHASHSEED=0 LANG=C.UTF-8 LC_ALL=C.UTF-8 TZ=UTC
COPY supply/ {ROOT}/supply/
COPY profile.json check.py {ROOT}/
RUN ["python", "-B", "-I", "-m", "pip", "--isolated", "--disable-pip-version-check", "--no-cache-dir", "install", "--no-index", "--no-deps", "--require-hashes", "--ignore-installed", "--no-compile", "--find-links", "{ROOT}/supply", "--target", "{ROOT}/deps", "-r", "{ROOT}/supply/requirements.txt"]
RUN ["python", "-B", "-I", "{ROOT}/check.py"]
USER 65534:65534
WORKDIR /workspace
'''
    files['Dockerfile'] = SourceFile(dockerfile.encode(), False)
    payload = SourceArchive(files).to_tar()
    if len(payload) > policy.max_staging_bytes:
        raise PolicyRejected('runtime image context exceeds staging limit')
    return payload


def validate_runtime_image(recipe, policy, store):
    ref = recipe.runtime_image
    if (ref is None or ref.kind != 'runtime-image' or ref.encoding != 'bytes'
            or ref.visibility != Visibility.AUTHORING or ref not in recipe.provenance.inputs):
        raise PolicyRejected('recipe lacks its exact runtime image record')
    record = RuntimeImage.model_validate_json(store.get_bytes(ref,
        max_envelope_bytes=65536, max_payload_bytes=32768))
    payload = image_context(store, recipe.dependencies, policy)
    if (record.image_digest != recipe.image_digest or record.base_image != policy.image
            or record.host_requirements != HostRequirements(platform=policy.platform)
            or record.context_sha256 != hashlib.sha256(payload).hexdigest()
            or record.context.kind != 'runtime-image-context'
            or record.context not in recipe.provenance.inputs
            or record.context.visibility != Visibility.AUTHORING):
        raise PolicyRejected('runtime image/profile/context identity mismatch')
    cap = policy.max_staging_bytes
    if store.get_bytes(record.context, max_envelope_bytes=4*((cap+2)//3)+4096,
                       max_payload_bytes=cap) != payload:
        raise PolicyRejected('retained runtime image recipe differs from profile')
    summaries = {ref for evidence in recipe.provenance.evidence for ref in evidence.artifacts
                 if ref.kind == 'runtime-image-construction-summary'}
    if len(summaries) != 1:
        raise PolicyRejected('runtime image lacks its baseline construction evidence')
    summary = summaries.pop()
    if summary.visibility != Visibility.AUTHORING:
        raise PolicyRejected('runtime image construction summary must be authoring-visible')
    observed = json.loads(store.get_bytes(summary, max_envelope_bytes=32768, max_payload_bytes=16384))
    if (observed.get('baseline') != recipe.baseline.model_dump(mode='json')
            or observed.get('image_digest') != record.image_digest
            or observed.get('context_sha256') != record.context_sha256
            or observed.get('cleanup_verified') is not True):
        raise PolicyRejected('runtime image baseline construction evidence mismatch')
    return record


def prepare_runtime_image(runtime, pins):
    """Build/publish once under the existing durable runtime operation lock."""
    engine, policy = runtime.engine, runtime.policy
    if engine.image_repository is None:
        raise PolicyRejected('new environments require an image_repository for published prebuilt runtimes')
    payload = image_context(runtime.store, pins, policy)
    key = hashlib.sha256(payload).hexdigest()
    context = runtime.store.put_bytes(payload, 'runtime-image-context', Visibility.AUTHORING)
    name = 'runtime-image-'+key+'.json'
    tag = engine.image_repository+':runtime-'+key
    with engine.state.lock():
        engine._require_clean_owned_state()
        try:
            record = RuntimeImage.model_validate_json(canonical_json(engine.state.read(name)))
        except FileNotFoundError:
            record = None
        if record is not None:
            if (record.context != context or record.context_sha256 != key or record.base_image != policy.image
                    or record.host_requirements != HostRequirements(platform=policy.platform)):
                raise PolicyRejected('runtime image cache identity mismatch')
            info = engine.ensure_image(record.image_digest)
        else:
            # Tags discover shared construction-cache entries only. No rollout
            # ever receives a tag, nor falls back from a recorded digest to one.
            pulled = engine.image_command(['pull', '--platform', policy.platform, tag], checked=False)
            if pulled.reason == 'exited' and pulled.exit_code == 0:
                info = engine.inspect_image(tag)
            else:
                message = (pulled.stdout+pulled.stderr).decode(errors='replace').lower()
                if pulled.reason != 'exited' or not any(value in message for value in ('manifest unknown', 'manifest_unknown')):
                    raise PolicyRejected('cannot resolve runtime image cache: '+message[-1500:])
                engine.ensure_image(policy.image)
                engine.image_command(['buildx', 'build', '--builder', 'default', '--platform', policy.platform,
                    '--network', 'none', '--provenance=false', '--build-arg', f'SOURCE_DATE_EPOCH={EPOCH}',
                    '--output', 'type=docker,rewrite-timestamp=true', '--label', LABEL+'='+key,
                    '--tag', tag, '-'], stdin=payload)
                info = engine.inspect_image(tag)
                check_image(info, key, policy)
                built_id = info['Id']
                engine.image_command(['push', tag])
                info = engine.inspect_image(tag)
                if info['Id'] != built_id:
                    raise PolicyRejected('runtime image changed while publishing')
            check_image(info, key, policy)
            digests = [value for value in (info.get('RepoDigests') or ())
                       if value.startswith(engine.image_repository+'@sha256:')]
            if len(digests) != 1:
                raise PolicyRejected('published runtime image must resolve to one registry manifest digest')
            pinned = engine.ensure_image(digests[0])
            if pinned['Id'] != info['Id']:
                raise PolicyRejected('published manifest differs from constructed runtime image')
            record = RuntimeImage(image_digest=digests[0], base_image=policy.image,
                context_sha256=key, context=context, host_requirements=HostRequirements(platform=policy.platform))
            engine.state.write(name, record.model_dump(mode='json'))
        check_image(info, key, policy)
    ref = runtime.publish(record.model_dump(mode='json'), 'runtime-image', Visibility.AUTHORING)
    return record, ref


def check_image(info, key, policy):
    if (info.get('Os') != 'linux' or info.get('Architecture') != policy.platform.split('/')[1]
            or (info.get('Config', {}).get('Labels') or {}).get(LABEL) != key
            or info['Config'].get('Volumes') or info['Config'].get('OnBuild')
            or not info.get('Created', '').startswith('2000-01-01T00:00:00')):
        raise PolicyRejected('prebuilt image identity/platform/reproducible timestamp mismatch')


def validate_baseline(runtime, image, baseline, source):
    """Reject unbuildable repositories and incomplete dependency closures early."""
    session = runtime.engine.session(binding={'purpose': 'runtime-image-construction',
        'context': image.context_sha256, 'tree': source.tree_sha256}, saved_source={}, image=image.image_digest)
    error = None
    try:
        with session:
            runtime.stage(session, source)
            commands = (*runtime.profile.setup[:2], CommandSpec(
                argv=('python', '-I', '-c', CHECK_CODE, '/workspace/built/'+runtime.profile.wheel_filename),
                working_directory='/workspace', timeout_seconds=30.0))
            for command in commands:
                settings = canonical_json(runtime.profile.model_dump(mode='json', exclude={'neutral_repairs'})) if command == commands[-1] else b''
                result = session.execute(command, settings, environment=runtime.profile.environment)
                if result.reason != 'exited' or result.exit_code != 0:
                    raise PolicyRejected('environment cannot be built with its pinned dependency closure: '+
                        result.stderr.decode(errors='replace')[-1500:])
            wheel = runtime.capture_wheel(session, source)
    except BaseException as exc:
        error = exc
        raise
    finally:
        evidence = runtime.evidence(session, 'runtime-image-construction', {
            'image_digest': image.image_digest, 'error': repr(error) if error else None})
    return runtime.publish({'baseline': baseline.model_dump(mode='json'),
        'image_digest': image.image_digest, 'context_sha256': image.context_sha256,
        'source_tree_sha256': source.tree_sha256, 'wheel_sha256': hashlib.sha256(wheel).hexdigest(),
        'cleanup_verified': session.cleanup_verified, 'execution_sha256': evidence.sha256},
        'runtime-image-construction-summary', Visibility.AUTHORING)
