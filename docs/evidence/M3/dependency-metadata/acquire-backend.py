"""Acquire one explicit neutral backend pin as inert bytes; no package execution."""
import datetime,email.parser,hashlib,io,json,pathlib,tarfile,urllib.parse,urllib.request,zipfile
p=pathlib.Path('.feature-rl/research/M3/dependencies')
metadata=json.loads(p.joinpath('flit_core-3.11.0.json').read_text())
assert metadata['info']['version']=='3.11.0'
files=[x for x in metadata['urls'] if x['filename']=='flit_core-3.11.0-py3-none-any.whl']
assert len(files)==1
f=files[0];assert f['packagetype']=='bdist_wheel' and not f['yanked'] and f['size']<1024*1024
url=f['url'];assert urllib.parse.urlsplit(url).scheme=='https' and urllib.parse.urlsplit(url).hostname=='files.pythonhosted.org'
class NoRedirect(urllib.request.HTTPRedirectHandler):
 def redirect_request(self,*args,**kwargs):raise RuntimeError('unapproved redirect')
started=datetime.datetime.now(datetime.timezone.utc).isoformat()
with urllib.request.build_opener(NoRedirect).open(url,timeout=20) as r:
 assert r.status==200
 b=r.read(f['size']+1);headers=dict(r.headers);final=r.url
assert len(b)==f['size'] and hashlib.sha256(b).hexdigest()==f['digests']['sha256']
assert f['digests']['sha256']=='fe464c086f630f106c0fc5001ee377980f45938f03f8f0d03da08a4841748541'
p.joinpath(f['filename']).write_bytes(b)
with zipfile.ZipFile(io.BytesIO(b)) as z:
 assert len(z.infolist())<1000 and sum(x.file_size for x in z.infolist())<2*1024*1024
 def field(suffix):
  names=[x for x in z.infolist() if x.filename=='flit_core-3.11.0'+suffix]
  assert len(names)==1 and names[0].file_size<1024*1024
  return z.read(names[0]).decode()
 coretext=field('.dist-info/METADATA');wheeltext=field('.dist-info/WHEEL')
core=email.parser.Parser().parsestr(coretext);wheel=email.parser.Parser().parsestr(wheeltext)
assert core['Name']=='flit_core' and core['Version']=='3.11.0' and core['Requires-Python']=='>=3.6' and core.get_all('Requires-Dist',[])==[]
assert wheel['Root-Is-Purelib']=='true' and wheel.get_all('Tag')==['py3-none-any']
receipt={'requested_url':url,'final_url':final,'utc':started,'status':200,'headers':headers,'bytes':len(b),'sha256':hashlib.sha256(b).hexdigest(),'path':str(p/f['filename'])}
p.joinpath('backend-wheel-receipt.json').write_text(json.dumps(receipt,indent=2)+'\n')
inv=json.loads(p.joinpath('inventory.json').read_text())
with tarfile.open(inv['baseline_tar'],'r:') as tar:
 assert tar.pax_headers.get('comment')=='19fd4d6e18bc9fce451f92f422696b11169faa57'
 stamp=tar.getmember('pyproject.toml').mtime
 baseline_time=datetime.datetime.fromtimestamp(stamp,datetime.timezone.utc).isoformat()
assert datetime.datetime.fromisoformat(f['upload_time_iso_8601'].replace('Z','+00:00')).timestamp()<stamp
inv['baseline_archive_comment']='19fd4d6e18bc9fce451f92f422696b11169faa57';inv['baseline_archive_member_mtime_utc']=baseline_time
inv['backend']={'name':'flit-core','version':'3.11.0','filename':f['filename'],'url':url,'metadata_url':'https://pypi.org/pypi/flit_core/3.11.0/json','sha256':receipt['sha256'],'bytes':len(b),'requires_python':core['Requires-Python'],'requires_dist':[],'wheel_metadata':wheeltext,'wheel_core_metadata_sha256':hashlib.sha256(coretext.encode()).hexdigest(),'upload_time':f['upload_time_iso_8601'],'retrieved_utc':started,'provenance':'proposed_neutral_reconstruction_pin_not_historical_lock','vendored_metadata_observed':{'name':'tomli','version':'1.2.3','separate_distribution_requirement':False},'selection_rule':'Choose exact stable lower-bound release 3.11.0 satisfying declared flit_core>=3.11,<4, verify release predates baseline archive timestamp, supports Python 3.12, has no runtime dependencies and has a universal wheel.'}
inv['artifact_count']+=2;inv['http_request_count_including_repeated_wheel']=13;inv['repeated_backend_download_bytes']=len(b);inv['wheel_bytes']+=len(b);inv['download_bytes_including_metadata']+=len(b)+p.joinpath('flit_core-3.11.0.json').stat().st_size
inv['status']='acquired_inert_locked_test_wheels_and_proposed_neutral_backend_compatibility_unexecuted'
assert inv['artifact_count']<=20 and inv['download_bytes_including_metadata']<=30*1024*1024
p.joinpath('inventory.json').write_text(json.dumps(inv,indent=2)+'\n')
print('backend',inv['backend']);print('baseline_archive_time',baseline_time);print('download_bytes',inv['download_bytes_including_metadata'],'wheel_bytes',inv['wheel_bytes'],'artifacts',inv['artifact_count'])
