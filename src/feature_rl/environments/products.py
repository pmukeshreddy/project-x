"""Bounded build products, with only internal regular-file symbolic links.

Unlike submitted source, npm products need executable links. Links cannot be
ancestors, absolute paths, dangling paths or chains and never escape the product.
"""
from dataclasses import dataclass
import hashlib
import io
import posixpath
import tarfile

from feature_rl.artifacts import canonical_json
from .archive import SourceArchive, SourceFile, safe_path
from .models import SourceRejected


@dataclass(frozen=True)
class BuildProduct:
    files: dict[str, SourceFile]
    links: dict[str, str]

    @classmethod
    def read(cls, data, policy):
        if type(data) is not bytes or len(data) > policy.max_staging_bytes:
            raise SourceRejected('build product archive byte limit')
        files, links, seen = {}, {}, set()
        total = 0
        try:
            with tarfile.open(fileobj=io.BytesIO(data), mode='r:') as archive:
                for count, item in enumerate(archive, 1):
                    if count > policy.max_files:
                        raise SourceRejected('build product member count limit')
                    name = safe_path(item.name)
                    if name in seen:
                        raise SourceRejected('duplicate build product path')
                    seen.add(name)
                    if item.isdir():
                        continue
                    if item.issym():
                        target = item.linkname
                        if (not target or target.startswith('/') or '\\' in target
                                or any(ord(char) < 32 for char in target)):
                            raise SourceRejected('unsafe build product link')
                        safe_path(posixpath.normpath(posixpath.join(posixpath.dirname(name), target)))
                        links[name] = target
                        continue
                    if not item.isfile() or item.issparse() or item.size < 0:
                        raise SourceRejected('unsupported build product member')
                    total += item.size
                    if total > policy.max_staging_bytes:
                        raise SourceRejected('expanded build product byte limit')
                    value = archive.extractfile(item).read(item.size + 1)
                    if len(value) != item.size:
                        raise SourceRejected('truncated build product')
                    files[name] = SourceFile(value, bool(item.mode & 0o111))
        except (tarfile.TarError, ValueError, UnicodeError, OSError) as exc:
            raise SourceRejected('malformed build product') from exc
        leaves = set(files) | set(links)
        for name in seen:
            if any('/'.join(name.split('/')[:n]) in leaves for n in range(1, len(name.split('/')))):
                raise SourceRejected('build product leaf is an ancestor')
        for name, target in links.items():
            if posixpath.normpath(posixpath.join(posixpath.dirname(name), target)) not in files:
                raise SourceRejected('build product link must name a retained regular file')
        return cls(files, links)

    @property
    def tree_sha256(self):
        return hashlib.sha256(canonical_json({'files': SourceArchive(self.files).tree_sha256,
                                               'links': self.links})).hexdigest()

    def to_tar(self):
        out = io.BytesIO()
        with tarfile.open(fileobj=out, mode='w', format=tarfile.PAX_FORMAT) as archive:
            for name in sorted(set(self.files) | set(self.links)):
                item = tarfile.TarInfo(name)
                if name in self.links:
                    item.type = tarfile.SYMTYPE
                    item.linkname = self.links[name]
                    archive.addfile(item)
                else:
                    entry = self.files[name]
                    item.size = len(entry.data)
                    item.mode = 0o755 if entry.executable else 0o644
                    archive.addfile(item, io.BytesIO(entry.data))
        return out.getvalue()


