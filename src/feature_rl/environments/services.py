"""Fresh loopback services owned by the same isolated container as a command.

No host ports, shared service state, sidecars or external network are admitted.
Container removal is the process teardown and the reset boundary.
"""
import json
import time
from typing import Annotated

from pydantic import Field, model_validator
from feature_rl.artifacts import canonical_json
from feature_rl.contracts import CommandSpec, ServiceRecipe, StrictModel


class LocalService(StrictModel):
    name: Annotated[str, Field(pattern=r'^[a-z][a-z0-9_-]{0,47}$')]
    start: Annotated[tuple[str, ...], Field(min_length=1, max_length=128)]
    readiness: CommandSpec
    ready_stdout: Annotated[str, Field(max_length=4096)]
    startup_seconds: Annotated[float, Field(gt=0, le=30)] = 10.0

    @property
    def state_directory(self):
        return '/workspace/services/' + self.name

    @model_validator(mode='after')
    def validate_commands(self):
        if not self.start[0] or any('\x00' in value or len(value) > 4096 for value in self.start):
            raise ValueError('invalid service start command')
        if self.readiness.working_directory != self.state_directory or self.readiness.timeout_seconds > 5:
            raise ValueError('service readiness must use bounded isolated state')
        return self

    def recipe(self, image):
        # Every actual reset destroys the old container first. This checked
        # command describes initialization of state in the new empty container.
        reset = CommandSpec(argv=('/usr/local/bin/python', '-I', '-c',
            'import pathlib,sys;pathlib.Path(sys.argv[1]).mkdir(parents=True,exist_ok=False)', self.state_directory),
            working_directory='/workspace', timeout_seconds=5.0)
        return ServiceRecipe(name=self.name, image_digest=image, readiness=self.readiness,
                             reset=reset, isolated_state=self.state_directory)


START_SERVICE = r'''
import json,pathlib,subprocess,sys
settings=json.loads(sys.stdin.buffer.read());root=pathlib.Path(settings['state_directory'])
if not root.is_dir() or root.is_symlink():raise RuntimeError('service state was not freshly initialized')
with (root/'stdout.log').open('xb') as out,(root/'stderr.log').open('xb') as err:
 process=subprocess.Popen(settings['start'],cwd=root,stdout=out,stderr=err,start_new_session=True)
status=pathlib.Path('/proc/'+str(process.pid)+'/stat').read_text().rsplit(')',1)[1].split()
print(json.dumps({'name':settings['name'],'pid':process.pid,'started':status[19]},sort_keys=True))
'''

SERVICE_ALIVE = "import pathlib,sys;s=pathlib.Path('/proc/'+sys.argv[1]+'/stat').read_text().rsplit(')',1)[1].split();sys.exit(0 if s[0]!='Z' and s[19]==sys.argv[2] else 1)"


def start_services(runtime, session, image):
    from .runtime import StageFailure
    for service in runtime.profile.services:
        recipe = service.recipe(image)
        result = session.execute(recipe.reset, environment=runtime.profile.environment)
        if result.reason != 'exited' or result.exit_code != 0:
            raise StageFailure('fresh service state initialization failed: ' + service.name, 'setup_failed', 'infrastructure')
        result = session.execute(CommandSpec(argv=('/usr/local/bin/python', '-I', '-c', START_SERVICE),
            working_directory='/workspace', timeout_seconds=5.0),
            canonical_json({'name': service.name, 'state_directory': service.state_directory, 'start': service.start}),
            environment=runtime.profile.environment)
        if result.reason != 'exited' or result.exit_code != 0:
            raise StageFailure('local service launch failed: ' + service.name, 'setup_failed', 'infrastructure')
        observed = json.loads(result.stdout)
        if (observed.get('name') != service.name or type(observed.get('pid')) is not int or observed['pid'] <= 0
                or not isinstance(observed.get('started'), str) or not observed['started'].isdigit()):
            raise StageFailure('invalid local service process receipt', 'setup_failed', 'infrastructure')
        alive_command = CommandSpec(argv=('/usr/local/bin/python', '-I', '-c', SERVICE_ALIVE,
            str(observed['pid']), observed['started']), working_directory='/workspace', timeout_seconds=3.0)
        def require_alive():
            alive = session.execute(alive_command, environment=runtime.profile.environment)
            if alive.reason != 'exited' or alive.exit_code != 0:
                raise StageFailure('local service process exited: ' + service.name, 'setup_failed', 'infrastructure')
        deadline = min(session.deadline, time.monotonic() + service.startup_seconds)
        while True:
            require_alive()
            remaining = deadline-time.monotonic()
            if remaining <= 0:
                raise StageFailure('local service readiness deadline exceeded: ' + service.name, 'setup_failed', 'infrastructure')
            command = service.readiness.model_copy(update={'timeout_seconds': min(remaining, service.readiness.timeout_seconds)})
            result = session.execute(command, environment=runtime.profile.environment)
            if result.reason == 'exited' and result.exit_code == 0 and result.stdout == service.ready_stdout.encode():
                require_alive()
                break
            if result.reason != 'exited':
                raise StageFailure('local service readiness interrupted: ' + service.name,
                                   'infrastructure_failure' if result.reason == 'monitor_failure' else result.reason, 'infrastructure')
            time.sleep(min(0.05, max(0, deadline-time.monotonic())))
