"""Bounded synthetic schedules through real runtime; scheduling wrappers only."""
from contextlib import contextmanager
from datetime import datetime,timezone
import hashlib,json,pathlib,threading,time
from feature_rl.artifacts import ArtifactStore
from feature_rl.contracts import ActorRole,DependencyPin,EvidenceRecord,Visibility
from feature_rl.environments import (DockerEngine,EnvironmentRuntime,SandboxPolicy,
 SourceArchive,SourceFile,ExecutionRequest,CommandSpec,EnvironmentError,PolicyRejected)
from feature_rl.environments.runtime import WHEELS

out=pathlib.Path('docs/evidence/M3/fix-round1')
root=pathlib.Path('.feature-rl/research/M3/fix-round1/interleaving-1');root.mkdir(parents=True,exist_ok=False)
start=time.monotonic();engine=None
report={'started_at':datetime.now(timezone.utc).isoformat(),'source_hashes':{str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in pathlib.Path('src/feature_rl/environments').glob('*') if p.is_file()},'driver_sha256':hashlib.sha256(pathlib.Path(__file__).read_bytes()).hexdigest(),'cases':[],'status':'running'}
try:
 store=ArtifactStore(root/'artifacts',ActorRole.CONTROLLER)
 engine=DockerEngine(state_root=root/'state',socket_path=pathlib.Path.home()/'.docker/run/docker.sock',policy=SandboxPolicy(lifecycle_seconds=25.0,cleanup_seconds=8.0))
 report['qualification']=engine.qualify_boundary()
 runtime=EnvironmentRuntime(store=store,engine=engine,revision='a9fec98ce2fec8cd8bc30c245a1100156532e847')
 source=SourceArchive({'pyproject.toml':SourceFile(b'[project]\nname="click"\nversion="8.3.3"\n[build-system]\nrequires=["flit_core>=3.11,<4"]\nbuild-backend="flit_core.buildapi"\n',False),'src/click/__init__.py':SourceFile(b'x=1\n',False)})
 baseline=store.put_bytes(source.to_tar(),'source-archive',Visibility.AUTHORING);pins=[]
 for name,(version,filename,digest) in WHEELS.items():
  data=pathlib.Path('.feature-rl/research/M3/dependencies',filename).read_bytes();assert hashlib.sha256(data).hexdigest()==digest
  pins.append(DependencyPin(name=name,version=version,sha256=digest,artifact=store.put_bytes(data,'dependency-wheel',Visibility.AUTHORING)))
 evidence=EvidenceRecord(producer='M3 atomic terminal transition diagnostic',command=('construct inert synthetic Click source',),recorded_at=datetime.now(timezone.utc),exit_status=0,artifacts=(baseline,),revision=runtime.revision,scope='unit_diagnostic')
 prepared=runtime.create_click_recipe(baseline,tuple(pins),source_evidence=evidence)
 for transition,schedule in [('close','before-lock'),('reset','before-lock'),('close','after-read'),('reset','after-read'),('reset','overlapping-reset')]:
  handle=runtime.open_workspace(prepared);initial=runtime.workspace(handle)[3]
  paused=threading.Event();release=threading.Event();outcome={};original_lock=engine.state.lock;original_workspace=runtime.workspace
  case={'transition':transition,'schedule':schedule,'started_at':datetime.now(timezone.utc).isoformat(),'initial':initial.model_dump(mode='json')};report['cases'].append(case)
  @contextmanager
  def scheduled_lock():
   if threading.current_thread().name=='terminal':
    outcome['lock_calls']=outcome.get('lock_calls',0)+1
    if outcome['lock_calls']==2 and schedule!='after-read':
     paused.set();assert release.wait(10),'bounded synchronization expired'
   with original_lock():yield
  def scheduled_workspace(h):
   result=original_workspace(h)
   if threading.current_thread().name=='terminal' and schedule=='after-read':
    case['paused_read_generation']=result[0]['generation'];paused.set();assert release.wait(10),'bounded synchronization expired'
   return result
  def terminal():
   try:outcome['saved']=getattr(runtime,transition)(handle)
   except BaseException as exc:outcome['error']=repr(exc)
  engine.state.lock=scheduled_lock;runtime.workspace=scheduled_workspace
  thread=threading.Thread(target=terminal,name='terminal');thread.start()
  try:
   assert paused.wait(3),'terminal never reached synchronization point'
   if schedule=='overlapping-reset':
    runtime.reset(handle);value,actual,recipe,saved,_=runtime.workspace(handle)
    pending=engine.session(binding=runtime.binding(actual,saved,'development',value),saved_source=saved.model_dump(mode='json'))
    case['intermediate']=value
   else:
    try:
     result=runtime.execute_development(handle,ExecutionRequest(command=CommandSpec(argv=('python','-c',"import pathlib;pathlib.Path('/workspace/source/src/click/__init__.py').write_text('x=42\\n')"),working_directory='/workspace',timeout_seconds=2.0)))
    except EnvironmentError as exc:
     assert schedule=='after-read';case['overlapping_rejection']={'type':type(exc).__name__,'message':str(exc)}
    else:
     assert schedule=='before-lock' and result.reason=='completed' and result.save_status=='saved' and result.cleanup_verified
     case['execution']=json.loads(result.model_dump_json());case['execution_evidence']=json.loads(runtime.read_bytes(result.evidence,32*1024*1024))
    case['intermediate']=engine.state.read('workspace-'+handle.workspace_id+'.json')
  finally:
   release.set();thread.join(3);engine.state.lock=original_lock;runtime.workspace=original_workspace
  assert not thread.is_alive() and 'error' not in outcome,outcome
  state=engine.state.read('workspace-'+handle.workspace_id+'.json');case['final']=state;case['terminal_saved']=outcome['saved'].model_dump(mode='json')
  expected=result.saved_source if schedule=='before-lock' and transition=='close' else initial
  assert outcome['saved']==expected and state['saved']==expected.model_dump(mode='json')
  assert state['generation']==case['intermediate']['generation']+1
  assert state['closed']==(transition=='close')
  if schedule=='after-read':assert 'overlapping_rejection' in case
  if schedule=='overlapping-reset':
   try:
    with pending:pass
   except PolicyRejected as exc:case['stale_admission_rejection']=str(exc)
   else:raise AssertionError('stale generation was admitted')
   assert pending.record.container_id is None and pending.cleanup_verified
   case['admission_record']=pending.record.model_dump(mode='json');case['admission_cleanup_receipts']=pending.receipts
  case['remaining_recovery']=runtime.recover_owned();assert case['remaining_recovery']==[]
  case['finished_at']=datetime.now(timezone.utc).isoformat();case['passed']=True
 report['status']='passed'
finally:
 if engine:
  report['remaining_recovery']=engine.recover_owned()
  report['operation_records']=[engine.state.read(n) for n in engine.state.names() if n.startswith('operation-')]
  assert all(x['phase']=='removed' for x in report['operation_records'])
 report['finished_at']=datetime.now(timezone.utc).isoformat();report['wall_seconds']=time.monotonic()-start
 report['cost']={'wall_seconds':report['wall_seconds'],'cpu_seconds':None,'gpu_seconds':None,'usd':None,'measurement':'partial','note':'Local trusted/synthetic diagnostic; no historical source, downloads, native/model or paid/remote compute. Actual execution costs also in case receipts.'}
 (out/'interleaving-result.json').write_text(json.dumps(report,indent=2)+'\n')
print(json.dumps({'status':report['status'],'cases':len(report['cases']),'wall_seconds':report['wall_seconds'],'all_owned_operations_removed':True}))
