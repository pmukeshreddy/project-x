"""Fixed trusted transport code executed only inside the untrusted Docker worker."""
EXPORT_CODE = r'''
import sys
try:
 import io,os,stat,sys,tarfile
 max_bytes,max_files,max_tar=map(int,sys.argv[1:]);entries=[];total=0;count=0
 root=os.open('/workspace/source',os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
 def visit(directory,prefix):
  global total,count
  for name in sorted(os.listdir(directory)):
   count+=1
   if count>max_files:raise RuntimeError("source count cap")
   path=prefix+name
   if len(entries)>=max_files:raise RuntimeError('source count cap')
   fd=os.open(name,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK,dir_fd=directory)
   try:
    before=os.fstat(fd)
    if stat.S_ISDIR(before.st_mode):visit(fd,path+'/');continue
    if not stat.S_ISREG(before.st_mode):raise RuntimeError('nonregular source')
    total+=before.st_size
    if total>max_bytes:raise RuntimeError('source byte cap')
    data=bytearray()
    while len(data)<=before.st_size:
     chunk=os.read(fd,min(65536,before.st_size+1-len(data)))
     if not chunk:break
     data.extend(chunk)
    after=os.fstat(fd)
    if len(data)!=before.st_size or (before.st_size,before.st_mtime_ns,before.st_ctime_ns)!=(after.st_size,after.st_mtime_ns,after.st_ctime_ns):raise RuntimeError('source changed during capture')
    entries.append((path,bytes(data),bool(before.st_mode&0o111)))
   finally:os.close(fd)
 visit(root,'');os.close(root)
 out=io.BytesIO()
 with tarfile.open(fileobj=out,mode='w',format=tarfile.PAX_FORMAT) as t:
  for path,data,executable in entries:
   item=tarfile.TarInfo(path);item.size=len(data);item.mode=0o755 if executable else 0o644;t.addfile(item,io.BytesIO(data))
 value=out.getvalue()
 if len(value)>max_tar:raise RuntimeError('archive output cap')
 sys.stdout.buffer.write(value)
except (OSError,ValueError,RuntimeError) as exc:
 sys.stderr.write("SOURCE_REJECTED: "+type(exc).__name__+": "+str(exc)[:1000]+"\n")
 sys.exit(65)
'''

STAGE_CODE = r'''
import io,os,pathlib,sys,tarfile
cap=int(sys.argv[1]);data=sys.stdin.buffer.read(cap+1)
if len(data)>cap:raise RuntimeError('staging cap')
root=pathlib.Path('/workspace')
with tarfile.open(fileobj=io.BytesIO(data),mode='r:') as t:
 for item in t:
  parts=item.name.split('/')
  if not item.isfile() or any(x in ('','.','..') for x in parts) or item.name.startswith('/') or '\\' in item.name:raise RuntimeError('invalid staging member')
  path=root.joinpath(*parts);path.parent.mkdir(parents=True,exist_ok=True)
  with path.open('xb') as f:f.write(t.extractfile(item).read(item.size+1))
  path.chmod(0o755 if item.mode&0o111 else 0o644)
print('staged')
'''
