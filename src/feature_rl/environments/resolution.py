"""Resolve repository declarations once, then retain immutable offline inputs.

The networked builder receives only parsed requirements and trusted resolver code.
Repository hooks never run there or on the controller. Builds and metadata checks
of repository code remain inside the ordinary qualified, networkless sandbox.
"""
import hashlib
import json
import re

from packaging.specifiers import SpecifierSet
from packaging.version import Version

from feature_rl.artifacts import canonical_json
from feature_rl.contracts import CommandSpec, DependencyPin, Visibility
from feature_rl.intake.sources import BoundedHttpFetcher
from .archive import SourceArchive, SourceFile
from .inference import infer_repository
from .models import PolicyRejected, SandboxPolicy
from .profiles import RuntimeProfile, WheelPin, SystemPackagePin, metadata_digest


CATALOG = 'https://raw.githubusercontent.com/docker-library/python/master/versions.json'
ROOT = '/opt/feature-rl-resolution'
# No repository-controlled program, shell command, index URL or build hook enters
# this program. Pip downloads wheels only, with markers evaluated on the target.
RESOLVE_CODE = r'''
import email.parser, hashlib, json, os, pathlib, platform, re, subprocess, sys, zipfile
from pip._vendor.packaging.requirements import Requirement
from pip._vendor.packaging.specifiers import SpecifierSet
from pip._vendor.packaging.utils import canonicalize_name, parse_wheel_filename
from pip._vendor.packaging.tags import sys_tags
root=pathlib.Path('/opt/feature-rl-resolution');settings=json.loads((root/'input.json').read_text())
if not SpecifierSet(settings['requires_python']).contains(platform.python_version()):
 raise RuntimeError('resolved interpreter violates repository requirements')
if settings['python_versions'] and not any(platform.python_version()==value or platform.python_version().startswith(value+'.') for value in settings['python_versions']):
 raise RuntimeError('resolved interpreter violates repository runtime selectors')
requirements=settings['build_requirements']+settings['requirements']
for text in requirements+settings['constraints']:
 req=Requirement(text)
 if req.url or (req.marker and any(x in str(req.marker) for x in ('platform_release','platform_version'))):
  raise RuntimeError('nonportable or external dependency: '+text)
(root/'requirements.txt').write_text('\n'.join(requirements)+'\n')
(root/'constraints.txt').write_text('\n'.join(settings['constraints'])+'\n')
system=settings['system_requirements']
if any(not re.fullmatch(r'[a-z0-9][a-z0-9+.-]*(?:=[0-9][A-Za-z0-9.+:~_-]*)?',p) for p in system):
 raise RuntimeError('unresolved system package declaration')
if system:
 subprocess.run(['apt-get','update'],check=True)
 subprocess.run(['apt-get','install','-y','--no-install-recommends',*system],check=True)
wheelhouse=root/'wheels';wheelhouse.mkdir()
subprocess.run([sys.executable,'-I','-m','pip','--isolated','--disable-pip-version-check','--no-cache-dir',
 'download','--only-binary=:all:','--index-url','https://pypi.org/simple','--dest',str(wheelhouse),
 '-r',str(root/'requirements.txt'),'-c',str(root/'constraints.txt')],check=True)
pins=[];total=0;tags=set(sys_tags());seen=set()
for path in sorted(wheelhouse.iterdir()):
 name,version,build,wheel_tags=parse_wheel_filename(path.name)
 if not tags.intersection(wheel_tags) or name in seen:raise RuntimeError('incompatible/duplicate dependency wheel')
 seen.add(name);data=path.read_bytes();total+=len(data)
 if total>settings['max_bytes'] or len(pins)>=64:raise RuntimeError('dependency capture exceeds bound')
 digest=hashlib.sha256(data).hexdigest()
 allowed=settings.get('dependency_hashes',{}).get(name)
 if allowed and digest not in allowed:raise RuntimeError('dependency disagrees with repository lock hash: '+name)
 with zipfile.ZipFile(path) as wheel:
  metadata=[n for n in wheel.namelist() if n.endswith('.dist-info/METADATA')]
  if len(metadata)!=1:raise RuntimeError('ambiguous wheel metadata')
  identity=email.parser.BytesParser().parsebytes(wheel.read(metadata[0]))
 if canonicalize_name(identity['Name'])!=name or identity['Version']!=str(version):raise RuntimeError('wheel identity mismatch')
 pins.append(dict(name=name,version=str(version),filename=path.name,sha256=digest))
packages=[]
if system:
 text=subprocess.run(['dpkg-query','-W','-f=${Package}\t${Version}\t${Status}\n'],check=True,capture_output=True,text=True).stdout
 requested={p.split('=')[0] for p in system}
 for line in text.splitlines():
  name,version,status=line.split('\t',2)
  if name in requested:
   if status!='install ok installed':raise RuntimeError('system package not installed: '+name)
   packages.append(dict(name=name,version=version))
 if {p['name'] for p in packages}!=requested:raise RuntimeError('unresolved system packages')
(root/'resolution.json').write_text(json.dumps(dict(interpreter_version=platform.python_version(),dependencies=pins,
 system_packages=packages),sort_keys=True,separators=(',',':')))
for path in sorted(root.rglob('*'),reverse=True):os.utime(path,(946684800,946684800),follow_symlinks=False)
os.utime(root,(946684800,946684800))
'''

