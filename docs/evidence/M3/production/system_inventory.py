from pathlib import Path
import json
from feature_rl.environments import DockerEngine,SandboxPolicy,CommandSpec
engine=DockerEngine(state_root=Path('.feature-rl/research/M3/production/system-inventory'),socket_path=Path.home()/'.docker/run/docker.sock',policy=SandboxPolicy())
with engine.session(binding={'purpose':'trusted-system-inventory'},saved_source={}) as s:
 r=s.execute(CommandSpec(argv=('python','-I','-c',"import pathlib,subprocess;print(pathlib.Path('/etc/os-release').read_text());print(subprocess.run(['dpkg-query','-W','-f=${Package} ${Version} ${Architecture}\\n','libc6','libtinfo6'],capture_output=True,text=True).stdout)"),working_directory='/workspace',timeout_seconds=3.0))
 print(r.stdout.decode());print(r.stderr.decode());print(r.exit_code)
print('cleanup',s.cleanup_verified)
