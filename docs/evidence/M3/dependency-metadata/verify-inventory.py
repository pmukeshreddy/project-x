"""Use already installed trusted packaging only to parse inert wheel metadata."""
import datetime,hashlib,json,pathlib
import packaging
from packaging.markers import Marker
from packaging.requirements import Requirement
from packaging.specifiers import SpecifierSet
from packaging.utils import canonicalize_name,parse_wheel_filename
from packaging.version import Version
p=pathlib.Path('.feature-rl/research/M3/dependencies');inv=json.loads(p.joinpath('inventory.json').read_text())
rows=inv['selected']+[inv['backend']]; byname={x['name']:x for x in rows}; env=inv['target']; decisions=[]
assert len(rows)==6
for item in rows:
 data=p.joinpath(item['filename']).read_bytes()
 assert len(data)==item['bytes'] and hashlib.sha256(data).hexdigest()==item['sha256']
 name,version,build,tags=parse_wheel_filename(item['filename'])
 assert canonicalize_name(name)==item['name'] and str(version)==item['version']
 assert {str(tag) for tag in tags}=={'py3-none-any'}
 assert Version(env['python_full_version']) in SpecifierSet(item['requires_python'])
 for raw in item['requires_dist']:
  req=Requirement(raw);active=req.marker is None or req.marker.evaluate(env)
  decision={'package':item['name'],'requirement':raw,'active':active}
  if active:
   dependency=byname[canonicalize_name(req.name)]
   assert Version(dependency['version']) in req.specifier
   decision['selected']=dependency['version']
  decisions.append(decision)
for item in inv['selected']:
 for dep in item['lock_record'].get('dependencies',[]):
  active='marker' not in dep or Marker(dep['marker']).evaluate(env)
  decisions.append({'lock_parent':item['name'],'lock_requirement':dep,'active':active})
  if active:assert dep['name'] in byname
assert Version('3.11.0') in SpecifierSet('>=3.11,<4')
record={'status':'pass_static_integrity_and_dependency_closure_only','utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'parser':{'name':'packaging','version':packaging.__version__,'origin':packaging.__file__,'note':'Existing trusted host parser; acquired wheels were not imported or installed.'},'target':env,'decisions':decisions,'wheel_count':6,'wheel_bytes':sum(x['bytes'] for x in rows),'runtime_execution':False}
p.joinpath('verification.json').write_text(json.dumps(record,indent=2)+'\n')
requirements=''.join(f"{x['name']}=={x['version']} --hash=sha256:{x['sha256']}\n" for x in rows)
p.joinpath('proposed-requirements.txt').write_text(requirements)
print(record['status'],'wheels',record['wheel_count'],'bytes',record['wheel_bytes'])
print(requirements)
