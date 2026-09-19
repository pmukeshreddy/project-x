"""Real synthetic cleanup fault between recovery and terminal admission; no history."""
from contextlib import contextmanager
from datetime import datetime,timezone
import hashlib,json,pathlib,threading,time
from feature_rl.artifacts import ArtifactStore
from feature_rl.contracts import ActorRole,DependencyPin,EvidenceRecord,Visibility
from feature_rl.environments import (DockerEngine,EnvironmentRuntime,SandboxPolicy,
 SourceArchive,SourceFile,ExecutionRequest,CommandSpec,CleanupUnverified)
from feature_rl.environments.docker import DockerSession
from feature_rl.environments.runtime import WHEELS

out=pathlib.Path('docs/evidence/M3/fix-round2')
root=pathlib.Path('.feature-rl/research/M3/fix-round2/terminal-cleanup-1');root.mkdir(parents=True,exist_ok=False)
start=time.monotonic();engine=None
report={'started_at':datetime.now(timezone.utc).isoformat(),'source_hashes':{str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in pathlib.Path('src/feature_rl/environments').glob('*') if p.is_file()},'driver_sha256':hashlib.sha256(pathlib.Path(__file__).read_bytes()).hexdigest(),'cases':[],'status':'running'}
try:
 store=ArtifactStore(root/'artifacts',ActorRole.CONTROLLER)
 engine=DockerEngine(state_root=root/'state',socket_path=pathlib.Path.home()/'.docker/run/docker.sock',policy=SandboxPolicy(lifecycle_seconds=25.0,cleanup_seconds=8.0))
 report['qualification']=engine.qualify_boundary()
 runtime=EnvironmentRuntime(store=store,engine=engine,revision='ad154f88013200d9c0134b808c9211032ed67a2b')
 source=SourceArchive({'pyproject.toml':SourceFile(b'[project]\nname="click"\nversion="8.3.3"\n[build-system]\nrequires=["flit_core>=3.11,<4"]\nbuild-backend="flit_core.buildapi"\n',False),'src/click/__init__.py':SourceFile(b'x=1\n',False)})
 baseline=store.put_bytes(source.to_tar(),'source-archive',Visibility.AUTHORING);pins=[]
 for name,(version,filename,digest) in WHEELS.items():
  data=pathlib.Path('.feature-rl/research/M3/dependencies',filename).read_bytes();assert hashlib.sha256(data).hexdigest()==digest
  pins.append(DependencyPin(name=name,version=version,sha256=digest,artifact=store.put_bytes(data,'dependency-wheel',Visibility.AUTHORING)))
 evidence=EvidenceRecord(producer='M3 protected terminal cleanup diagnostic',command=('construct inert synthetic Click source',),recorded_at=datetime.now(timezone.utc),exit_status=0,artifacts=(baseline,),revision=runtime.revision,scope='unit_diagnostic')
 prepared=runtime.create_click_recipe(baseline,tuple(pins),source_evidence=evidence)
 def inspect(cid):
  status,body=engine.http('GET','/containers/'+cid+'/json',deadline=time.monotonic()+2,cap=1024*1024)
  value=json.loads(body);return {'utc':datetime.now(timezone.utc).isoformat(),'status':status,'id':value.get('Id'),'state':value.get('State'),'message':value.get('message')}
 for transition in ('close','reset'):
  handle=runtime.open_workspace(prepared);initial=runtime.workspace(handle)[3]
  paused=threading.Event();release=threading.Event();outcome={};original_lock=engine.state.lock
  case={'transition':transition,'started_at':datetime.now(timezone.utc).isoformat(),'initial':initial.model_dump(mode='json')};report['cases'].append(case)
  @contextmanager
  def scheduled_lock():
   if threading.current_thread().name=='terminal':
    outcome['lock_calls']=outcome.get('lock_calls',0)+1
    if outcome['lock_calls']==2:
     paused.set();assert release.wait(10),'bounded synchronization expired'
   with original_lock():yield
  def terminal():
   try:outcome['saved']=getattr(runtime,transition)(handle)
   except BaseException as exc:outcome['error']=exc
  original_cleanup=DockerSession.cleanup;original_base=engine.base
  def unavailable_cleanup(session):
   session.engine.base=[*original_base[:3],'--host','unix:///tmp/feature-rl-definitely-missing.sock']
   try:return original_cleanup(session)
   finally:session.engine.base=original_base
  engine.state.lock=scheduled_lock;thread=threading.Thread(target=terminal,name='terminal');thread.start()
  try:
   assert paused.wait(3)
   DockerSession.cleanup=unavailable_cleanup
   try:
    result=runtime.execute_development(handle,ExecutionRequest(command=CommandSpec(argv=('python','-c',"import pathlib;pathlib.Path('/workspace/source/src/click/__init__.py').write_text('x=42\\n')"),working_directory='/workspace',timeout_seconds=2.0)))
   finally:DockerSession.cleanup=original_cleanup
   assert result.failure_category=='infrastructure' and not result.cleanup_verified and result.save_status=='saved'
   case['execution']=json.loads(result.model_dump_json());case['execution_evidence']=json.loads(runtime.read_bytes(result.evidence,32*1024*1024))
   pending=engine.state.read('operation-'+result.operation_id+'.json');case['pending_before_terminal']=pending
   assert pending['phase']=='cleanup_pending'
   case['live_before_terminal']=inspect(pending['container_id']);assert case['live_before_terminal']['status']==200 and case['live_before_terminal']['state']['Running']
   case['workspace_before_terminal']=engine.state.read('workspace-'+handle.workspace_id+'.json')
   release.set();thread.join(3);assert not thread.is_alive()
   assert isinstance(outcome.get('error'),CleanupUnverified) and 'saved' not in outcome
   case['terminal_rejection']={'type':type(outcome['error']).__name__,'message':str(outcome['error'])}
   case['workspace_after_terminal']=engine.state.read('workspace-'+handle.workspace_id+'.json')
   assert case['workspace_after_terminal']==case['workspace_before_terminal']
   case['pending_after_terminal']=engine.state.read('operation-'+result.operation_id+'.json');assert case['pending_after_terminal']['phase']=='cleanup_pending'
   case['live_after_terminal']=inspect(pending['container_id']);assert case['live_after_terminal']['status']==200 and case['live_after_terminal']['state']['Running']
  finally:
   release.set();thread.join(3);engine.state.lock=original_lock;DockerSession.cleanup=original_cleanup;engine.base=original_base
   case['recovery']=runtime.recover_owned()
  assert any(x['operation_id']==result.operation_id and x['cleanup_verified'] for x in case['recovery'])
  case['absent_after_recovery']=inspect(pending['container_id']);assert case['absent_after_recovery']['status']==404
  retried=getattr(runtime,transition)(handle);assert retried==(result.saved_source if transition=='close' else initial)
  case['retry_saved']=retried.model_dump(mode='json');case['workspace_after_retry']=engine.state.read('workspace-'+handle.workspace_id+'.json')
  assert case['workspace_after_retry']['generation']==case['workspace_before_terminal']['generation']+1
  assert case['workspace_after_retry']['closed']==(transition=='close')
  case['remaining_recovery']=runtime.recover_owned();assert case['remaining_recovery']==[]
  case['finished_at']=datetime.now(timezone.utc).isoformat();case['passed']=True
 report['status']='passed'
finally:
 if engine:
  report['remaining_recovery']=engine.recover_owned()
  report['operation_records']=[engine.state.read(n) for n in engine.state.names() if n.startswith('operation-')]
  assert all(x['phase']=='removed' for x in report['operation_records'])
 report['finished_at']=datetime.now(timezone.utc).isoformat();report['wall_seconds']=time.monotonic()-start
 report['cost']={'wall_seconds':report['wall_seconds'],'cpu_seconds':None,'gpu_seconds':None,'usd':None,'measurement':'partial','note':'Local trusted/synthetic diagnostic only; no historical source, downloads, model/native or remote/paid work. Execution costs remain in case receipts.'}
 (out/'terminal-cleanup-result.json').write_text(json.dumps(report,indent=2)+'\n')
print(json.dumps({'status':report['status'],'cases':len(report['cases']),'wall_seconds':report['wall_seconds'],'all_owned_operations_removed':True}))
