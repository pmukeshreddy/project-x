"""Real required Docker checks. Missing Docker is a failure, never a skip."""
import json
from pathlib import Path
import pytest

@pytest.fixture
def engine(tmp_path):
    from feature_rl.environments import DockerEngine,SandboxPolicy
    return DockerEngine(state_root=tmp_path/'runtime',socket_path=Path.home()/'.docker/run/docker.sock',policy=SandboxPolicy(lifecycle_seconds=15.0,cleanup_seconds=8.0,memory_bytes=128*1024*1024,pids=32,disk_bytes=32*1024*1024))


def test_actual_denials_are_machine_checked(engine):
    result=engine.qualify_boundary()
    assert result['qualified'] is True
    assert result['cleanup_verified'] is True
    assert result['observations']['uid']==65534
    assert result['observations']['seccomp']==2
    assert result['observations']['ipv4_errno']==101
    assert result['observations']['ptrace_errno']==1
    assert result['observations']['memory_oom_delta']>=1


def test_stalled_stdin_and_closed_output_do_not_escape_deadline(engine):
    from feature_rl.environments import CommandSpec
    with engine.session(binding={'test':'blocked-stdin'},saved_source={}) as session:
        result=session.execute(CommandSpec(argv=('python','-I','-c','import os,time;os.close(1);os.close(2);time.sleep(30)'),working_directory='/workspace',timeout_seconds=0.5),b'x'*500000)
        assert result.reason=='timeout'
    assert session.cleanup_verified
    assert engine.recover_owned()==[]


def test_export_preserves_tmpfs_source_as_validated_capture(engine):
    from feature_rl.environments import CommandSpec,SourceArchive
    with engine.session(binding={'test':'export'},saved_source={}) as session:
        result=session.execute(CommandSpec(argv=('python','-I','-c',"import pathlib; p=pathlib.Path('/workspace/source/src');p.mkdir(parents=True);(p/'a.py').write_text('saved')"),working_directory='/workspace',timeout_seconds=2.0))
        assert result.exit_code==0
        source=session.export_source()
        assert source.files['src/a.py'].data==b'saved'
    assert session.cleanup_verified


def test_output_overflow_kills_container_not_only_attach_client(engine):
    from feature_rl.environments import CommandSpec
    with engine.session(binding={'test':'overflow'},saved_source={}) as session:
        result=session.execute(CommandSpec(argv=('python','-I','-c',"import os;\nwhile True: os.write(1,b'x'*8192)"),working_directory='/workspace',timeout_seconds=3.0),output_limit=16384)
        assert result.reason=='output_limit'
        assert len(result.stdout)+len(result.stderr)==16384
    assert session.cleanup_verified


def test_missing_daemon_is_not_verified_absence(tmp_path):
    from feature_rl.environments import DockerEngine,SandboxPolicy,DockerUnavailable
    with pytest.raises(DockerUnavailable):DockerEngine(state_root=tmp_path/'r',socket_path=tmp_path/'missing.sock',policy=SandboxPolicy())


def test_actual_memory_exhaustion_remains_inside_cgroup(engine):
    from feature_rl.environments import CommandSpec
    with engine.session(binding={'test':'memory'},saved_source={}) as session:
        result=session.execute(CommandSpec(argv=('python','-I','-c','a=bytearray(256*1024*1024)'),working_directory='/workspace',timeout_seconds=3.0))
        assert result.exit_code!=0
        events=session.execute(CommandSpec(argv=('python','-I','-c',"print(open('/sys/fs/cgroup/memory.events').read())"),working_directory='/workspace',timeout_seconds=2.0))
        assert 'oom_kill 1' in events.stdout.decode()
    assert session.cleanup_verified

