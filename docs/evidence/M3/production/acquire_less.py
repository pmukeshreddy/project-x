"""Bounded official package acquisition; decode only inert ar/tar bytes."""
from datetime import datetime,timezone
import hashlib,io,json,pathlib,tarfile,urllib.request
root=pathlib.Path('.feature-rl/research/M3/production/less-repair');root.mkdir(exist_ok=False)
receipt={'started_at':datetime.now(timezone.utc).isoformat(),'downloads':[],'cost':{'usd':0,'remote_compute':False}}
for name,url,cap in [('download.html','https://packages.debian.org/bookworm/arm64/less/download',200000),('package.html','https://packages.debian.org/bookworm/arm64/less',200000),('less.deb','https://deb.debian.org/debian/pool/main/l/less/less_590-2.1~deb12u2_arm64.deb',128172)]:
 with urllib.request.urlopen(url,timeout=15) as r:
  data=r.read(cap+1);assert len(data)<=cap
  receipt['downloads'].append({'url':url,'resolved_url':r.url,'status':r.status,'bytes':len(data),'sha256':hashlib.sha256(data).hexdigest(),'utc':datetime.now(timezone.utc).isoformat()})
 (root/name).write_bytes(data)
data=(root/'less.deb').read_bytes();assert len(data)==128172 and hashlib.sha256(data).hexdigest()=='eb430d92921f98b163031ee3ac81a96110d1f20bd84f67faab06dad50c75d744'
assert data[:8]==b'!<arch>\n';pos=8;members={}
while pos<len(data):
 h=data[pos:pos+60];assert len(h)==60 and h[58:]==b'`\n';size=int(h[48:58]);name=h[:16].decode().strip().rstrip('/');pos+=60;members[name]=data[pos:pos+size];pos+=size+(size%2)
assert pos==len(data)
with tarfile.open(fileobj=io.BytesIO(members['control.tar.xz'])) as t:
 control=t.extractfile('./control').read(10000);receipt['control']=control.decode()
with tarfile.open(fileobj=io.BytesIO(members['data.tar.xz'])) as t:
 receipt['package_files']=[{'path':i.name,'size':i.size,'type':i.type.decode()} for i in t]
 t.members=[]
with tarfile.open(fileobj=io.BytesIO(members['data.tar.xz'])) as t:
 for path,dest in [('./usr/bin/less','less'),('./usr/share/doc/less/copyright','copyright')]:
  i=t.getmember(path);assert i.isfile() and i.size<1024*1024;body=t.extractfile(i).read(i.size+1);assert len(body)==i.size
  (root/dest).write_bytes(body);receipt[dest]={'sha256':hashlib.sha256(body).hexdigest(),'size':len(body)}
base='python@sha256:eb5be8e5b4d0a159c237946bbdd06356dda5d19c30fc4f7843e8046d3a590333'
dockerfile=f'FROM {base}\nCOPY --chmod=0755 less /usr/bin/less\nCOPY --chmod=0644 copyright /usr/share/doc/feature-rl-less/copyright\nLABEL feature-rl.repair="debian-less-590-2.1~deb12u2-arm64"\n'
(root/'Dockerfile').write_text(dockerfile);(root/'.dockerignore').write_text('*\n!Dockerfile\n!less\n!copyright\n')
receipt['dockerfile']=dockerfile;receipt['finished_at']=datetime.now(timezone.utc).isoformat()
pathlib.Path('docs/evidence/M3/production/less-acquisition.json').write_text(json.dumps(receipt,indent=2)+'\n')
print(receipt['control']);print(receipt['less'])
