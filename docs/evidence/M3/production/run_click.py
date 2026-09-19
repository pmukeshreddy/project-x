"""Real M3 acceptance driver; only controller orchestration/data handling on host."""
from datetime import datetime,timezone
import argparse,base64,hashlib,json,pathlib,sys,time,traceback
from feature_rl.artifacts import ArtifactStore,canonical_json
from feature_rl.contracts import ActorRole,ArtifactRef,DependencyPin,EvidenceRecord,Visibility
from feature_rl.environments import DockerEngine,EnvironmentRuntime,SandboxPolicy,ExecutionRequest,CommandSpec,BuildFailed
from feature_rl.environments.runtime import WHEELS

args=argparse.ArgumentParser();args.add_argument('--attempt',required=True);args.add_argument('--phase',choices=['initial','complete','final-smoke'],default='initial');a=args.parse_args()
if not a.attempt.isascii() or not a.attempt.replace('-','').isalnum():raise ValueError('invalid attempt')
root=pathlib.Path('.feature-rl/research/M3/production')/a.attempt;root.mkdir(parents=True,exist_ok=False)
out=pathlib.Path('docs/evidence/M3/production')/(a.attempt+'.json')
record={'attempt':a.attempt,'phase':a.phase,'started_at':datetime.now(timezone.utc).isoformat(),'revision':'8f97f83b4d59db73c43f1a7a0507d35f5b1edaeb','source_hashes':{str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(pathlib.Path('src/feature_rl/environments').glob('*')) if p.is_file()},'driver_sha256':hashlib.sha256(pathlib.Path(__file__).read_bytes()).hexdigest(),'results':[],'status':'running'}
runtime=None

def save():out.write_text(json.dumps(record,indent=2)+'\n')
def ref(value):return ArtifactRef.model_validate_json(canonical_json(value))
def run(label,handle,build,code=None,pytest=False,timeout=30.0):
    if pytest:
        argv=('python','-m','pytest','-v','--tb=short','--basetemp=/tmp/pytest','-o','cache_dir=/tmp/pytest-cache')
        cwd='/workspace/source'
    else:argv=('python','-c',code);cwd='/workspace'
    result=runtime.execute(handle,ExecutionRequest(command=CommandSpec(argv=argv,working_directory=cwd,timeout_seconds=timeout)),build=build)
    value=result.model_dump(mode='python');value['stdout']=base64.b64encode(value['stdout']).decode();value['stderr']=base64.b64encode(value['stderr']).decode()
    value=json.loads(json.dumps(value,default=lambda x:x.model_dump(mode='json') if hasattr(x,'model_dump') else x.value))
    record['results'].append({'label':label,**value});save()
    print(label,result.reason,result.exit_code,'saved',result.save_status,'cleanup',result.cleanup_verified,flush=True)
    print(result.stdout.decode(errors='replace')[-6000:],result.stderr.decode(errors='replace')[-1500:],flush=True)
    return result