@pytest.fixture
def runtime_fixture(engine,tmp_path):
    from datetime import datetime,timezone
    from feature_rl.artifacts import ArtifactStore
    from feature_rl.contracts import ActorRole,Visibility,DependencyPin,EvidenceRecord
    from feature_rl.environments import EnvironmentRuntime
    from feature_rl.environments.runtime import WHEELS
    import io,tarfile
    store=ArtifactStore(tmp_path/'artifacts',ActorRole.CONTROLLER)
    data=io.BytesIO()
    with tarfile.open(fileobj=data,mode='w') as t:
        for name,body in [('pyproject.toml',b'[project]\nname="click"\nversion="8.3.3"\n[build-system]\nrequires=["flit_core>=3.11,<4"]\nbuild-backend="flit_core.buildapi"\n'),('src/click/__init__.py',b'x=1\n')]:
            item=tarfile.TarInfo(name);item.size=len(body);t.addfile(item,io.BytesIO(body))
    baseline=store.put_bytes(data.getvalue(),'source-archive',Visibility.AUTHORING)
    pins=[]
    for name,(version,filename,digest) in WHEELS.items():
        body=Path('.feature-rl/research/M3/dependencies',filename).read_bytes()
        ref=store.put_bytes(body,'dependency-wheel',Visibility.AUTHORING)
        pins.append(DependencyPin(name=name,version=version,artifact=ref,sha256=digest))
    evidence=EvidenceRecord(producer='trusted-test-fixture',command=('create synthetic inert source',),recorded_at=datetime.now(timezone.utc),exit_status=0,artifacts=(baseline,),revision='a'*40,scope='unit_diagnostic')
    engine.qualify_boundary()
    runtime=EnvironmentRuntime(store=store,engine=engine,revision='a'*40)
    prepared=runtime.create_click_recipe(baseline,tuple(pins),source_evidence=evidence)
    return runtime,prepared


def test_saved_workspace_roundtrip_and_reset_keep_exact_source_identity(runtime_fixture):
    runtime,prepared=runtime_fixture
    handle=runtime.open_workspace(prepared)
    value,actual,recipe,saved,source=runtime.workspace(handle)
    assert source.files['src/click/__init__.py'].data==b'x=1\n'
    assert runtime.reset(handle).artifact==saved.artifact
    assert runtime.close(handle).artifact==saved.artifact
    from feature_rl.environments import PolicyRejected
    with pytest.raises(PolicyRejected):runtime.workspace(handle)


def test_recipe_and_source_join_refuse_undeclared_network_and_reference(runtime_fixture):
    runtime,prepared=runtime_fixture
    from feature_rl.environments import PolicyRejected,PreparedEnvironment
    recipe=runtime.recipe(prepared)
    bad=runtime.store.put_artifact(recipe.model_copy(update={'network_policy':'declared_local_services'}))
    with pytest.raises(PolicyRejected):runtime.recipe(PreparedEnvironment(recipe=bad,policy=prepared.policy))
    with pytest.raises(PolicyRejected):runtime.open_workspace(prepared,role='reference')


def test_development_repairs_broken_source_without_build_and_imports_current_edit(runtime_fixture):
    from feature_rl.environments import CommandSpec,ExecutionRequest
    runtime,prepared=runtime_fixture;handle=runtime.open_workspace(prepared)
    code="import pathlib;p=pathlib.Path('/workspace/source/src/click/__init__.py');p.write_text('x=42\\n');import click;print(click.__file__);assert click.x==42"
    result=runtime.execute_development(handle,ExecutionRequest(command=CommandSpec(argv=('python','-c',code),working_directory='/workspace',timeout_seconds=2.0)))
    assert result.reason=='completed' and result.failure_category=='none' and result.save_status=='saved'
    assert b'/workspace/source/src/click/__init__.py' in result.stdout
    broken=runtime.execute_development(handle,ExecutionRequest(command=CommandSpec(argv=('python','-c',"import pathlib;pathlib.Path('/workspace/source/src/click/__init__.py').write_text('def broken(\\n')"),working_directory='/workspace',timeout_seconds=2.0)))
    assert broken.save_status=='saved'
    repaired=runtime.execute_development(handle,ExecutionRequest(command=CommandSpec(argv=('python','-c',code),working_directory='/workspace',timeout_seconds=2.0)))
    assert repaired.reason=='completed' and repaired.cleanup_verified

@pytest.mark.parametrize('code',[
 "import shutil;shutil.rmtree('/workspace/source')",
 "import pathlib;pathlib.Path('/workspace/source/src/escape').symlink_to('/etc/passwd')",
 "import pathlib;pathlib.Path('/workspace/source/src/huge').write_bytes(b'x'*(9*1024*1024))",
])
def test_candidate_export_failure_is_attributed_and_preserves_saved_source(runtime_fixture,code):
    from feature_rl.environments import CommandSpec,ExecutionRequest
    runtime,prepared=runtime_fixture;handle=runtime.open_workspace(prepared);saved=runtime.workspace(handle)[3]
    result=runtime.execute_development(handle,ExecutionRequest(command=CommandSpec(argv=('python','-c',code),working_directory='/workspace',timeout_seconds=3.0)))
    assert result.reason=='source_rejected' and result.failure_category=='candidate'
    assert result.saved_source==saved and result.cleanup_verified


