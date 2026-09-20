"""CPU-only request/monitor/classification tests; session doubles are not Docker evidence."""
from m4_fixtures import runtime_policy
from types import SimpleNamespace
import json
import pytest
from feature_rl.contracts import ArtifactRef,CommandSpec,Visibility
from feature_rl.environments import (ExecutionRequest,SandboxPolicy,PolicyRejected,SourceArchive,
    SourceFile,SavedSource,PreparedEnvironment,WorkspaceHandle,EnvironmentRuntime,ProcessObservation,
    DockerUnavailable,CleanupUnverified)
from feature_rl.environments.docker import DockerEngine

COMMAND=CommandSpec(argv=('python','-c','pass'),working_directory='/workspace',timeout_seconds=1.0)

@pytest.mark.parametrize('cap',[0.0,-1.0,float('inf'),float('nan')])
def test_request_remaining_cpu_must_be_positive_finite(cap):
    with pytest.raises(ValueError):ExecutionRequest(command=COMMAND,remaining_cpu_seconds=cap)


def test_request_default_and_fractional_remaining_cpu():
    assert ExecutionRequest(command=COMMAND).remaining_cpu_seconds is None
    assert ExecutionRequest(command=COMMAND,remaining_cpu_seconds=.05).remaining_cpu_seconds==.05


def engine_double():
    engine=object.__new__(DockerEngine);engine.policy=runtime_policy();engine.daemon_id='test-daemon'
    engine.http=lambda *a,**k:(200,json.dumps({'cpu_stats':{'cpu_usage':{'total_usage':700000000}},'memory_stats':{'usage':123}}).encode())
    return engine


def test_monitor_uses_request_cap_and_default_preserves_policy():
    engine=engine_double()
    low=engine.session(binding={},saved_source={},cpu_seconds=.5)
    assert low.effective_cpu_seconds==.5 and low.record.binding['effective_cpu_seconds']=='0.5'
    assert low.monitor()=='cpu_limit' and low.maximum_cpu_seconds==.7
    default=engine.session(binding={},saved_source={})
    assert default.effective_cpu_seconds==engine.policy.cpu_seconds and default.monitor() is None

@pytest.mark.parametrize('cap',[0,-1,float('inf'),float('nan'),61.0,True])
def test_session_cap_cannot_exceed_admitted_policy(cap):
    with pytest.raises(PolicyRejected):engine_double().session(binding={},saved_source={},cpu_seconds=cap)


def ref(kind):return ArtifactRef(sha256='a'*64,kind=kind,schema_version=1,visibility=Visibility.PRIVATE,encoding='bytes')


def runtime_double(phase,cleanup_failure=False):
    runtime=object.__new__(EnvironmentRuntime);runtime.policy=runtime_policy();runtime.revision='a'*40
    source=SourceArchive({'src/click/__init__.py':SourceFile(b'x=1\n',False)})
    saved=SavedSource(artifact=ref('source-archive'),raw_sha256='b'*64,tree_sha256=source.tree_sha256,version=0,saved_at='2026-09-19T00:00:00+00:00')
    prepared=PreparedEnvironment(recipe=ref('EnvironmentRecipe'),policy=ref('sandbox-policy'))
    value={'workspace_id':'a'*32,'generation':0,'role':'candidate','source_input':saved.artifact.model_dump(mode='json'),'source_pair':None,'allowed_changes':{}}
    runtime.workspace=lambda handle:(value,prepared,SimpleNamespace(dependencies=()),saved,source)
    runtime.recover_owned=lambda:[];runtime.dependency_bytes=lambda pins:{}
    observed={}
    class Session:
        maximum_cpu_seconds=.1;maximum_memory_bytes=123;memory_oom_events=0;container_state={};cleanup_verified=False
        record=SimpleNamespace(operation_id='b'*32)
        def __enter__(self):return self
        def __exit__(self,*args):
            if cleanup_failure:raise CleanupUnverified('test cleanup fault')
            self.cleanup_verified=True
        def execute(self,command,*args,**kwargs):
            count=observed.get('commands',0);observed['commands']=count+1
            reason='cpu_limit' if (phase=='stage' and count==0) or (phase=='setup' and count==1) or (phase=='command' and count==2) else 'exited'
            return ProcessObservation(argv=command.argv,exit_code=0 if reason=='exited' else -9,reason=reason,stdout=b'',stderr=b'',observed_bytes=0,wall_seconds=.01)
        def export_source(self):
            from feature_rl.environments import CpuBudgetExceeded
            if phase=='export':raise CpuBudgetExceeded('test export cap')
            if phase=='storage':raise OSError('test storage unavailable')
            raise DockerUnavailable('test monitor unavailable')
    def session(**kwargs):observed['session_arguments']=kwargs;return Session()
    runtime.engine=SimpleNamespace(session=session)
    runtime.evidence=lambda s,p,extra:observed.update(extra=extra) or ref('environment-execution')
    return runtime,saved,observed

