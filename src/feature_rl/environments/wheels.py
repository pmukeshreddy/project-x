"""Inert project-wheel validation; builds and installation stay in the sandbox."""
from email.parser import BytesParser
import io
import math
import re
import stat
import zipfile
import zlib

from packaging.specifiers import SpecifierSet
from packaging.tags import compatible_tags, cpython_tags, parse_tag
from packaging.utils import canonicalize_name, parse_wheel_filename
from packaging.version import Version

from .archive import safe_path
from .models import SourceRejected


CAPTURE_WHEEL_CODE = r'''
import io,pathlib,sys,tarfile
paths=list(pathlib.Path('/workspace/built').glob('*.whl'));cap=int(sys.argv[1])
if len(paths)!=1 or paths[0].is_symlink() or not paths[0].is_file():
 raise RuntimeError('exactly one regular project wheel required')
with paths[0].open('rb') as stream:data=stream.read(cap+1)
if len(data)>cap:raise RuntimeError('built wheel size limit')
out=io.BytesIO()
with tarfile.open(fileobj=out,mode='w',format=tarfile.PAX_FORMAT) as archive:
 item=tarfile.TarInfo(paths[0].name);item.size=len(data)
 archive.addfile(item,io.BytesIO(data))
sys.stdout.buffer.write(out.getvalue())
'''


def _compatible(tags, policy):
    """Check declared architecture/ABI here; the worker checks its exact sys_tags."""
    version = tuple(map(int, policy.profile.interpreter_version.split('.')[:2]))
    interpreter = 'cp'+''.join(map(str, version))
    machine = {'linux/amd64': 'x86_64', 'linux/arm64': 'aarch64'}[policy.platform]
    platforms = {'linux_'+machine} | {tag.platform for tag in tags if tag.platform == 'linux_'+machine
                 or re.fullmatch(r'manylinux_(?:[0-9]+_){2}'+machine, tag.platform)
                 or re.fullmatch(r'musllinux_(?:[0-9]+_){2}'+machine, tag.platform)
                 or re.fullmatch(r'manylinux(?:1|2010|2014)_'+machine, tag.platform)}
    # Explicit ABI/platform arguments avoid using the controller's interpreter,
    # OS or libc as evidence about the pinned Linux worker.
    supported = set(cpython_tags(version, abis=[interpreter], platforms=sorted(platforms)))
    supported.update(compatible_tags(version, interpreter=interpreter, platforms=sorted(platforms)))
    return bool(tags & supported)


def mapped_path(name, mappings):
    """Whether an installed file belongs to a declared project namespace."""
    for mapping in mappings:
        root = mapping.wheel
        if name == root or name.startswith(root+'/'):
            return True
        stem = root[:-3] if root.endswith('.py') else root
        if re.fullmatch(re.escape(stem)+r'(?:\.(?:abi3|cpython-[0-9]+[-A-Za-z0-9_]*))?\.so', name):
            return True
        if name.startswith(stem+'.libs/'):
            return True
    return False


def _tags(value):
    fields = value.split('-')
    if (len(value) > 255 or len(fields) != 3
            or any(not re.fullmatch(r'[A-Za-z0-9_]+(?:\.[A-Za-z0-9_]+)*', field) for field in fields)
            or math.prod(len(field.split('.')) for field in fields) > 1024):
        raise SourceRejected('invalid or oversized wheel tag set')
    return parse_tag(value)


