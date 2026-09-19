"""Bounded B-only diagnosis through production runtime; no host source execution."""
import pathlib,json,base64
from feature_rl.artifacts import ArtifactStore,canonical_json
from feature_rl.contracts import ActorRole,CommandSpec
from feature_rl.environments import DockerEngine,SandboxPolicy,EnvironmentRuntime,ExecutionRequest,BuildResult,WorkspaceHandle
root=pathlib.Path('.feature-rl/research/M3/production/click-initial-1')
engine=DockerEngine(state_root=root/'state',socket_path=pathlib.Path.home()/'.docker/run/docker.sock',policy=SandboxPolicy());engine.qualify_boundary()
runtime=EnvironmentRuntime(store=ArtifactStore(root/'artifacts',ActorRole.CONTROLLER),engine=engine,revision='8f97f83b4d59db73c43f1a7a0507d35f5b1edaeb')
report=json.loads(pathlib.Path('docs/evidence/M3/production/click-initial-1.json').read_text());build=BuildResult.model_validate_json(canonical_json(report['baseline_build']))
name=next((root/'state').glob('workspace-*.json'));handle=WorkspaceHandle(workspace_id=json.loads(name.read_text())['workspace_id'])
code="""import subprocess,sys,pathlib,json,os,shutil
print(json.dumps({'less':shutil.which('less'),'tmp':list(os.statvfs('/tmp')),'workspace':list(os.statvfs('/workspace'))}),flush=True)
r=subprocess.run([sys.executable,'-m','pytest','tests/test_utils.py::test_echo_via_pager[test0-less]','-x','-v','--tb=long','--basetemp=/tmp/pytest','-o','cache_dir=/tmp/pytest-cache','--junitxml=/workspace/diagnostic.xml'])
p=pathlib.Path('/workspace/diagnostic.xml');print('JUNIT',p.read_text() if p.exists() else 'MISSING',flush=True)
print('RESOURCE',list(os.statvfs('/tmp')),open('/sys/fs/cgroup/memory.events').read(),flush=True)
sys.exit(r.returncode)
"""
r=runtime.execute(handle,ExecutionRequest(command=CommandSpec(argv=('python','-c',code),working_directory='/workspace/source',timeout_seconds=30.0)),build=build)
v=r.model_dump(mode='python');v['stdout']=base64.b64encode(r.stdout).decode();v['stderr']=base64.b64encode(r.stderr).decode()
pathlib.Path('docs/evidence/M3/production/baseline-diagnosis-1.json').write_bytes(canonical_json(v));print(r.stdout.decode(errors='replace'));print(r.stderr.decode(errors='replace'));print(r.reason,r.cleanup_verified)
