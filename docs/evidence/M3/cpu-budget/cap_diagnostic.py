"""One bounded real Docker request cap; synthetic source and cached wheels only."""
from datetime import datetime,timezone
import hashlib,json,pathlib,time
from feature_rl.artifacts import ArtifactStore
from feature_rl.contracts import ActorRole,DependencyPin,EvidenceRecord,Visibility
from feature_rl.environments import (DockerEngine,EnvironmentRuntime,SandboxPolicy,SourceArchive,
 SourceFile,ExecutionRequest,CommandSpec)
from feature_rl.environments.runtime import WHEELS
out=pathlib.Path('docs/evidence/M3/cpu-budget');root=pathlib.Path('.feature-rl/research/M3/cpu-budget/cap-1');root.mkdir(parents=True,exist_ok=False)
start=time.monotonic();engine=None
report={'started_at':datetime.now(timezone.utc).isoformat(),'source_hashes':{str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in pathlib.Path('src/feature_rl/environments').glob('*') if p.is_file()},'driver_sha256':hashlib.sha256(pathlib.Path(__file__).read_bytes()).hexdigest(),'status':'running'}
try:
 store=ArtifactStore(root/'artifacts',ActorRole.CONTROLLER)
 engine=DockerEngine(state_root=root/'state',socket_path=pathlib.Path.home()/'.docker/run/docker.sock',policy=SandboxPolicy())
 report['qualification']=engine.qualify_boundary()
 runtime=EnvironmentRuntime(store=store,engine=engine,revision=hashlib.sha256(json.dumps(report['source_hashes'],sort_keys=True).encode()).hexdigest())
 source=SourceArchive({'pyproject.toml':SourceFile(b'[project]\nname="click"\nversion="8.3.3"\n[build-system]\nrequires=["flit_core>=3.11,<4"]\nbuild-backend="flit_core.buildapi"\n',False),'src/click/__init__.py':SourceFile(b'x=1\n',False)})
 baseline=store.put_bytes(source.to_tar(),'source-archive',Visibility.AUTHORING);pins=[]
 for name,(version,filename,digest) in WHEELS.items():
  data=pathlib.Path('.feature-rl/research/M3/dependencies',filename).read_bytes();assert hashlib.sha256(data).hexdigest()==digest
  pins.append(DependencyPin(name=name,version=version,sha256=digest,artifact=store.put_bytes(data,'dependency-wheel',Visibility.AUTHORING)))
 evidence=EvidenceRecord(producer='M3 synthetic remaining CPU cap diagnostic',command=('construct inert synthetic source',),recorded_at=datetime.now(timezone.utc),exit_status=0,artifacts=(baseline,),revision=runtime.revision,scope='unit_diagnostic')
 prepared=runtime.create_click_recipe(baseline,tuple(pins),source_evidence=evidence);handle=runtime.open_workspace(prepared);saved=runtime.workspace(handle)[3]
 request=ExecutionRequest(command=CommandSpec(argv=('python','-c',"print('REQUESTED_COMMAND_MUST_NOT_START')"),working_directory='/workspace',timeout_seconds=10.0),remaining_cpu_seconds=.05)
 result=runtime.execute_development(handle,request)
 report['request']=json.loads(request.model_dump_json());report['result']=json.loads(result.model_dump_json());report['initial_saved']=saved.model_dump(mode='json')
 receipt=json.loads(runtime.read_bytes(result.evidence,32*1024*1024));report['execution_evidence']=receipt
 assert result.reason=='cpu_limit' and result.failure_category=='candidate' and result.cleanup_verified
 assert result.saved_source==saved and result.save_status=='last_confirmed' and runtime.workspace(handle)[3]==saved
 assert receipt['effective_cpu_seconds']==.05 and receipt['record']['binding']['effective_cpu_seconds']=='0.05'
 assert receipt['maximum_cpu_seconds']>=.05
 assert not any(tuple(c.get('argv',[])[-3:])==request.command.argv for c in receipt['commands'])
 assert receipt['effective']['HostConfig']['NanoCpus']==500000000 and runtime.policy.cpu_seconds==60.0
 report['observed_cpu_overrun_seconds']=receipt['maximum_cpu_seconds']-.05
 runtime.close(handle);report['status']='passed'
finally:
 if engine:
  report['remaining_recovery']=engine.recover_owned();report['operation_records']=[engine.state.read(n) for n in engine.state.names() if n.startswith('operation-')]
  assert all(r['phase']=='removed' for r in report['operation_records'])
 report['finished_at']=datetime.now(timezone.utc).isoformat();report['wall_seconds']=time.monotonic()-start
 (out/'cap-result.json').write_text(json.dumps(report,indent=2)+'\n')
print(json.dumps({'status':report['status'],'reason':report['result']['reason'],'effective_cpu_seconds':report['execution_evidence']['effective_cpu_seconds'],'maximum_cpu_seconds':report['execution_evidence']['maximum_cpu_seconds'],'wall_seconds':report['wall_seconds'],'all_owned_removed':True}))
