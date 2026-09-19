"""Inert acquisition of the exact five locked Click Python 3.12/Linux test wheels."""
import datetime,email.parser,hashlib,io,json,pathlib,tarfile,time,tomllib,urllib.parse,urllib.request,zipfile
ROOT=pathlib.Path('.feature-rl/research/M3/dependencies')
TAR=pathlib.Path('.feature-rl/research/M1/git-archives/click-B-19fd4d6e18bc9fce451f92f422696b11169faa57.tar')
EXPECTED='9de5108a0e639b8e502117d4fbe955cb2dd2cb38319fc853fde3c222c16eb595'
TARGET={'python_full_version':'3.12.14','python_version':'3.12','sys_platform':'linux','platform_system':'Linux','platform_machine':'aarch64','implementation_name':'cpython','extra':''}
VERSIONS={'pytest':'9.0.2','iniconfig':'2.3.0','packaging':'26.0','pluggy':'1.6.0','pygments':'2.20.0'}
RECEIPTS=[];TOTAL=0
class OfficialRedirect(urllib.request.HTTPRedirectHandler):
 def redirect_request(self,req,fp,code,msg,headers,newurl):
  assert urllib.parse.urlsplit(newurl).scheme=='https' and urllib.parse.urlsplit(newurl).hostname in {'pypi.org','files.pythonhosted.org'}
  return super().redirect_request(req,fp,code,msg,headers,newurl)
OPENER=urllib.request.build_opener(OfficialRedirect)
def fetch(url,out,cap):
 global TOTAL
 assert len(RECEIPTS)<20
 parsed=urllib.parse.urlsplit(url)
 assert parsed.scheme=='https' and parsed.hostname in {'pypi.org','files.pythonhosted.org'} and not parsed.username
 started=datetime.datetime.now(datetime.timezone.utc).isoformat();t=time.monotonic()
 with OPENER.open(urllib.request.Request(url,headers={'User-Agent':'feature-rl-M3-inert-acquisition/1','Accept-Encoding':'identity'}),timeout=20) as response:
  assert response.status==200
  length=response.headers.get('Content-Length')
  if length:assert int(length)<=cap
  data=response.read(cap+1);assert len(data)<=cap
  assert TOTAL+len(data)<=30*1024*1024
  record={'requested_url':url,'final_url':response.url,'utc':started,'status':response.status,'headers':dict(response.headers),'bytes':len(data),'sha256':hashlib.sha256(data).hexdigest(),'seconds':round(time.monotonic()-t,4),'path':str(out)}
 out.write_bytes(data);TOTAL+=len(data);RECEIPTS.append(record)
 (ROOT/'http-receipts.json').write_text(json.dumps(RECEIPTS,indent=2)+'\n')
 return data,record
assert TAR.stat().st_size<32*1024*1024
with TAR.open('rb') as f:assert hashlib.file_digest(f,'sha256').hexdigest()==EXPECTED
members={};parsed={}
with tarfile.open(TAR,'r:') as archive:
 for name in ['pyproject.toml','.github/workflows/tests.yaml','uv.lock']:
  entries=[e for e in archive.getmembers() if e.name==name]
  assert len(entries)==1 and entries[0].isfile() and entries[0].size<2*1024*1024
  item=entries[0]; data=archive.extractfile(item).read(item.size+1);assert len(data)==item.size
  members[name]={'bytes':len(data),'sha256':hashlib.sha256(data).hexdigest()}
  if name.endswith('.toml') or name=='uv.lock':parsed[name]=tomllib.loads(data.decode())
lock=parsed['uv.lock']; selected=[]
for name,version in VERSIONS.items():
 entries=[x for x in lock['package'] if x['name']==name]
 assert len(entries)==1 and entries[0]['version']==version
 entry=entries[0]; assert entry['source']=={'registry':'https://pypi.org/simple'}
 wheels=[w for w in entry['wheels'] if urllib.parse.urlsplit(w['url']).path.endswith('-py3-none-any.whl')]
 assert len(wheels)==1,(name,wheels)
 wheel=wheels[0]; filename=urllib.parse.urlsplit(wheel['url']).path.rsplit('/',1)[-1]
 assert wheel['size']<10*1024*1024
 mdbytes,mdreceipt=fetch(f'https://pypi.org/pypi/{name}/{version}/json',ROOT/f'{name}-{version}.json',2*1024*1024)
 metadata=json.loads(mdbytes)
 remote=[x for x in metadata['urls'] if x['filename']==filename];assert len(remote)==1
 remote=remote[0]
 assert remote['url']==wheel['url'] and remote['digests']['sha256']==wheel['hash'].split(':',1)[1] and remote['size']==wheel['size'] and remote['packagetype']=='bdist_wheel' and not remote['yanked']
 data,receipt=fetch(wheel['url'],ROOT/filename,wheel['size'])
 assert len(data)==wheel['size'] and hashlib.sha256(data).hexdigest()==wheel['hash'].split(':',1)[1]
 with zipfile.ZipFile(io.BytesIO(data)) as z:
  assert len(z.infolist())<=4000
  assert sum(i.file_size for i in z.infolist())<=20*1024*1024
  def member(suffix):
   names=[i for i in z.infolist() if i.filename.endswith(suffix)]
   assert len(names)==1 and names[0].file_size<1024*1024
   return z.read(names[0]).decode()
  core_text=member('.dist-info/METADATA'); wheel_text=member('.dist-info/WHEEL')
 core=email.parser.Parser().parsestr(core_text); wheel_header=email.parser.Parser().parsestr(wheel_text)
 assert core['Name'].lower().replace('_','-')==name and core['Version']==version
 assert wheel_header['Root-Is-Purelib']=='true' and wheel_header.get_all('Tag')==['py3-none-any']
 item={'name':name,'version':version,'filename':filename,'lock_record':entry,'metadata_url':mdreceipt['requested_url'],'url':receipt['requested_url'],'sha256':receipt['sha256'],'bytes':len(data),'requires_python':core['Requires-Python'],'requires_dist':core.get_all('Requires-Dist',[]),'wheel_metadata':wheel_text,'wheel_core_metadata_sha256':hashlib.sha256(core_text.encode()).hexdigest(),'metadata_requires_dist':metadata['info'].get('requires_dist'),'metadata_requires_python':metadata['info'].get('requires_python'),'yanked':remote['yanked'],'upload_time':remote['upload_time_iso_8601'],'retrieved_utc':receipt['utc']}
 selected.append(item)
 print(name,version,'bytes',len(data),'requires_python',core['Requires-Python'],'requires_dist',core.get_all('Requires-Dist',[]),flush=True)
flit=[x for x in lock['package'] if x['name']=='flit-core'];assert not flit
inventory={'baseline_tar':str(TAR),'baseline_tar_sha256':EXPECTED,'baseline_tar_bytes':TAR.stat().st_size,'baseline_member_receipts':members,'target':TARGET,'test_group':parsed['pyproject.toml']['dependency-groups']['tests'],'build_system':parsed['pyproject.toml']['build-system'],'flit_core_lock_entries':0,'selected':selected,'artifact_count':len(RECEIPTS),'download_bytes_including_metadata':TOTAL,'wheel_bytes':sum(x['bytes'] for x in selected),'status':'acquired_inert_test_wheels_backend_resolution_unpinned'}
(ROOT/'inventory.json').write_text(json.dumps(inventory,indent=2)+'\n')
print('artifact_count',len(RECEIPTS),'download_bytes',TOTAL,'wheel_bytes',inventory['wheel_bytes'],flush=True)