def test_daemon_loss_during_cleanup_is_pending_then_recovered(engine):
    from feature_rl.environments import CleanupUnverified
    session=engine.session(binding={'test':'cleanup-recovery'},saved_source={});session.__enter__()
    original=engine.base;engine.base=[*original[:3],'--host','unix:///tmp/feature-rl-definitely-missing.sock']
    try:
        with pytest.raises(CleanupUnverified):session.__exit__(None,None,None)
        assert not session.cleanup_verified
        assert engine.state.read('operation-'+session.record.operation_id+'.json')['phase']=='cleanup_pending'
    finally:engine.base=original
    result=engine.recover_owned();assert any(x['operation_id']==session.record.operation_id and x['cleanup_verified'] for x in result)
    assert engine.recover_owned()==[]


def test_direct_exec_exit_does_not_leave_descendant_container(engine):
    from feature_rl.environments import CommandSpec
    with engine.session(binding={'test':'descendant'},saved_source={}) as s:
        r=s.execute(CommandSpec(argv=('python','-I','-c',"import subprocess;subprocess.Popen(['python','-I','-c','import time;time.sleep(30)'],stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)"),working_directory='/workspace',timeout_seconds=2.0))
        assert r.reason=='exited' and r.exit_code==0
    assert s.cleanup_verified and engine.recover_owned()==[]


def test_failed_candidate_build_has_typed_receipt_and_saved_source(runtime_fixture):
    from feature_rl.environments import BuildFailed
    runtime,prepared=runtime_fixture;handle=runtime.open_workspace(prepared);saved=runtime.workspace(handle)[3]
    # Synthetic fixture deliberately lacks the description required by its pinned backend.
    with pytest.raises(BuildFailed) as caught:runtime.build_snapshot(handle)
    error=caught.value
    assert error.failure_category=='candidate' and error.reason=='command_failed'
    assert error.saved_source==saved and error.cleanup_verified
    record=json.loads(runtime.read_bytes(error.evidence,32*1024*1024))
    assert record['extra']['failure_category']=='candidate' and record['cleanup_verified']


def test_build_cleanup_fault_remains_infrastructure_and_can_recover(runtime_fixture,monkeypatch):
    from feature_rl.environments import BuildFailed
    from feature_rl.environments.docker import DockerSession
    runtime,prepared=runtime_fixture;handle=runtime.open_workspace(prepared)
    original_cleanup=DockerSession.cleanup;original_base=runtime.engine.base
    def unavailable_cleanup(session):
        session.engine.base=[*original_base[:3],'--host','unix:///tmp/feature-rl-definitely-missing.sock']
        try:return original_cleanup(session)
        finally:session.engine.base=original_base
    with monkeypatch.context() as patch:
        patch.setattr(DockerSession,'cleanup',unavailable_cleanup)
        with pytest.raises(BuildFailed) as caught:runtime.build_snapshot(handle)
    assert caught.value.failure_category=='infrastructure' and not caught.value.cleanup_verified
    assert runtime.recover_owned()[0]['cleanup_verified']


def test_new_controller_recovers_persisted_owned_scope(engine):
    from feature_rl.environments import DockerEngine
    session=engine.session(binding={'test':'controller-restart'},saved_source={'version':4});session.__enter__()
    name=session.name
    # Release only the controller lock, simulating loss after a persisted create/start.
    session._lock.__exit__(None,None,None)
    replacement=DockerEngine(state_root=engine.state.path,socket_path=Path(engine.socket_path),policy=engine.policy)
    result=replacement.recover_owned()
    assert result[0]['operation_id']==session.record.operation_id and result[0]['cleanup_verified']
    assert replacement.recover_owned()==[]


def test_development_output_limit_keeps_last_confirmed_submission(runtime_fixture):
    from feature_rl.environments import CommandSpec,ExecutionRequest
    runtime,prepared=runtime_fixture;handle=runtime.open_workspace(prepared);saved=runtime.workspace(handle)[3]
    code="import pathlib,os;pathlib.Path('/workspace/source/src/click/__init__.py').write_text('x=999\\n');\nwhile True:os.write(1,b'x'*65536)"
    result=runtime.execute_development(handle,ExecutionRequest(command=CommandSpec(argv=('python','-c',code),working_directory='/workspace',timeout_seconds=3.0)))
    assert result.reason=='output_limit' and result.failure_category=='candidate'
    assert result.saved_source==saved and result.save_status=='last_confirmed' and result.cleanup_verified


def test_memory_oom_is_reported_from_kernel_events(engine):
    from feature_rl.environments import CommandSpec
    with engine.session(binding={'test':'oom-attribution'},saved_source={}) as s:
        r=s.execute(CommandSpec(argv=('python','-I','-c','a=bytearray(256*1024*1024)'),working_directory='/workspace',timeout_seconds=3.0))
        assert r.reason=='memory_limit' and s.memory_oom_events>=1
    assert s.cleanup_verified


