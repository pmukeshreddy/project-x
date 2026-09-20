"""Bounded inert source archives. Never extract source onto the controller."""
from dataclasses import dataclass
import hashlib
import io
from pathlib import PurePosixPath
import tarfile
from feature_rl.artifacts import canonical_json
from .models import SandboxPolicy, SourceRejected


def safe_path(name: str) -> str:
    if not isinstance(name,str) or not name or len(name.encode('utf-8'))>1024:
        raise SourceRejected('empty or oversized source path')
    if '\\' in name or '\x00' in name or any(ord(c)<32 for c in name):
        raise SourceRejected('invalid source path characters')
    parts=name.rstrip('/').split('/')
    if any(p in {'','.', '..','.git','__pycache__'} for p in parts) or name.startswith('/'):
        raise SourceRejected('source traversal/history/cache path')
    if any(len(p.encode('utf-8'))>255 for p in parts):raise SourceRejected('source component exceeds filesystem limit')
    if name.endswith(('.pyc','.pyo')):raise SourceRejected('compiled Python source artifact')
    return '/'.join(parts)

@dataclass(frozen=True)
class SourceFile:
    data: bytes
    executable: bool

@dataclass(frozen=True)
class SourceArchive:
    files: dict[str,SourceFile]

    def without_pytest_cache(self, baseline):
        """Drop only new, ordinary pytest metadata, never tracked source changes.

        Link/device rejection and byte/count bounds happen before this projection.
        Existing baseline files, executable files and unknown cache payloads stay
        in the delta so the ordinary change policy still rejects them.
        """
        metadata = {'.gitignore', 'CACHEDIR.TAG', 'README.md',
                    'v/cache/nodeids', 'v/cache/lastfailed', 'v/cache/stepwise'}
        def transient(name, entry):
            parts = name.split('/')
            if name in baseline.files or entry.executable or '.pytest_cache' not in parts:
                return False
            index = parts.index('.pytest_cache')
            return '/'.join(parts[index+1:]) in metadata
        return SourceArchive({name: entry for name, entry in self.files.items()
                              if not transient(name, entry)})

    @classmethod
    def read(cls,data: bytes,policy: SandboxPolicy):
        if type(data) is not bytes or len(data)>policy.max_archive_bytes:
            raise SourceRejected('archive input byte limit')
        files={};seen=set();total=0
        try:
            with tarfile.open(fileobj=io.BytesIO(data),mode='r:') as tar:
                for count,item in enumerate(tar,1):
                    if count>policy.max_files:raise SourceRejected('archive member count limit')
                    name=safe_path(item.name)
                    if name in seen:raise SourceRejected('duplicate archive path')
                    seen.add(name)
                    if item.isdir():continue
                    if not item.isfile() or item.issparse():raise SourceRejected('only regular source files permitted')
                    total+=item.size
                    if item.size<0 or total>policy.max_source_bytes:raise SourceRejected('expanded source byte limit')
                    value=tar.extractfile(item).read(item.size+1)
                    if len(value)!=item.size:raise SourceRejected('truncated member')
                    files[name]=SourceFile(value,bool(item.mode&0o111))
        except (tarfile.TarError,ValueError,UnicodeError,OSError) as exc:
            raise SourceRejected('malformed source tar') from exc
        for name in files:
            if any('/'.join(name.split('/')[:n]) in files for n in range(1,len(name.split('/')))):
                raise SourceRejected('file is ancestor of another file')
        return cls(files)

    @property
    def tree_sha256(self):
        return hashlib.sha256(canonical_json([{ 'path':n,'sha256':hashlib.sha256(f.data).hexdigest(),'executable':f.executable} for n,f in sorted(self.files.items())])).hexdigest()

    def to_tar(self):
        out=io.BytesIO()
        with tarfile.open(fileobj=out,mode='w',format=tarfile.PAX_FORMAT) as tar:
            for name,entry in sorted(self.files.items()):
                if safe_path(name)!=name or not isinstance(entry,SourceFile) or type(entry.data) is not bytes or type(entry.executable) is not bool:raise SourceRejected('invalid in-memory source entry')
                item=tarfile.TarInfo(name);item.size=len(entry.data);item.mode=0o755 if entry.executable else 0o644
                tar.addfile(item,io.BytesIO(entry.data))
        return out.getvalue()

    def validate_changes(self,baseline,roots,forbidden):
        roots=tuple(safe_path(x) for x in roots);forbidden=tuple(safe_path(x) for x in forbidden)
        if not roots:raise SourceRejected('source change roots required')
        for name in set(self.files)|set(baseline.files):
            if self.files.get(name)==baseline.files.get(name):continue
            if '.pytest_cache' in name.split('/'):raise SourceRejected('unauthorized pytest cache change: '+name)
            if not any(name==r or name.startswith(r+'/') for r in roots):raise SourceRejected('change outside allowed source roots: '+name)
            if any(name==r or name.startswith(r+'/') for r in forbidden):raise SourceRejected('forbidden source change: '+name)