IMPORT_CODE="""import click,pytest,flit_core,pip,sys,json,pathlib
print(json.dumps({'click':click.__file__,'pytest':pytest.__file__,'flit_core':flit_core.__file__,'pip_version':pip.__version__,'sys_path':sys.path,'scratch_present':pathlib.Path('/workspace/source/src/click/_m3_saved_probe.py').exists()},sort_keys=True))
assert click.__file__.startswith('/workspace/site/click/')
assert pytest.__file__.startswith('/workspace/deps/pytest/')
assert flit_core.__file__.startswith('/workspace/deps/flit_core/')
"""
try:
    store=ArtifactStore(root/'artifacts',ActorRole.CONTROLLER)
    engine=DockerEngine(state_root=root/'state',socket_path=pathlib.Path.home()/'.docker/run/docker.sock',policy=SandboxPolicy())
    record['qualification']=engine.qualify_boundary();save()
    runtime=EnvironmentRuntime(store=store,engine=engine,revision=record['revision'])
    old=ArtifactStore(pathlib.Path('.feature-rl/research/M1/production-store'),ActorRole.CONTROLLER)
    intake=json.loads(pathlib.Path('docs/evidence/M1/coordinator-round2-intake-1.json').read_text())
    baseline=ref(intake['authoring_view']['baseline']);pair_ref=ref(intake['source_pair_ref']);reference=ref(intake['reference_ref'])
    for source in [baseline,reference]:
        data=old.get_bytes(source,max_envelope_bytes=24*1024*1024,max_payload_bytes=16*1024*1024)
        assert store.put_bytes(data,source.kind,source.visibility)==source
    pair=old.get_artifact(pair_ref,max_envelope_bytes=512*1024);assert store.put_artifact(pair)==pair_ref
    pins=[]
    for name,(version,filename,digest) in WHEELS.items():
        data=pathlib.Path('.feature-rl/research/M3/dependencies',filename).read_bytes();assert hashlib.sha256(data).hexdigest()==digest
        pins.append(DependencyPin(name=name,version=version,artifact=store.put_bytes(data,'dependency-wheel',Visibility.AUTHORING),sha256=digest))
    evidence=EvidenceRecord(producer='M3 bounded baseline manifest inspection',command=('SourceArchive.read','M0 bounded source read'),recorded_at=datetime.now(timezone.utc),exit_status=0,artifacts=(baseline,),revision=record['revision'],scope='source_inspection')
    prepared=runtime.create_click_recipe(baseline,tuple(pins),source_evidence=evidence);record['prepared']=prepared.model_dump(mode='json');save()
    b=runtime.open_workspace(prepared);bb=runtime.build_snapshot(b);record['baseline_build']=bb.model_dump(mode='json');save()
    r=run('B-import',b,bb,IMPORT_CODE);assert r.reason=='completed' and r.exit_code==0 and r.cleanup_verified
    if a.phase!='final-smoke':
        r=run('B-regression-1',b,bb,pytest=True);assert r.reason=='completed' and r.exit_code==0 and r.cleanup_verified
    if a.phase=='complete':
        h=runtime.open_workspace(prepared,source=reference,role='reference',source_pair=pair_ref);hb=runtime.build_snapshot(h);record['reference_build']=hb.model_dump(mode='json');save()
        for i in range(3):
            r=run('H-import-'+str(i+1),h,hb,IMPORT_CODE);assert r.reason=='completed' and r.exit_code==0 and r.cleanup_verified
        for i in range(2):
            r=run('B-regression-'+str(i+2),b,bb,pytest=True);assert r.reason=='completed' and r.exit_code==0 and r.cleanup_verified
        for i in range(3):
            edited=run('save-'+str(i),b,bb,"import pathlib;pathlib.Path('/workspace/source/src/click/_m3_saved_probe.py').write_text('# confirmed saved source\\n')")
            assert edited.reason=='completed' and edited.save_status=='saved';changed=runtime.build_snapshot(b)
            timed=run('timeout-'+str(i),b,changed,"import pathlib,time;pathlib.Path('/workspace/source/src/click/_m3_saved_probe.py').write_text('# unsaved timed out edit\\n');time.sleep(20)",timeout=.3)
            assert timed.reason=='timeout' and timed.saved_source==edited.saved_source and timed.cleanup_verified
            restored=runtime.reset(b);assert restored.artifact==bb.source
            r=run('reset-'+str(i),b,bb,IMPORT_CODE);assert r.reason=='completed' and b'"scratch_present": false' in r.stdout
        runtime.close(h)
    runtime.close(b);record['remaining_recovery']=runtime.recover_owned();assert record['remaining_recovery']==[]
    record['status']='passed';save()
except BaseException as exc:
    record['status']='failed';record['error']=repr(exc)
    if isinstance(exc,BuildFailed):
        record['build_failure_evidence']=exc.evidence.model_dump(mode='json')
        raw=runtime.read_bytes(exc.evidence,32*1024*1024);failure=json.loads(raw)
        for cmd in failure['commands']:
            if 'stdout_b64' in cmd:
                print('BUILD COMMAND',cmd['argv'],cmd['exit_code'],cmd['reason'],flush=True)
                print(base64.b64decode(cmd['stdout_b64']).decode(errors='replace')[-5000:],base64.b64decode(cmd['stderr_b64']).decode(errors='replace')[-2500:],flush=True)
    traceback.print_exc();save();raise
finally:
    record['finished_at']=datetime.now(timezone.utc).isoformat();save()