CAPTURE_PRODUCT = r'''
import io,json,os,pathlib,stat,sys,tarfile
root=pathlib.Path('/workspace/site');settings=json.loads(sys.stdin.buffer.read())
cap,count_cap=int(sys.argv[1]),int(sys.argv[2]);total=0;entries=[]
def visit(directory,prefix):
 global total
 for name in sorted(os.listdir(directory)):
  path=prefix+name
  if path=='target' and settings['language']=='rust':continue
  fd=None
  before=os.stat(name,dir_fd=directory,follow_symlinks=False)
  if stat.S_ISDIR(before.st_mode):
   fd=os.open(name,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=directory)
   try:visit(fd,path+'/')
   finally:os.close(fd)
   continue
  if stat.S_ISLNK(before.st_mode):entries.append((path,None,os.readlink(name,dir_fd=directory),False))
  elif stat.S_ISREG(before.st_mode):
   total+=before.st_size
   if total>cap:raise RuntimeError('build product byte cap')
   fd=os.open(name,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK,dir_fd=directory)
   try:
    opened=os.fstat(fd)
    if not stat.S_ISREG(opened.st_mode) or (opened.st_dev,opened.st_ino)!=(before.st_dev,before.st_ino):raise RuntimeError('build product changed')
    data=b''
    while len(data)<=before.st_size:
     chunk=os.read(fd,min(65536,before.st_size+1-len(data)))
     if not chunk:break
     data+=chunk
    after=os.fstat(fd)
    if len(data)!=before.st_size or (before.st_size,before.st_mtime_ns,before.st_ctime_ns)!=(after.st_size,after.st_mtime_ns,after.st_ctime_ns):raise RuntimeError('build product changed')
   finally:os.close(fd)
   entries.append((path,data,None,bool(before.st_mode&0o111)))
  else:raise RuntimeError('nonregular build output')
  if len(entries)>count_cap:raise RuntimeError('build product count cap')
fd=os.open(root,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
try:visit(fd,'')
finally:os.close(fd)
if settings['language']=='rust':
 for name in settings['entry_points']:
  if not name.startswith('target/'):continue
  path=root/name
  if path.is_symlink() or not path.is_file():raise RuntimeError('missing regular Rust build product')
  data=path.read_bytes();total+=len(data)
  if total>cap or len(entries)>=count_cap:raise RuntimeError('build product cap')
  entries.append((name,data,None,bool(path.stat().st_mode&0o111)))
out=io.BytesIO()
with tarfile.open(fileobj=out,mode='w',format=tarfile.PAX_FORMAT) as archive:
 for name,data,link,executable in sorted(entries):
  item=tarfile.TarInfo(name)
  if link is not None:item.type=tarfile.SYMTYPE;item.linkname=link;archive.addfile(item)
  else:item.size=len(data);item.mode=0o755 if executable else 0o644;archive.addfile(item,io.BytesIO(data))
if len(out.getvalue())>cap:raise RuntimeError('build product archive cap')
sys.stdout.buffer.write(out.getvalue())
'''


STAGE_PRODUCT = r'''
import io,pathlib,posixpath,sys,tarfile
cap=int(sys.argv[1]);data=sys.stdin.buffer.read(cap+1)
if len(data)>cap:raise RuntimeError('product staging byte cap')
root=pathlib.Path('/workspace/site');root.mkdir();links=[];names=set();regular=set()
with tarfile.open(fileobj=io.BytesIO(data),mode='r:') as archive:
 for item in archive:
  parts=item.name.split('/')
  if item.name in names or any(part in ('','.','..') for part in parts) or item.name.startswith('/') or '\\' in item.name:raise RuntimeError('invalid product path')
  names.add(item.name);path=root.joinpath(*parts)
  if item.issym():links.append((item.name,item.linkname));continue
  if not item.isfile() or item.issparse():raise RuntimeError('invalid product member')
  path.parent.mkdir(parents=True,exist_ok=True)
  with path.open('xb') as stream:stream.write(archive.extractfile(item).read(item.size+1))
  path.chmod(0o755 if item.mode&0o111 else 0o644);regular.add(item.name)
for name,target in links:
 if target.startswith('/') or '\\' in target or posixpath.normpath(posixpath.join(posixpath.dirname(name),target)) not in regular:raise RuntimeError('unsafe product link')
 if any(other.startswith(name+'/') for other in names):raise RuntimeError('link ancestor')
 path=root/name;path.parent.mkdir(parents=True,exist_ok=True);path.symlink_to(target)
print('build product staged')
'''
