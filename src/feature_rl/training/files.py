"""Immutable manifests for external, trusted native tensor directories.

CAS holds hashes, not multi-GB tensors. Every use revalidates the private files.
Candidate source/submissions must never be passed to these checkpoint functions.
"""
import hashlib
from pathlib import Path
from typing import Annotated, Literal
from pydantic import Field
from feature_rl import contracts as c
from feature_rl.artifacts import canonical_json
from feature_rl.environments.archive import safe_path
from feature_rl.verifiers.loader import read_local


class NativeFile(c.StrictModel):
    path: str
    sha256: c.Digest
    size: Annotated[int,Field(ge=0)]


class NativeDirectory(c.StrictModel):
    version: Literal['m7-native-directory-v1']='m7-native-directory-v1'
    files: Annotated[tuple[NativeFile,...],Field(min_length=1,max_length=10000)]


def inspect_directory(path: Path) -> NativeDirectory:
    path=Path(path)
    if not path.is_absolute() or path.is_symlink() or not path.is_dir():
        raise ValueError('Absolute private native directory required')
    rows=[]
    for file in sorted(path.rglob('*')):
        if file.is_symlink(): raise ValueError('Native directory symlinks forbidden')
        if file.is_dir(): continue
        if not file.is_file() or len(rows)>=10000: raise ValueError('Native directory entry bound/type')
        name=file.relative_to(path).as_posix();safe_path(name)
        size=0;digest=hashlib.sha256()
        with file.open('rb') as stream:
            for data in iter(lambda:stream.read(1024*1024),b''):
                size+=len(data);digest.update(data)
        rows.append(NativeFile(path=name,sha256=digest.hexdigest(),size=size))
    return NativeDirectory(files=tuple(rows))


def publish_directory(*,store,registry,path:Path,dependencies=()):
    manifest=inspect_directory(path)
    ref=store.put_bytes(canonical_json(manifest.model_dump(mode='json')),'m7-native-directory',c.Visibility.PRIVATE)
    registry.register(ref,dependencies=tuple(dependencies))
    return ref


def verify_directory(*,store,ref,path:Path):
    expected=read_local(store,ref,NativeDirectory,'m7-native-directory',4*1024*1024)
    if expected!=inspect_directory(path): raise ValueError('Native tensor directory differs from immutable manifest')
    return expected
