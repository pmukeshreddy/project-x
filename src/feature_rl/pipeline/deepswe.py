"""Import official DeepSWE tasks without authoring or replacing their verifier.

The Harbor-format compatibility path uses the existing CAS, SolverView,
inventory, durable state and bounded process runner. Official images supply the
repository/dependency setup; official scripts own patch application and scoring.
It intentionally does not enter the reference-comparison or training services.
"""
from contextlib import contextmanager
import hashlib
import io
import json
from pathlib import Path
import re
import tarfile
import time
import tomllib
import uuid

from feature_rl.artifacts import ArtifactStore, canonical_json
from feature_rl.contracts import ActorRole, ArtifactRef, SolverView, Visibility
from feature_rl.environments.archive import SourceArchive, SourceFile
from feature_rl.environments.docker import StateDirectory, stream_process
from .models import SolverInventory
from .packaging import inventory_entries


CAP = 128 * 1024 * 1024


def digest(data):
    return hashlib.sha256(data).hexdigest()


def ref(value):
    return ArtifactRef.model_validate_json(canonical_json(value))


def official_reward(raw, reason, exit_code):
    """Read the official verdict; an absent/crashed run is never a reward zero."""
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError('duplicate official reward key')
            result[key] = value
        return result
    if reason != 'exited' or exit_code != 0:
        raise ValueError('official verifier did not complete normally')
    value = json.loads(raw, object_pairs_hook=pairs)
    reward = value.get('reward')
    if type(reward) not in (int, float) or reward not in (0, 1):
        raise ValueError('official verifier did not produce reward 0 or 1')
    return int(reward)


def package_solver(store, instruction, baseline, runtime):
    """Positive allowlist: official B archive, verbatim request, public runtime."""
    runtime_bytes = canonical_json(runtime)
    files = {'instruction.md': SourceFile(instruction, False),
             'baseline.tar': SourceFile(baseline, False),
             'runtime_manifest.json': SourceFile(runtime_bytes, False)}
    package = SourceArchive(files).to_tar()
    instruction_ref = store.put_bytes(instruction, 'deepswe-instruction', Visibility.PUBLIC)
    baseline_ref = store.put_bytes(baseline, 'deepswe-baseline', Visibility.PUBLIC)
    runtime_ref = store.put_bytes(runtime_bytes, 'deepswe-runtime', Visibility.PUBLIC)
    package_ref = store.put_bytes(package, 'deepswe-solver-package', Visibility.PUBLIC)
    inventory = SolverInventory(archive=package_ref, archive_sha256=digest(package),
        components=(instruction_ref, baseline_ref, runtime_ref), files=inventory_entries(files))
    inventory_ref = store.put_bytes(canonical_json(inventory.model_dump(mode='json')),
                                   'm6-solver-inventory', Visibility.PUBLIC)
    return SolverView(instruction=instruction_ref, workspace=baseline_ref,
        runtime_manifest=runtime_ref, public_checks=(), inventory=inventory_ref), package_ref


# Hash actual tracked worktree bytes (not merely Git's index). The image digest
# binds ignored dependencies; reset replaces the complete writable container.
SNAPSHOT = r'''
import hashlib,json,os,pathlib,stat,subprocess
root=pathlib.Path('/app'); rows=[]
names=subprocess.check_output(['git','ls-files','--recurse-submodules','-z']).decode().split('\0')
for name in sorted(filter(None,names)):
    p=root/name
    try:
        s=p.lstat()
        # Uninitialized Git submodules are empty directories, not file blobs.
        if stat.S_ISDIR(s.st_mode):
            rows.append([name,'directory']); continue
        data=os.readlink(p).encode() if stat.S_ISLNK(s.st_mode) else p.read_bytes()
        rows.append([name,stat.S_IFMT(s.st_mode),stat.S_IMODE(s.st_mode),hashlib.sha256(data).hexdigest()])
    except FileNotFoundError: rows.append([name,'missing'])
print(json.dumps({'head':subprocess.check_output(['git','rev-parse','HEAD']).decode().strip(),
 'tree_sha256':hashlib.sha256(json.dumps(rows,separators=(',',':')).encode()).hexdigest(),
 'tracked_files':len(rows),
 'status':subprocess.check_output(['git','status','--porcelain','--untracked-files=all']).decode(),
 'reset_probe_present':(root/'.feature-rl-reset-probe').exists(),
 'private_paths_present':[p for p in ['/tests','/solution','/logs/verifier','/logs/artifacts/model.patch','/var/run/docker.sock'] if pathlib.Path(p).exists()]}))
'''