CAPTURE_CODE = r'''
import io,pathlib,sys,tarfile
root=pathlib.Path('/opt/feature-rl-resolution');out=io.BytesIO();cap=int(sys.argv[1]);total=0
with tarfile.open(fileobj=out,mode='w') as archive:
 for path in [root/'resolution.json',*sorted((root/'wheels').glob('*.whl'))]:
  data=path.read_bytes();total+=len(data)
  if total>cap:raise RuntimeError('resolution capture limit')
  item=tarfile.TarInfo(path.name);item.size=len(data);archive.addfile(item,io.BytesIO(data))
if len(out.getvalue())>cap:raise RuntimeError('resolution archive limit')
sys.stdout.buffer.write(out.getvalue())
'''


def _python_images(engine, metadata):
    """Choose a declared compatible Python and immediately bind its OCI digest."""
    source = BoundedHttpFetcher(max_bytes=1024*1024, timeout_seconds=30).fetch(
        CATALOG, edit_history='not_applicable')
    catalog = json.loads(source.body)
    spec = SpecifierSet(metadata['requires_python'])
    selectors = metadata['python_versions']
    candidates = []
    for item in catalog.values():
        version = item.get('version', '')
        if not re.fullmatch(r'3\.[0-9]+\.[0-9]+', version) or not spec.contains(version):
            continue
        if selectors and not any(version == s or version.startswith(s+'.') for s in selectors):
            continue
        variants = item.get('variants', ())
        for distribution in ('bookworm', 'trixie'):
            if distribution in variants:
                candidates.append((Version(version), version+'-'+distribution))
    # Historical selectors may still have official images after they leave the
    # current catalog. Resolve minor tags immediately to an immutable digest;
    # the trusted resolver records and checks the actual complete version.
    for selector in selectors:
        if re.fullmatch(r'3\.[0-9]+(?:\.[0-9]+)?', selector):
            if selector.count('.')==2 and not spec.contains(selector):continue
            candidates.extend((Version(selector), selector+'-'+d)
                              for d in ('bookworm','trixie','bullseye','buster'))
    if not candidates:
        raise PolicyRejected('no official Python runtime satisfies repository declarations')
    distributions={'bookworm':0,'trixie':1,'bullseye':2,'buster':3}
    for _, tag in sorted(set(candidates),key=lambda item:(-item[0].major,-item[0].minor,-item[0].micro,
            distributions[item[1].rsplit('-',1)[1]],item[1]))[:16]:
        name = 'docker.io/library/python:'+tag
        pulled=engine.image_command(['pull', '--platform', engine.policy.platform, name],checked=False)
        if pulled.reason!='exited' or pulled.exit_code!=0:
            message=(pulled.stdout+pulled.stderr).decode(errors='replace').lower()
            if pulled.reason=='exited' and 'manifest unknown' in message:continue
            raise PolicyRejected('cannot acquire the declared Python runtime: '+message[-1000:])
        info = engine.inspect_image(name)
        digests = info.get('RepoDigests') or ()
        if len(digests) != 1:
            raise PolicyRejected('Python image did not resolve to one immutable digest')
        engine.ensure_image(digests[0])
        yield digests[0], source.body