def test_cpu_budget_monitor_stops_nonterminating_command(tmp_path):
    from feature_rl.environments import DockerEngine,SandboxPolicy,CommandSpec
    engine=DockerEngine(state_root=tmp_path/'runtime',socket_path=Path.home()/'.docker/run/docker.sock',policy=SandboxPolicy(cpu_seconds=1.0,lifecycle_seconds=12.0))
    with engine.session(binding={'test':'cpu-budget'},saved_source={}) as s:
        r=s.execute(CommandSpec(argv=('python','-I','-c','while True: pass'),working_directory='/workspace',timeout_seconds=10.0))
        assert r.reason=='cpu_limit' and s.maximum_cpu_seconds>=1.0
    assert s.cleanup_verified


def test_forbidden_dependency_policy_cannot_allow_manifest_edits(runtime_fixture):
    from feature_rl.environments import PolicyRejected
    from feature_rl.contracts import AllowedChanges
    runtime,prepared=runtime_fixture
    policy=AllowedChanges(source_roots=('pyproject.toml',),forbidden_paths=(),dependencies='forbidden',dependency_artifacts=(),additional_artifact_types=())
    with pytest.raises(PolicyRejected):runtime.open_workspace(prepared,allowed_changes=policy)


@pytest.mark.parametrize('transition', ['close', 'reset'])
def test_terminal_transition_reloads_after_concurrent_confirmed_save(runtime_fixture, monkeypatch, transition):
    from contextlib import contextmanager
    import threading
    from feature_rl.environments import CommandSpec,ExecutionRequest
    runtime,prepared=runtime_fixture;handle=runtime.open_workspace(prepared)
    initial=runtime.workspace(handle)[3]
    paused=threading.Event();release=threading.Event();outcome={};original_lock=runtime.engine.state.lock
    @contextmanager
    def scheduled_lock():
        if threading.current_thread().name=='terminal-transition':
            outcome['lock_calls']=outcome.get('lock_calls',0)+1
            # Recovery takes the first lock; delay the terminal transition's own lock.
            if outcome['lock_calls']==2:
                paused.set();assert release.wait(10), 'bounded interleaving expired'
        with original_lock():yield
    def terminal():
        try:outcome['saved']=getattr(runtime,transition)(handle)
        except BaseException as exc:outcome['error']=exc
    with monkeypatch.context() as patch:
        patch.setattr(runtime.engine.state,'lock',scheduled_lock)
        thread=threading.Thread(target=terminal,name='terminal-transition');thread.start()
        try:
            assert paused.wait(3)
            result=runtime.execute_development(handle,ExecutionRequest(command=CommandSpec(
                argv=('python','-c',"import pathlib;pathlib.Path('/workspace/source/src/click/__init__.py').write_text('x=42\\n')"),
                working_directory='/workspace',timeout_seconds=2.0)))
            assert result.reason=='completed' and result.save_status=='saved' and result.cleanup_verified
            intermediate=runtime.engine.state.read('workspace-'+handle.workspace_id+'.json')
        finally:release.set();thread.join(3)
    assert not thread.is_alive() and 'error' not in outcome
    state=runtime.engine.state.read('workspace-'+handle.workspace_id+'.json')
    expected=result.saved_source if transition=='close' else initial
    assert outcome['saved']==expected
    assert state['saved']==expected.model_dump(mode='json')
    assert state['generation']==intermediate['generation']+1
    assert state['closed']==(transition=='close')
    assert runtime.recover_owned()==[]


def test_overlapping_resets_advance_generation_and_reject_old_admission(runtime_fixture, monkeypatch):
    from contextlib import contextmanager
    import threading
    from feature_rl.environments import PolicyRejected
    runtime,prepared=runtime_fixture;handle=runtime.open_workspace(prepared)
    paused=threading.Event();release=threading.Event();outcome={};original_lock=runtime.engine.state.lock
    @contextmanager
    def scheduled_lock():
        if threading.current_thread().name=='terminal-reset':
            outcome['lock_calls']=outcome.get('lock_calls',0)+1
            if outcome['lock_calls']==2:
                paused.set();assert release.wait(10), 'bounded interleaving expired'
        with original_lock():yield
    def reset():
        try:outcome['saved']=runtime.reset(handle)
        except BaseException as exc:outcome['error']=exc
    with monkeypatch.context() as patch:
        patch.setattr(runtime.engine.state,'lock',scheduled_lock)
        thread=threading.Thread(target=reset,name='terminal-reset');thread.start()
        try:
            assert paused.wait(3)
            runtime.reset(handle)
            intermediate,actual,recipe,saved,source=runtime.workspace(handle)
            pending=runtime.engine.session(binding=runtime.binding(actual,saved,'development',intermediate),saved_source=saved.model_dump(mode='json'))
        finally:release.set();thread.join(3)
    assert not thread.is_alive() and 'error' not in outcome
    state=runtime.engine.state.read('workspace-'+handle.workspace_id+'.json')
    assert state['saved']==intermediate['saved']  # Same bytes cannot excuse generation reuse.
    assert state['generation']==intermediate['generation']+1
    with pytest.raises(PolicyRejected,match='workspace changed before operation admission'):
        with pending:pass
    assert pending.record.container_id is None and pending.cleanup_verified
    assert runtime.recover_owned()==[]