@pytest.mark.parametrize('phase',['stage','setup','command','export'])
def test_budget_exhaustion_preserves_last_confirmed_source_and_candidate_category(phase):
    runtime,saved,observed=runtime_double(phase)
    result=runtime.execute_development(WorkspaceHandle(workspace_id='a'*32),ExecutionRequest(command=COMMAND,remaining_cpu_seconds=.1))
    assert result.reason=='cpu_limit' and result.failure_category=='candidate'
    assert result.saved_source==saved and result.save_status=='last_confirmed' and result.cleanup_verified
    assert observed['session_arguments']['cpu_seconds']==.1

@pytest.mark.parametrize('phase,cleanup_failure',[('monitor',False),('storage',False),('setup',True),('export',True)])
def test_actual_infrastructure_fault_is_not_relabelled_cpu_exhaustion(phase,cleanup_failure):
    runtime,saved,observed=runtime_double(phase,cleanup_failure)
    result=runtime.execute_development(WorkspaceHandle(workspace_id='a'*32),ExecutionRequest(command=COMMAND,remaining_cpu_seconds=.1))
    assert result.reason=='infrastructure_failure' and result.failure_category=='infrastructure'
    assert result.saved_source==saved and result.save_status=='last_confirmed'


def test_excessive_request_cap_rejected_before_session_creation():
    runtime,saved,observed=runtime_double('stage')
    with pytest.raises(PolicyRejected):runtime.execute_development(WorkspaceHandle(workspace_id='a'*32),ExecutionRequest(command=COMMAND,remaining_cpu_seconds=61.0))
    assert 'session_arguments' not in observed


def test_export_cpu_monitor_failure_has_distinct_exception(monkeypatch):
    from feature_rl.environments.docker import DockerSession
    import feature_rl.environments.docker as docker_module
    from feature_rl.environments import CpuBudgetExceeded
    engine=engine_double();engine.base=['unused'];session=engine.session(binding={},saved_source={},cpu_seconds=.1)
    def response(reason):return ProcessObservation(argv=('trusted-export',),exit_code=0 if reason=='exited' else -9,reason=reason,stdout=b'',stderr=b'',observed_bytes=0,wall_seconds=.01)
    monkeypatch.setattr(docker_module,'stream_process',lambda *a,**k:response('cpu_limit'))
    with pytest.raises(CpuBudgetExceeded):session.export_source()
    monkeypatch.setattr(docker_module,'stream_process',lambda *a,**k:response('monitor_failure'))
    with pytest.raises(DockerUnavailable):session.export_source()
    monkeypatch.setattr(docker_module,'stream_process',lambda *a,**k:response('exited'))
    with pytest.raises(CpuBudgetExceeded):session.export_source()  # Final cumulative poll precedes source publication.
    engine.http=lambda *a,**k:(503,b'{}')
    with pytest.raises(DockerUnavailable):session.export_source()


def test_recovery_preserves_original_cap_when_current_policy_is_stricter():
    from feature_rl.environments.docker import DockerSession
    engine=engine_double();original=engine.session(binding={},saved_source={})
    engine.policy=runtime_policy(cpu_seconds=1.0)
    recovery=DockerSession(engine,original.record.binding,{},record=original.record)
    assert recovery.effective_cpu_seconds==60.0
    assert recovery.record.binding==original.record.binding