class DeepSWE:
    """Small official-task adapter with build/validate/package and episode IO."""

    def __init__(self, state_root):
        self.state = StateDirectory(Path(state_root).resolve())
        self.store = ArtifactStore(self.state.path/'artifacts', ActorRole.CONTROLLER)
        self.owner = digest(str(self.state.path).encode())

    def _put(self, data, kind, visibility=Visibility.PRIVATE):
        return self.store.put_bytes(data, kind, visibility).model_dump(mode='json')

    def _get(self, value):
        return self.store.get_bytes(ref(value), max_envelope_bytes=2*CAP, max_payload_bytes=CAP)

    def _record(self, task):
        if not re.fullmatch(r'[a-z0-9][a-z0-9-]{0,150}', task):
            raise ValueError('invalid DeepSWE task id')
        return self.state.read('task-'+task+'.json')

    def _save(self, record):
        self.state.write('task-'+record['task_id']+'.json', record)

    def _command(self, argv, *, stdin=b'', timeout=60, cap=CAP, checked=True):
        result = stream_process(['docker', *argv], stdin, time.monotonic()+timeout, cap)
        if checked and (result.reason != 'exited' or result.exit_code != 0):
            raise RuntimeError('Docker '+argv[0]+' failed: '+result.reason+' '+
                               result.stderr.decode(errors='replace')[-1800:])
        return result

    def _inspect(self, image):
        return json.loads(self._command(['image', 'inspect', image]).stdout)[0]

    def _create(self, image, task, role):
        operation = uuid.uuid4().hex
        name = 'feature-rl-ds-'+operation
        record = {'container': name, 'task_id': task['task_id'], 'role': role,
                  'owner': self.owner, 'phase': 'intent', 'image': image}
        self.state.write('container-'+operation+'.json', record)
        limits = task['metadata']['environment']
        args = ['create', '--name', name, '--label', 'feature-rl.deepswe-owner='+self.owner,
                '--platform', task['platform'], '--network', 'none', '--cap-drop', 'ALL',
                '--security-opt', 'no-new-privileges', '--pids-limit', '1024',
                '--cpus', str(limits['cpus']), '--memory', str(limits['memory_mb'])+'m',
                '--memory-swap', str(limits['memory_mb'])+'m',
                '--workdir', '/app', '--entrypoint', '/bin/sleep']
        for key, value in task['metadata']['environment'].get('env', {}).items():
            args.extend(['--env', key+'='+str(value)])
        try:
            self._command([*args, image, 'infinity'])
            self._command(['start', name])
            record['phase'] = 'running'
            self.state.write('container-'+operation+'.json', record)
            return name
        except BaseException:
            self._remove(name)
            raise

    def _remove(self, name):
        operation = name.removeprefix('feature-rl-ds-')
        record = self.state.read('container-'+operation+'.json')
        inspected = self._command(['inspect', name], checked=False)
        if inspected.exit_code == 0:
            value = json.loads(inspected.stdout)[0]
            if value['Config']['Labels'].get('feature-rl.deepswe-owner') != self.owner:
                raise RuntimeError('container ownership mismatch')
            self._command(['rm', '--force', name])
        elif b'No such object' not in inspected.stderr:
            raise RuntimeError('container inspection unavailable')
        absent = self._command(['inspect', name], checked=False)
        if absent.exit_code == 0 or b'No such object' not in absent.stderr:
            raise RuntimeError('container removal not verified')
        record['phase'] = 'removed'
        self.state.write('container-'+operation+'.json', record)

    @contextmanager
    def _container(self, image, task, role):
        name = self._create(image, task, role)
        try:
            yield name
        finally:
            self._remove(name)

    def _copy_bytes(self, name, destination, data):
        temporary = self.state.path/('transfer-'+uuid.uuid4().hex)
        temporary.write_bytes(data)
        # Docker copy can retain the host UID. Keep transferred inputs readable
        # without needing CAP_FOWNER in the container; the parent is private.
        temporary.chmod(0o644)
        try:
            self._command(['cp', str(temporary), name+':'+destination])
        finally:
            temporary.unlink()

    def _exec(self, name, argv, *, timeout=60, checked=True, stdin=b'', cap=CAP):
        return self._command(['exec', '-i', '--workdir', '/app', name, *argv],
                             timeout=timeout, checked=checked, stdin=stdin, cap=cap)

    def snapshot(self, name):
        return json.loads(self._exec(name, ['python3', '-c', SNAPSHOT], timeout=180).stdout)

    def build(self, task_dir):
        task_dir = Path(task_dir).resolve()
        metadata = tomllib.loads((task_dir/'task.toml').read_text())
        task_id = metadata['metadata']['task_id']
        if task_id != task_dir.name or not re.fullmatch(r'[a-z0-9][a-z0-9-]{0,150}', task_id):
            raise ValueError('invalid official task identity')
        if metadata['verifier']['environment_mode'] != 'separate':
            raise ValueError('DeepSWE v1.1 separate verifier task required')
        input_files = {p.relative_to(task_dir).as_posix(): p.read_bytes()
                       for p in task_dir.rglob('*') if p.is_file()}
        input_hash = digest(canonical_json({n:digest(b) for n,b in sorted(input_files.items())}))
        try:
            existing = self._record(task_id)
        except FileNotFoundError:
            existing = None
        if existing:
            if existing['official_input_sha256'] != input_hash:
                raise ValueError('official task bytes changed; use a new state directory')
            self._inspect(existing['image_digest'])
            self._inspect(existing['verifier_image'])
            return existing
        tag = metadata['environment']['docker_image']
        manifest = json.loads(self._command(['manifest', 'inspect', '--verbose', tag], timeout=120).stdout)
        descriptor = manifest['Descriptor']
        platform = descriptor['platform']['os']+'/'+descriptor['platform']['architecture']
        image = tag.rsplit(':', 1)[0]+'@'+descriptor['digest']
        if self._command(['image', 'inspect', image], checked=False).exit_code:
            pulled = self._command(['pull', '--platform', platform, image], timeout=1800)
            self._put(pulled.stdout+pulled.stderr, 'deepswe-build-log')
        image_info = self._inspect(image)
        if image not in image_info.get('RepoDigests', []):
            raise ValueError('official image manifest digest not available locally')
        record = {'version':'deepswe-v1.1-task', 'task_id':task_id, 'metadata':metadata,
                  'official_input_sha256':input_hash, 'platform':platform, 'image_digest':image,
                  'official_files':{n:self._put(b, 'deepswe-official-file') for n,b in input_files.items()},
                  'image_architecture':image_info['Architecture']}
        verifier_base = image
        # Polars' optimized x86 runtime segfaults under ARM-hosted emulation.
        # Its official compat wheel preserves the installed version and API.
        if platform == 'linux/amd64' and self._command(
                ['info', '--format', '{{.Architecture}}']).stdout.strip() in (b'arm64', b'aarch64'):
            with self._container(image, record, 'runtime-compatibility-probe') as name:
                version = self._exec(name, ['python3', '-c',
                    "import importlib.metadata as m; print(next((d.version for d in "
                    "m.distributions() if d.metadata['Name'] == 'polars-runtime-32'), ''))"
                    ]).stdout.decode().strip()
            if version:
                runtime_tag = 'feature-rl-deepswe:'+input_hash[:24]+'-runtime'
                dockerfile = 'FROM '+image+'\nRUN '+json.dumps(['python3', '-m', 'pip',
                    'install', '--no-cache-dir', '--no-deps', 'polars-runtime-compat=='+version])+'\n'
                built = self._command(['build', '--platform', platform, '-t', runtime_tag, '-'],
                                      stdin=dockerfile.encode(), timeout=1800)
                record['runtime_build_log'] = self._put(built.stdout+built.stderr, 'deepswe-build-log')
                record['official_image_digest'] = image
                record['runtime_compatibility'] = {'polars-runtime-compat':version}
                image = record['image_digest'] = self._inspect(runtime_tag)['Id']
                verifier_base = runtime_tag
        # Resolve FROM to the runtime image without changing verifier code.
        # The official verifier code and test files are copied byte-for-byte.
        context = self.state.path/'build-contexts'/task_id
        context.mkdir(parents=True, exist_ok=True)
        for path in (task_dir/'tests').rglob('*'):
            if path.is_file():
                target = context/path.relative_to(task_dir/'tests')
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(path.read_bytes())
        dockerfile = (context/'Dockerfile').read_text()
        dockerfile, count = re.subn(r'^FROM\s+\S+', 'FROM '+verifier_base, dockerfile, count=1, flags=re.M)
        if count != 1:
            raise ValueError('official verifier Dockerfile has no FROM')
        (context/'Dockerfile').write_text(dockerfile)
        verifier_tag = 'feature-rl-deepswe:'+input_hash[:24]
        built = self._command(['build', '--platform', platform, '--network', 'none',
                               '-t', verifier_tag, str(context)], timeout=1800)
        record['build_log'] = self._put(built.stdout+built.stderr, 'deepswe-build-log')
        record['verifier_image'] = self._inspect(verifier_tag)['Id']
        record['verifier_build_context_sha256'] = digest(canonical_json({
            p.relative_to(context).as_posix():digest(p.read_bytes()) for p in context.rglob('*') if p.is_file()}))
        with self._container(image, record, 'baseline-construction') as name:
            snapshot = self.snapshot(name)
            base_commit = self._exec(name, ['git', 'rev-parse', '--verify',
                metadata['metadata']['base_commit_hash']+'^{commit}']).stdout.decode().strip()
            if snapshot['head'] != base_commit:
                raise ValueError('official image HEAD differs from official B commit')
            if snapshot['private_paths_present']:
                raise ValueError('official solver image contains private task material')
            baseline = self._exec(name, ['git', 'archive', '--format=tar', snapshot['head']], timeout=180).stdout
            record['baseline'] = self._put(baseline, 'deepswe-baseline', Visibility.PUBLIC)
            record['baseline_snapshot'] = snapshot
            record['platform_probe'] = json.loads(self._exec(name, ['python3','-c',
                'import json,platform; print(json.dumps({"machine":platform.machine(),"python":platform.python_version()}))']).stdout)
        self._save(record)
        return record

    def grade(self, task_id, patch=b''):
        """Ordinary grading never reads H; the caller supplies only a patch."""
        record = self._record(task_id)
        if len(patch) > CAP:
            raise ValueError('submitted patch exceeds byte limit')
        run_id = uuid.uuid4().hex
        report_dir = self.state.path/'reports'/task_id/run_id
        report_dir.mkdir(parents=True)
        receipt = {'task_id':task_id, 'run_id':run_id,
                   'verifier_image':record['verifier_image'],
                   'official_input_sha256':record['official_input_sha256'],
                   'patch_sha256':digest(patch), 'reward':None}
        with self._container(record['verifier_image'], record, 'official-verifier') as name:
            self._exec(name, ['mkdir', '-p', '/logs/artifacts'])
            self._copy_bytes(name, '/logs/artifacts/model.patch', patch)
            for key, value in record['metadata']['verifier'].get('env', {}).items():
                if key not in record['metadata']['environment'].get('env', {}):
                    raise ValueError('task-specific verifier env needs explicit exec support: '+key)
            result = self._exec(name, ['bash', '/tests/test.sh'],
                timeout=record['metadata']['verifier']['timeout_sec'], checked=False)
            receipt.update(reason=result.reason, exit_code=result.exit_code,
                           wall_seconds=result.wall_seconds,
                           stdout=self._put(result.stdout, 'deepswe-verifier-log'),
                           stderr=self._put(result.stderr, 'deepswe-verifier-log'))
            # Keep reports private. A test failure is a valid official zero;
            # a missing/malformed verdict or a terminated runner is unavailable.
            self._command(['cp', name+':/logs/verifier/.', str(report_dir)], checked=False)
            try:
                raw = (report_dir/'reward.json').read_bytes()
                receipt['reward'] = official_reward(raw, result.reason, result.exit_code)
                receipt['official_result'] = json.loads(raw)
            except (OSError, ValueError) as exc:
                receipt['error'] = str(exc)
        receipt['cleanup_verified'] = True
        receipt_ref = self._put(canonical_json(receipt), 'deepswe-grade-receipt')
        receipt['receipt'] = receipt_ref
        (report_dir/'receipt.json').write_bytes(canonical_json(receipt))
        return receipt

    def reference_patch(self, task_id):
        """Offline positive control only: run the official solve and collect hooks."""
        record = self._record(task_id)
        with self._container(record['image_digest'], record, 'offline-reference') as name:
            self._exec(name, ['mkdir', '-p', '/solution'])
            for path, value in record['official_files'].items():
                if path.startswith('solution/'):
                    parent = str(Path('/'+path).parent)
                    self._exec(name, ['mkdir', '-p', parent])
                    self._copy_bytes(name, '/'+path, self._get(value))
            result = self._exec(name, ['bash', '/solution/solve.sh'], timeout=1800)
            self._put(result.stdout+result.stderr, 'deepswe-reference-log')
            for hook in record['metadata']['verifier']['collect']:
                self._exec(name, ['bash','-lc',hook['command']], timeout=hook['timeout_sec'])
            patch = self._exec(name, ['cat', '/logs/artifacts/model.patch']).stdout
            if not patch:
                raise ValueError('official reference solve/collect produced an empty patch')
        return patch

    def start(self, task_id):
        record = self._record(task_id)
        name = self._create(record['image_digest'], record, 'solver-workspace')
        try:
            # Only this exact public instruction is copied into the workspace.
            self._copy_bytes(name, '/instruction.md', self._get(record['official_files']['instruction.md']))
            episode = uuid.uuid4().hex
            value = {'episode':episode, 'task_id':task_id, 'container':name,
                     'image_digest':record['image_digest'], 'closed':False}
            self.state.write('episode-'+episode+'.json', value)
            return value
        except BaseException:
            self._remove(name)
            raise

    def _episode(self, episode):
        if not re.fullmatch(r'[0-9a-f]{32}', episode):
            raise ValueError('invalid episode id')
        record = self.state.read('episode-'+episode+'.json')
        if record['closed']:
            raise ValueError('episode is closed')
        return record

    def execute(self, episode, argv, timeout=300):
        record = self._episode(episode)
        task = self._record(record['task_id'])
        if not argv or not 0 < timeout <= task['metadata']['agent']['timeout_sec']:
            raise ValueError('command or episode timeout is invalid')
        result = self._exec(record['container'], argv, timeout=timeout, checked=False)
        return {'exit_code':result.exit_code, 'reason':result.reason,
                'stdout':result.stdout.decode(errors='replace'),
                'stderr':result.stderr.decode(errors='replace'), 'wall_seconds':result.wall_seconds}

    def collect(self, episode):
        record = self._episode(episode)
        task = self._record(record['task_id'])
        for hook in task['metadata']['verifier']['collect']:
            self._exec(record['container'], ['bash','-lc',hook['command']], timeout=hook['timeout_sec'])
        return self._exec(record['container'], ['cat','/logs/artifacts/model.patch']).stdout

    def reset(self, episode):
        state = self._episode(episode)
        task = self._record(state['task_id'])
        self._remove(state['container'])
        state['closed'] = True
        self.state.write('episode-'+episode+'.json', state)
        fresh = self.start(state['task_id'])
        self.state.write('episode-'+fresh['episode']+'.json', {**fresh, 'closed':True})
        state.update(container=fresh['container'], closed=False)
        self.state.write('episode-'+episode+'.json', state)
        return state

    def close(self, episode):
        state = self._episode(episode)
        self._remove(state['container'])
        state['closed'] = True
        self.state.write('episode-'+episode+'.json', state)
        return state

    def validate(self, task_id):
        record = self._record(task_id)
        if record.get('validation', {}).get('passed'):
            return record['validation']
        validation = {'passed':False}
        # A real episode exercises the same public image and reset used later.
        episode = self.start(task_id)
        try:
            before = self.snapshot(episode['container'])
            mutate = """from pathlib import Path
import subprocess
names=subprocess.check_output(['git','ls-files','-z']).decode().split('\\0')
p=next(Path(n) for n in names if n and Path(n).is_file() and not Path(n).is_symlink())
p.write_bytes(p.read_bytes()+b'\\nreset probe\\n')
Path('/app/.feature-rl-reset-probe').write_text('dirty')
Path('/tmp/feature-rl-reset-probe').write_text('dirty')
"""
            self._exec(episode['container'], ['python3','-c',mutate])
            dirty = self.snapshot(episode['container'])
            reset = self.reset(episode['episode'])
            after = self.snapshot(reset['container'])
            tmp_absent = self._exec(reset['container'], ['test','!','-e','/tmp/feature-rl-reset-probe'], checked=False).exit_code == 0
            validation['reset'] = {'before':before, 'after':after,
                'mutation_observed':dirty != before,
                'restored':before == after == record['baseline_snapshot'] and dirty != before and tmp_absent}
            if not validation['reset']['restored']:
                raise ValueError('reset did not reproduce the official baseline')
            # Ordinary candidate grading with the official collect hook, on B.
            baseline_patch = self.collect(episode['episode'])
            if baseline_patch:
                raise ValueError('pristine solver workspace unexpectedly produced a patch')
        finally:
            self.close(episode['episode'])
        print(task_id+': reset verified; running official B control', flush=True)
        validation['B'] = self.grade(task_id, baseline_patch)
        record['validation'] = validation
        self._save(record)
        print(task_id+': B='+str(validation['B']['reward'])+'; running official H control', flush=True)
        patch = self.reference_patch(task_id)
        validation['H'] = self.grade(task_id, patch)
        validation['reference_patch'] = self._put(patch, 'deepswe-reference-patch')
        validation['passed'] = validation['B']['reward'] == 0 and validation['H']['reward'] == 1
        record['validation'] = validation
        self._save(record)
        print(task_id+': H='+str(validation['H']['reward']), flush=True)
        return validation

    def package(self, task_id, output):
        record = self._record(task_id)
        validation = record.get('validation', {})
        if not (validation.get('B', {}).get('reward') == 0
                and validation.get('H', {}).get('reward') == 1
                and validation.get('reset', {}).get('restored') is True):
            raise ValueError('packaging requires B=0, H=1 and reset restoration')
        for role, reward in (('B',0),('H',1)):
            receipt = json.loads(self._get(validation[role]['receipt']))
            if (receipt['reward'] != reward or receipt['task_id'] != task_id
                    or receipt['verifier_image'] != record['verifier_image']
                    or receipt['official_input_sha256'] != record['official_input_sha256']):
                raise ValueError('official validation receipt does not match this task')
        metadata = record['metadata']
        runtime = {'version':'deepswe-official-runtime-v1', 'task_id':task_id,
            'repository_url':metadata['metadata']['repository_url'],
            'base_commit':record['baseline_snapshot']['head'],
            'image_digest':record['image_digest'], 'platform':record['platform'],
            'working_directory':'/app', 'instruction_path':'/instruction.md',
            'network':'none', 'tools':'Tools and dependencies preinstalled in the task image',
            'limits':{k:v for k,v in metadata['environment'].items() if k in
                      ('cpus','memory_mb','storage_mb','gpus')},
            'episode_timeout_seconds':metadata['agent']['timeout_sec'],
            'submission':'Commit changes in /app; the official collect hook grades committed changes only.',
            'reset':'Destroy workspace container and recreate from image_digest.',
            'workspace_archive':'baseline.tar contains git archive of the exact base_commit; the image supplies installed dependencies.'}
        instruction = self._get(record['official_files']['instruction.md'])
        baseline = self._get(record['baseline'])
        view, package = package_solver(self.store, instruction, baseline, runtime)
        raw = self.store.get_bytes(package, max_envelope_bytes=2*CAP, max_payload_bytes=CAP)
        # Re-read through the solver role to exercise existing visibility checks.
        reader = ArtifactStore(self.store.root, ActorRole.SOLVER)
        assert reader.get_bytes(view.instruction) == instruction
        with tarfile.open(fileobj=io.BytesIO(raw)) as archive:
            assert set(archive.getnames()) == {'baseline.tar','instruction.md','runtime_manifest.json'}
            assert archive.extractfile('baseline.tar').read() == baseline
        output = Path(output).resolve()
        output.mkdir(parents=True, exist_ok=True)
        target = output/(task_id+'.tar')
        target.write_bytes(raw)
        record['solver_view'] = view.model_dump(mode='json')
        record['package'] = package.model_dump(mode='json')
        record['package_sha256'] = digest(raw)
        record['package_path'] = str(target)
        self._save(record)
        public_record = {'task_id':task_id, 'solver_view':view.model_dump(mode='json'),
            'package':package.model_dump(mode='json'), 'package_sha256':digest(raw)}
        (output/(task_id+'.json')).write_bytes(canonical_json(public_record))
        return {'task_id':task_id, 'package_path':str(target), 'package_sha256':digest(raw),
                'B':0, 'H':1, 'reset_verified':True, 'solver_view':view.model_dump(mode='json')}