@pytest.mark.parametrize('transition', ['close', 'reset'])
def test_terminal_admission_rejects_new_pending_cleanup_and_allows_recovered_retry(runtime_fixture, monkeypatch, transition):
    from contextlib import contextmanager
    import threading,time
    from feature_rl.environments import CommandSpec,ExecutionRequest,CleanupUnverified
    from feature_rl.environments.docker import DockerSession
    runtime,prepared=runtime_fixture;engine=runtime.engine;handle=runtime.open_workspace(prepared)
    initial=runtime.workspace(handle)[3]
    paused=threading.Event();release=threading.Event();outcome={};original_lock=engine.state.lock
    @contextmanager
    def scheduled_lock():
        if threading.current_thread().name=='terminal-cleanup':
            outcome['lock_calls']=outcome.get('lock_calls',0)+1
            if outcome['lock_calls']==2:
                paused.set();assert release.wait(10), 'bounded interleaving expired'
        with original_lock():yield
    def terminal():
        try:outcome['saved']=getattr(runtime,transition)(handle)
        except BaseException as exc:outcome['error']=exc
    original_cleanup=DockerSession.cleanup;original_base=engine.base
    def unavailable_cleanup(session):
        session.engine.base=[*original_base[:3],'--host','unix:///tmp/feature-rl-definitely-missing.sock']
        try:return original_cleanup(session)
        finally:session.engine.base=original_base
    with monkeypatch.context() as patch:
        patch.setattr(engine.state,'lock',scheduled_lock)
        thread=threading.Thread(target=terminal,name='terminal-cleanup');thread.start()
        try:
            assert paused.wait(3)
            with monkeypatch.context() as fault:
                fault.setattr(DockerSession,'cleanup',unavailable_cleanup)
                result=runtime.execute_development(handle,ExecutionRequest(command=CommandSpec(
                    argv=('python','-c',"import pathlib;pathlib.Path('/workspace/source/src/click/__init__.py').write_text('x=42\\n')"),
                    working_directory='/workspace',timeout_seconds=2.0)))
            assert result.failure_category=='infrastructure' and not result.cleanup_verified and result.save_status=='saved'
            pending=engine.state.read('operation-'+result.operation_id+'.json')
            assert pending['phase']=='cleanup_pending'
            status,body=engine.http('GET','/containers/'+pending['container_id']+'/json',deadline=time.monotonic()+2,cap=1024*1024)
            assert status==200 and json.loads(body)['State']['Running']
            before=engine.state.read('workspace-'+handle.workspace_id+'.json')
            release.set();thread.join(3)
            assert not thread.is_alive()
            after=engine.state.read('workspace-'+handle.workspace_id+'.json')
            status,body=engine.http('GET','/containers/'+pending['container_id']+'/json',deadline=time.monotonic()+2,cap=1024*1024)
            assert status==200 and json.loads(body)['State']['Running']
        finally:
            release.set();thread.join(3)
            recovery=runtime.recover_owned()
    assert isinstance(outcome.get('error'),CleanupUnverified) and 'saved' not in outcome
    assert after==before  # Includes exact source, generation and closed state.
    assert any(x['operation_id']==result.operation_id and x['cleanup_verified'] for x in recovery)
    status,_=engine.http('GET','/containers/'+pending['container_id']+'/json',deadline=time.monotonic()+2,cap=1024*1024)
    assert status==404
    final=getattr(runtime,transition)(handle)
    assert final==(result.saved_source if transition=='close' else initial)
    state=engine.state.read('workspace-'+handle.workspace_id+'.json')
    assert state['generation']==before['generation']+1 and state['closed']==(transition=='close')
    assert runtime.recover_owned()==[]
