"""One bounded real-Docker synthetic development run; no historical source."""
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib, json, pathlib, threading, time
from feature_rl.artifacts import ArtifactStore
from feature_rl.contracts import ActorRole, DependencyPin, EvidenceRecord, Visibility
from feature_rl.environments import (DockerEngine, EnvironmentRuntime, SandboxPolicy,
    SourceArchive, SourceFile, ExecutionRequest, CommandSpec)
from feature_rl.environments.runtime import WHEELS

here = pathlib.Path(__file__).resolve().parent
root = here / 'close-race-1'
root.mkdir(exist_ok=False)
report = {'question': 'Can close overwrite a successful concurrent saved source?',
          'started_at': datetime.now(timezone.utc).isoformat(),
          'source_hashes': {str(p): hashlib.sha256(p.read_bytes()).hexdigest()
              for p in pathlib.Path('src/feature_rl/environments').glob('*') if p.is_file()}}
engine = None
release = threading.Event()
paused = threading.Event()
closed = {}
thread = None
try:
    store = ArtifactStore(root / 'artifacts', ActorRole.CONTROLLER)
    engine = DockerEngine(state_root=root / 'state',
        socket_path=pathlib.Path.home() / '.docker/run/docker.sock',
        policy=SandboxPolicy(lifecycle_seconds=25.0, cleanup_seconds=8.0))
    report['qualification'] = engine.qualify_boundary()
    runtime = EnvironmentRuntime(store=store, engine=engine, revision='a9fec98ce2fec8cd8bc30c245a1100156532e847')
    source = SourceArchive({'pyproject.toml': SourceFile(
        b'[project]\nname="click"\nversion="8.3.3"\n[build-system]\nrequires=["flit_core>=3.11,<4"]\nbuild-backend="flit_core.buildapi"\n', False),
        'src/click/__init__.py': SourceFile(b'x=1\n', False)})
    baseline = store.put_bytes(source.to_tar(), 'source-archive', Visibility.AUTHORING)
    pins = []
    for name, (version, filename, digest) in WHEELS.items():
        data = pathlib.Path('.feature-rl/research/M3/dependencies', filename).read_bytes()
        assert hashlib.sha256(data).hexdigest() == digest
        pins.append(DependencyPin(name=name, version=version, sha256=digest,
            artifact=store.put_bytes(data, 'dependency-wheel', Visibility.AUTHORING)))
    evidence = EvidenceRecord(producer='independent synthetic close-race diagnostic',
        command=('construct inert synthetic Click source',), recorded_at=datetime.now(timezone.utc),
        exit_status=0, artifacts=(baseline,), revision=runtime.revision, scope='unit_diagnostic')
    prepared = runtime.create_click_recipe(baseline, tuple(pins), source_evidence=evidence)
    handle = runtime.open_workspace(prepared)
    old = runtime.workspace(handle)[3]
    original_workspace = runtime.workspace
    def intercept_workspace(h):
        value = original_workspace(h)
        if threading.current_thread().name == 'paused-close':
            paused.set()
            assert release.wait(20), 'bounded diagnostic synchronization expired'
        return value
    runtime.workspace = intercept_workspace
    def close():
        try: closed['saved'] = runtime.close(handle).model_dump(mode='json')
        except BaseException as exc: closed['error'] = repr(exc)
    thread = threading.Thread(target=close, name='paused-close')
    thread.start()
    assert paused.wait(3)
    result = runtime.execute_development(handle, ExecutionRequest(command=CommandSpec(
        argv=('python', '-c', "import pathlib;pathlib.Path('/workspace/source/src/click/__init__.py').write_text('x=42\\n')"),
        working_directory='/workspace', timeout_seconds=2.0)))
    assert result.reason == 'completed' and result.save_status == 'saved' and result.cleanup_verified
    release.set()
    thread.join(3)
    assert not thread.is_alive() and 'error' not in closed
    state = engine.state.read('workspace-' + handle.workspace_id + '.json')
    report.update(old_saved=old.model_dump(mode='json'),
        execution=json.loads(result.model_dump_json()), close_result=closed,
        final_workspace=state,
        reproduced=state['saved']['artifact'] == old.artifact.model_dump(mode='json')
            and state['saved']['artifact'] != result.saved_source.artifact.model_dump(mode='json'),
        execution_evidence=json.loads(runtime.read_bytes(result.evidence, 32*1024*1024)))
    assert report['reproduced']
finally:
    release.set()
    if thread: thread.join(3)
    if engine:
        report['remaining_recovery'] = engine.recover_owned()
        records = [engine.state.read(n) for n in engine.state.names() if n.startswith('operation-')]
        report['all_owned_operations_removed'] = all(x['phase'] == 'removed' for x in records)
    report['finished_at'] = datetime.now(timezone.utc).isoformat()
    (here / 'close-race-result.json').write_text(json.dumps(report, indent=2) + '\n')
print(json.dumps({k: report[k] for k in ['reproduced', 'all_owned_operations_removed', 'remaining_recovery']}))
