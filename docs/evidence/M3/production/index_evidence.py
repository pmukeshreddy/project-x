"""Bounded receipt verification and final source/evidence inventory; inert reads only."""
from datetime import datetime,timezone
import hashlib,json,pathlib
from feature_rl.artifacts import ArtifactStore,canonical_json
from feature_rl.contracts import ArtifactRef,ActorRole
p=pathlib.Path('docs/evidence/M3/production')
records=[]
for name in ['click-initial-1','click-repair1-complete','click-final-smoke']:
 report=json.loads((p/(name+'.json')).read_text());store=ArtifactStore(pathlib.Path('.feature-rl/research/M3/production')/name/'artifacts',ActorRole.CONTROLLER)
 refs=[x['evidence'] for x in report['results']]
 refs += [report[k]['evidence'] for k in ['baseline_build','reference_build'] if k in report]
 verified=[]
 for raw in refs:
  ref=ArtifactRef.model_validate_json(canonical_json(raw));data=store.get_bytes(ref,max_envelope_bytes=48*1024*1024,max_payload_bytes=32*1024*1024)
  value=json.loads(data);assert value['cleanup_verified'] and value['record']['phase']=='removed'
  verified.append({'ref':raw,'payload_sha256':hashlib.sha256(data).hexdigest(),'bytes':len(data),'phase':value['phase'],'operation_id':value['record']['operation_id'],'binding':value['record']['binding'],'cleanup_verified':True,'maximum_cpu_seconds':value['maximum_cpu_seconds'],'maximum_memory_bytes':value['maximum_memory_bytes']})
 records.append({'attempt':name,'status':report['status'],'started_at':report['started_at'],'finished_at':report['finished_at'],'verified_receipts':verified,'result_wall_seconds_sum':sum(x['cost']['wall_seconds'] or 0 for x in report['results']),'result_cpu_seconds_sum':sum(x['cost']['cpu_seconds'] or 0 for x in report['results'])})
assert (p/'final-owned-containers.log').read_bytes()==b''
r={'created_at':datetime.now(timezone.utc).isoformat(),'assigned_base':'8f97f83b4d59db73c43f1a7a0507d35f5b1edaeb','full_suite':{'command':['env','PYTHONPATH=src','.venv/bin/pytest','-q'],'exit_status':0,'passed':337,'pytest_wall_seconds':69.16,'log':'full-suite.log','start_capture_utc':datetime.fromtimestamp((p/'full-suite.log').stat().st_birthtime,timezone.utc).isoformat(),'finish_capture_utc':datetime.fromtimestamp((p/'full-suite.log').stat().st_mtime,timezone.utc).isoformat(),'timestamp_source':'filesystem log creation/final-write observations; duration reported by pytest'},'source_hashes':{str(x):hashlib.sha256(x.read_bytes()).hexdigest() for x in sorted(pathlib.Path('src/feature_rl/environments').glob('*')) if x.is_file()},'test_hashes':{str(x):hashlib.sha256(x.read_bytes()).hexdigest() for x in sorted(pathlib.Path('tests').glob('test_environments*.py'))},'records':records,'owned_containers_remaining':0,'inventory_command':['docker','ps','-aq','--filter','label=feature-rl.owner'],'cost_policy':'local execution only; CPU/wall partial observations; no model/GPU/remote compute or paid spend. Runtime USD remains unknown, never fabricated from wall time. Public package acquisition charged $0.','evidence_files':[{'path':str(x),'bytes':x.stat().st_size,'sha256':hashlib.sha256(x.read_bytes()).hexdigest(),'modified_at':datetime.fromtimestamp(x.stat().st_mtime,timezone.utc).isoformat()} for x in sorted(p.iterdir()) if x.is_file() and x.name not in {'index.json','index-evidence.log'}]}
(p/'index.json').write_text(json.dumps(r,indent=2)+'\n');print('verified',sum(len(x['verified_receipts']) for x in records),'receipts; no owned containers; source/test inventory recorded')