def add_cli(commands):
    parser = commands.add_parser('deepswe', help='import and run official DeepSWE environments; no authoring or training')
    parser.add_argument('--state', required=True, help='private controller state directory')
    actions = parser.add_subparsers(dest='action', required=True)
    build = actions.add_parser('build')
    build.add_argument('--task', required=True, help='official task directory')
    for action in ('validate','package','start','grade'):
        command = actions.add_parser(action)
        command.add_argument('--task-id', required=True)
        if action == 'package':
            command.add_argument('--output', required=True)
        if action == 'grade':
            command.add_argument('--patch', help='submitted patch; omitted means unchanged B')
    for action in ('exec','reset','close','collect'):
        command = actions.add_parser(action)
        command.add_argument('--episode', required=True)
        if action == 'exec':
            import argparse
            command.add_argument('--timeout', type=float, default=300)
            command.add_argument('argv', nargs=argparse.REMAINDER)
        if action == 'collect':
            command.add_argument('--output', required=True)


def dispatch_cli(args):
    pipeline = DeepSWE(args.state)
    if args.action == 'build':
        result = pipeline.build(args.task)
        return {k:result[k] for k in ('task_id','image_digest','platform','verifier_image','official_input_sha256')}
    if args.action == 'validate':
        value = pipeline.validate(args.task_id)
        if not value['passed']:
            raise ValueError('official B/H validation failed; inspect private receipts')
        return {'task_id':args.task_id, 'B':value['B']['reward'], 'H':value['H']['reward'], 'reset_verified':value['reset']['restored']}
    if args.action == 'package':
        return pipeline.package(args.task_id, args.output)
    if args.action == 'start':
        return pipeline.start(args.task_id)
    if args.action == 'grade':
        return pipeline.grade(args.task_id, b'' if args.patch is None else Path(args.patch).read_bytes())
    if args.action == 'exec':
        argv = args.argv[1:] if args.argv[:1] == ['--'] else args.argv
        return pipeline.execute(args.episode, argv, args.timeout)
    if args.action == 'collect':
        patch = pipeline.collect(args.episode)
        Path(args.output).write_bytes(patch)
        return {'patch_sha256':digest(patch), 'bytes':len(patch)}
    return getattr(pipeline, args.action)(args.episode)