def validate_wheel(data, source, filename, policy):
    if type(data) is not bytes or len(data) > policy.max_source_bytes:
        raise SourceRejected('built wheel size limit')
    if safe_path(filename) != filename or '/' in filename:
        raise SourceRejected('noncanonical built wheel filename')
    profile = policy.profile
    try:
        _tags('-'.join(filename[:-4].split('-')[-3:]))
        project, version, _, tags = parse_wheel_filename(filename)
        if (project != canonicalize_name(profile.project_name)
                or version != Version(profile.project_version) or not _compatible(tags, policy)):
            raise SourceRejected('built wheel filename project/version/platform mismatch')
        stem = '-'.join(filename.split('-')[:2])
        metadata = stem+'.dist-info'
        schemes = stem+'.data'
        libraries = project.replace('-', '_')+'.libs/'
        for mapping in profile.source_mappings:
            if not any(name == mapping.source or name.startswith(mapping.source+'/') for name in source.files):
                raise SourceRejected('wheel source mapping is absent from supplied source')
        with zipfile.ZipFile(io.BytesIO(data)) as wheel:
            infos = wheel.infolist()
            if (len(infos) > policy.max_files
                    or sum(item.file_size for item in infos) > policy.max_source_bytes+1024*1024):
                raise SourceRejected('expanded wheel cap')
            files = {}
            names = set()
            installed = set()
            for info in infos:
                name = safe_path(info.filename)
                if info.filename != name+('/' if info.is_dir() else '') or name in names:
                    raise SourceRejected('noncanonical or duplicate wheel paths')
                names.add(name)
                mode = stat.S_IFMT(info.external_attr >> 16)
                if mode not in (0, stat.S_IFDIR if info.is_dir() else stat.S_IFREG) or info.flag_bits & 1:
                    raise SourceRejected('linked, special or encrypted wheel member')
                if info.is_dir():
                    continue
                parts = name.split('/')
                if any(part.casefold() in {'.git', '.hg', '.svn', '.pytest_cache', 'controller_checks',
                                           'authoring_sessions', 'reference'} for part in parts):
                    raise SourceRejected('protected wheel path')
                if name.startswith(metadata+'/'):
                    destination = name
                elif parts[0] == schemes:
                    if len(parts) < 3 or parts[1] not in {'purelib', 'platlib', 'data', 'scripts', 'headers'}:
                        raise SourceRejected('unsupported wheel install scheme')
                    relative = '/'.join(parts[2:])
                    if parts[1] in {'purelib', 'platlib'}:
                        if not mapped_path(relative, profile.source_mappings):
                            raise SourceRejected('wheel install scheme replaces an undeclared import root')
                        destination = relative
                    elif parts[1] == 'scripts':
                        if len(parts) != 3:
                            raise SourceRejected('wheel scripts must be regular files without subdirectories')
                        destination = 'bin/'+relative
                    elif parts[1] == 'headers':
                        destination = 'include/'+project+'/'+relative
                    else:
                        # pip --target installs data under the target directory;
                        # data must not provide a second route to foreign imports.
                        if (any(part.endswith(('.dist-info', '.data')) for part in parts[2:])
                                or (relative.endswith(('.py', '.pyi', '.so', '.pyd', '.pth'))
                                    and not mapped_path(relative, profile.source_mappings))):
                            raise SourceRejected('wheel data replaces an undeclared import root')
                        destination = relative
                elif mapped_path(name, profile.source_mappings) or name.startswith(libraries):
                    destination = name
                else:
                    raise SourceRejected('unexpected wheel import root')
                if (destination.split('/')[0] in {'deps', 'controller_checks', 'authoring_sessions', 'reference'}
                        or destination.endswith('.pth') or destination in installed):
                    raise SourceRejected('wheel import hook or conflicting install paths')
                installed.add(destination)
                files[name] = wheel.read(info)
            if any('/'.join(name.split('/')[:n]) in files for name in files for n in range(1, len(name.split('/')))):
                raise SourceRejected('wheel file is an ancestor of another member')
            if any('/'.join(name.split('/')[:n]) in installed for name in installed for n in range(1, len(name.split('/')))):
                raise SourceRejected('wheel install paths have conflicting file/directory destinations')
            if not all(metadata+'/'+name in files for name in ('METADATA', 'WHEEL', 'RECORD')):
                raise SourceRejected('missing built wheel identity metadata')
            package = BytesParser().parsebytes(files[metadata+'/METADATA'])
            headers = BytesParser().parsebytes(files[metadata+'/WHEEL'])
            declared_tags = set()
            tag_headers = headers.get_all('Tag', [])
            if len(tag_headers) > 1024:
                raise SourceRejected('oversized wheel tag metadata')
            for value in tag_headers:
                declared_tags.update(_tags(value))
            if (len(package.get_all('Name', [])) != 1 or len(package.get_all('Version', [])) != 1
                    or canonicalize_name(package['Name']) != project or Version(package['Version']) != version
                    or headers.get_all('Root-Is-Purelib', []) not in (['true'], ['false'])
                    or headers.get_all('Wheel-Version', []) != ['1.0'] or declared_tags != tags):
                raise SourceRejected('built wheel metadata/filename identity mismatch')
            if any(not SpecifierSet(spec).contains(profile.interpreter_version, prereleases=True)
                   for spec in package.get_all('Requires-Python', [])):
                raise SourceRejected('built wheel requires an incompatible interpreter')
    except (zipfile.BadZipFile, ValueError, OSError, RuntimeError, NotImplementedError, EOFError, zlib.error) as exc:
        raise SourceRejected('invalid built wheel: '+str(exc)) from exc
    return data