def resolve_repository(runtime, source, *, extra_roots=()):
    """Return exact policy/pins; reuse an existing lock, never silently re-resolve."""
    metadata = infer_repository(source)
    engine = runtime.engine
    if engine.image_repository is None:
        raise PolicyRejected('automatic preparation requires a runtime image repository')
    manifests = set(metadata['manifest_paths']) | {'pyproject.toml','setup.py','setup.cfg','requirements.txt',
                                                  'uv.lock','poetry.lock','Pipfile.lock','.python-version'}
    # Version attributes are checked against the built wheel identity. Pinning
    # the entire containing Python module would forbid alternative implementations.
    manifests = {p for p in manifests if not p.endswith('.py') or p in {'setup.py','noxfile.py'}}
    hashes = {path: hashlib.sha256(source.files[path].data).hexdigest() if path in source.files else None
              for path in sorted(manifests)}
    inputs = dict(metadata=metadata, platform=runtime.base_policy.platform,
                  sandbox=runtime.base_policy.model_dump(mode='json',exclude={'image','profile'}),
                  manifests=hashes,extra_roots=sorted(set(extra_roots)), resolver_sha256=hashlib.sha256(RESOLVE_CODE.encode()).hexdigest())
    key = hashlib.sha256(canonical_json(inputs)).hexdigest()
    name = 'repository-resolution-'+key+'.json'
    with engine.state.lock():
        engine._require_clean_owned_state()
        try:
            locked = engine.state.read(name)
        except FileNotFoundError:
            locked = None
        if locked is not None:
            if locked['inputs'] != inputs:
                raise PolicyRejected('repository resolution lock changed')
            policy = SandboxPolicy.model_validate_json(canonical_json(locked['policy']))
            pins = tuple(DependencyPin.model_validate_json(canonical_json(p)) for p in locked['pins'])
            from .profiles import dependency_files
            dependency_files(runtime.store, pins, policy)
            policy.profile.validate_source(source)
            engine.ensure_image(policy.image)
            return policy, pins
    settings = {**metadata, 'max_bytes': runtime.base_policy.max_staging_bytes}
    tag = engine.image_repository+':resolution-'+key
    image=None;diagnosis='no compatible official interpreter image is available'
    for base,catalog in _python_images(engine,metadata):
        files = {'input.json': SourceFile(canonical_json(settings), False),
                 'resolve.py': SourceFile(RESOLVE_CODE.encode(), False)}
        files['Dockerfile'] = SourceFile((f'FROM --platform={engine.policy.platform} {base}\n'
            'USER 0:0\nENV SOURCE_DATE_EPOCH=946684800 PYTHONDONTWRITEBYTECODE=1 PYTHONHASHSEED=0 DEBIAN_FRONTEND=noninteractive\n'
            f'COPY input.json resolve.py {ROOT}/\nRUN ["python","-I","{ROOT}/resolve.py"]\n'
            'USER 65534:65534\nWORKDIR /workspace\n').encode(), False)
        context = SourceArchive(files).to_tar()
        with engine.state.lock():
            engine._require_clean_owned_state()
            built_result=engine.image_command(['buildx','build','--builder','default','--platform',engine.policy.platform,
                '--provenance=false','--build-arg','SOURCE_DATE_EPOCH=946684800',
                '--output','type=docker,rewrite-timestamp=true','--tag',tag,'-'],stdin=context,checked=False)
            if built_result.reason!='exited' or built_result.exit_code!=0:
                diagnosis=(built_result.stdout+built_result.stderr).decode(errors='replace')[-4000:]
                if built_result.reason=='exited' and any(message in diagnosis.lower() for message in (
                        'no matching distribution found','requires a different python','resolutionimpossible',
                        'could not find a version that satisfies','unable to locate package','has no installation candidate',
                        'resolved interpreter violates repository requirements','resolved interpreter violates repository runtime selectors')):
                    continue
                raise PolicyRejected('repository runtime resolution failed: '+diagnosis)
            built = engine.inspect_image(tag)['Id']
            engine.image_command(['push',tag])
            info = engine.inspect_image(tag)
            digests = [d for d in info.get('RepoDigests', ()) if d.startswith(engine.image_repository+'@sha256:')]
            if info['Id'] != built or len(digests) != 1:
                raise PolicyRejected('resolved image changed during publication')
            image = digests[0]
            if engine.ensure_image(image)['Id'] != built:
                raise PolicyRejected('published dependency resolution image changed')
        break
    if image is None:
        raise PolicyRejected('no reproducible runtime/dependency closure satisfies the repository: '+diagnosis)
    # Qualify the real image before using ordinary bounded worker transport.
    bootstrap = runtime.base_policy.model_copy(update={'image': image, 'profile': None})
    engine.qualified=False;engine.qualification=None
    engine.policy = bootstrap
    engine.qualify_boundary(image=image)
    session = engine.session(binding={'purpose':'repository-resolution','resolution':key}, saved_source={}, image=image)
    with session:
        result = session.execute(CommandSpec(argv=('python','-I','-c',CAPTURE_CODE,
            str(bootstrap.max_staging_bytes)), working_directory='/workspace', timeout_seconds=30),
            output_limit=bootstrap.max_staging_bytes,artifact_capture=True)
        if result.reason != 'exited' or result.exit_code != 0:
            raise PolicyRejected('cannot capture the complete resolved dependency closure')
    # This archive contains only trusted resolver output and inert wheel bytes.
    capture_policy = bootstrap.model_copy(update={'max_archive_bytes':bootstrap.max_staging_bytes,
                                                  'max_source_bytes':bootstrap.max_staging_bytes})
    captured = SourceArchive.read(result.stdout, capture_policy)
    observed = json.loads(captured.files.pop('resolution.json').data)
    wheel_pins = tuple(WheelPin.model_validate(p) for p in observed['dependencies'])
    if set(captured.files) != {p.filename for p in wheel_pins}:
        raise PolicyRejected('resolved wheel roster is incomplete')
    pins = []
    for pin in wheel_pins:
        data = captured.files[pin.filename].data
        if hashlib.sha256(data).hexdigest() != pin.sha256:
            raise PolicyRejected('resolved wheel digest differs from capture')
        artifact = runtime.store.put_bytes(data, 'dependency-wheel', Visibility.AUTHORING)
        pins.append(DependencyPin(name=pin.name, version=pin.version, sha256=pin.sha256, artifact=artifact))
    all_roots=set(metadata['source_roots']) | set(extra_roots)
    roots = tuple(sorted(p for p in all_roots if not any(p.startswith(parent+'/') for parent in all_roots if parent!=p)))
    catalog_ref=runtime.store.put_bytes(catalog,'python-image-catalog',Visibility.AUTHORING)
    context_ref=runtime.store.put_bytes(context,'repository-resolution-context',Visibility.AUTHORING)
    execution=runtime.publish({'execution':session.receipts,'cleanup_verified':session.cleanup_verified},
                              'repository-resolution-execution')
    evidence = runtime.publish({'inputs':inputs, 'base_image':base, 'resolved_image':image,
        'catalog':catalog_ref.model_dump(mode='json'), 'context':context_ref.model_dump(mode='json'),
        'execution_sha256':execution.sha256, 'cleanup_verified':session.cleanup_verified},
        'repository-runtime-resolution', Visibility.AUTHORING)
    profile = RuntimeProfile(profile_id='repository-'+key[:32], project_name=metadata['project_name'],
        project_version=metadata['project_version'], interpreter_version=observed['interpreter_version'],
        manifest_path=metadata['manifest_path'], manifest_sha256=hashes[metadata['manifest_path']],
        manifest_hashes=hashes,metadata_sha256=metadata_digest(metadata),resolution=evidence,build_backend=metadata['build_backend'],
        build_requirements=tuple(metadata['build_requirements']), source_roots=roots,
        source_mappings=tuple(metadata['source_mappings']), import_modules=tuple(metadata['import_modules']),
        entry_points=tuple(metadata['entry_points']), supported_observables=('JSON return value','CLI exit code',
            'standard output','standard error','combined terminal output'), dependencies=wheel_pins,
        system_packages=tuple(SystemPackagePin.model_validate(p) for p in observed['system_packages']))
    policy = SandboxPolicy.model_validate(runtime.base_policy.model_copy(update={'image':image,'profile':profile}))
    locked = dict(inputs=inputs, policy=policy.model_dump(mode='json'), pins=[p.model_dump(mode='json') for p in pins],
                  evidence=evidence.model_dump(mode='json'))
    with engine.state.lock():
        engine._require_clean_owned_state()
        try:
            existing = engine.state.read(name)
        except FileNotFoundError:
            engine.state.write(name, locked)
        else:
            if existing != locked:
                raise PolicyRejected('concurrent repository resolution selected different inputs')
    return policy, tuple(pins)
