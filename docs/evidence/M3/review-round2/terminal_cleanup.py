"""One bounded synthetic Docker schedule: cleanup fault before close admission."""
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib, json, pathlib, threading
from feature_rl.artifacts import ArtifactStore
from feature_rl.contracts import ActorRole, DependencyPin, EvidenceRecord, Visibility
from feature_rl.environments import (DockerEngine, EnvironmentRuntime, SandboxPolicy,
    SourceArchive, SourceFile, ExecutionRequest, CommandSpec)
from feature_rl.environments.docker import DockerSession
from feature_rl.environments.runtime import WHEELS

here=pathlib.Path(__file__).resolve().parent
root=here/'terminal-cleanup-1';root.mkdir(exist_ok=False)
report={'started_at':datetime.now(timezone.utc).isoformat(),
    'source_hashes':{str(p):hashlib.sha256(p.read_bytes()).hexdigest()
        for p in pathlib.Path('src/feature_rl/environments').glob('*') if p.is_file()}}
engine=None;thread=None;release=threading.Event();paused=threading.Event();outcome={}
try:
    store=ArtifactStore(root/'artifacts',ActorRole.CONTROLLER)
    engine=DockerEngine(state_root=root/'state',socket_path=pathlib.Path.home()/'.docker/run/docker.sock',
        policy=SandboxPolicy(lifecycle_seconds=25.0,cleanup_seconds=8.0))
    report['qualification']=engine.qualify_boundary()
    runtime=EnvironmentRuntime(store=store,engine=engine,revision='ad154f88013200d9c0134b808c9211032ed67a2b')
    source=SourceArchive({'pyproject.toml':SourceFile(b'[project]\nname="click"\nversion="8.3.3"\n[build-system]\nrequires=["flit_core>=3.11,<4"]\nbuild-backend="flit_core.buildapi"\n',False),
        'src/click/__init__.py':SourceFile(b'x=1\n',False)})
    baseline=store.put_bytes(source.to_tar(),'source-archive',Visibility.AUTHORING);pins=[]
    for name,(version,filename,digest) in WHEELS.items():
        data=pathlib.Path('.feature-rl/research/M3/dependencies',filename).read_bytes()
        assert hashlib.sha256(data).hexdigest()==digest
        pins.append(DependencyPin(name=name,version=version,sha256=digest,
            artifact=store.put_bytes(data,'dependency-wheel',Visibility.AUTHORING)))
    evidence=EvidenceRecord(producer='independent terminal cleanup race diagnostic',
        command=('construct inert synthetic source',),recorded_at=datetime.now(timezone.utc),
        exit_status=0,artifacts=(baseline,),revision=runtime.revision,scope='unit_diagnostic')
    prepared=runtime.create_click_recipe(baseline,tuple(pins),source_evidence=evidence)
    handle=runtime.open_workspace(prepared)
    original_lock=engine.state.lock
    @contextmanager
    def scheduled_lock():
        if threading.current_thread().name=='terminal-close':
            outcome['lock_calls']=outcome.get('lock_calls',0)+1
            if outcome['lock_calls']==2:
                paused.set();assert release.wait(15),'bounded synchronization expired'
        with original_lock():yield
    def close():
        try:outcome['saved']=runtime.close(handle).model_dump(mode='json')
        except BaseException as exc:outcome['error']=repr(exc)
    engine.state.lock=scheduled_lock
    thread=threading.Thread(target=close,name='terminal-close');thread.start();assert paused.wait(3)
    original_cleanup=DockerSession.cleanup;original_base=engine.base
    def unavailable_cleanup(session):
        session.engine.base=[*original_base[:3],'--host','unix:///tmp/feature-rl-definitely-missing.sock']
        try:return original_cleanup(session)
        finally:session.engine.base=original_base
    DockerSession.cleanup=unavailable_cleanup
    try:
        result=runtime.execute_development(handle,ExecutionRequest(command=CommandSpec(
            argv=('python','-c','pass'),working_directory='/workspace',timeout_seconds=2.0)))
    finally:DockerSession.cleanup=original_cleanup
    assert result.failure_category=='infrastructure' and not result.cleanup_verified
    report['execution']=json.loads(result.model_dump_json())
    report['execution_evidence']=json.loads(runtime.read_bytes(result.evidence,32*1024*1024))
    release.set();thread.join(3);assert not thread.is_alive()
    engine.state.lock=original_lock
    pending=engine.state.read('operation-'+result.operation_id+'.json')
    state=engine.state.read('workspace-'+handle.workspace_id+'.json')
    status,body=engine.http('GET','/containers/'+pending['container_id']+'/json',
        deadline=__import__('time').monotonic()+2,cap=1024*1024)
    inspection=json.loads(body)
    report.update(close_result=outcome,final_workspace=state,pending_after_close=pending,
        inspect_after_close={'status':status,'id':inspection.get('Id'),'state':inspection.get('State')})
    report['reproduced']='saved' in outcome and 'error' not in outcome and state['closed'] and pending['phase']=='cleanup_pending' and status==200 and inspection['State']['Running']
    assert report['reproduced']
finally:
    release.set()
    if thread:thread.join(3)
    if engine:
        report['final_recovery']=engine.recover_owned()
        report['all_owned_operations_removed']=all(engine.state.read(n)['phase']=='removed'
            for n in engine.state.names() if n.startswith('operation-'))
        report['remaining_recovery']=engine.recover_owned()
    report['finished_at']=datetime.now(timezone.utc).isoformat()
    (here/'terminal-cleanup-result.json').write_text(json.dumps(report,indent=2)+'\n')
print(json.dumps({k:report[k] for k in ['reproduced','all_owned_operations_removed','remaining_recovery']}))
