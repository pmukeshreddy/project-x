"""Native lifecycle boundary doubles around real selected runner/CAS/Registry mechanics."""
from types import SimpleNamespace
import pytest
from feature_rl import contracts as c
from feature_rl.agents import AgentRunner
from feature_rl.registry import QuarantinedError


def setup(tmp_path,monkeypatch):
    from test_training_factory import factory_fixture
    from feature_rl.training.factory import NativeSessionFactory
    from feature_rl.agents.native_service import NativeRunService
    import feature_rl.training.factory as native_module
    f=factory_fixture(tmp_path,monkeypatch);a=f.fixture;calls=[]
    factory=NativeSessionFactory(store=a.store,registry=a.registry,settings=f.service.settings,
        configuration=f.config,revision='f'*40)
    def native(**kwargs):
        calls.append('initialize')
        assert any(row.observation.source=='m7-native-startup' for job in a.registry.trace(factory.configuration).jobs
            for row in a.registry.accounting(job).observations)
        def activate(cp,policy):
            calls.append('activate');return a.store.put_bytes(b'diagnostic activation','m7-native-activation',c.Visibility.PRIVATE)
        return SimpleNamespace(backend=a.backend,last_probe={'scope':'unit_diagnostic'},
            activate_checkpoint=activate,validate_activation=lambda *a,**k:None,close=lambda:calls.append('close'))
    monkeypatch.setattr(native_module,'NativeSession',native)
    monkeypatch.setattr(AgentRunner,'_messages',lambda self,task:[{'role':'user','content':'DIAGNOSTIC public package'}])
    service=NativeRunService(store=a.store,registry=a.registry,native_factory=factory,
        lifecycle=a.runner.lifecycle,builder=a.runner.builder,runtime=a.runner.runtime,grader=a.runner.grader,
        revision='f'*40,evidence_scope='unit_diagnostic')
    return SimpleNamespace(service=service,f=a,calls=calls,policy=f.config.initial_policy)


def test_native_run_claim_child_join_costs_shutdown_and_replay(tmp_path,monkeypatch):
    f=setup(tmp_path,monkeypatch)
    result=f.service.run(f.f.task,f.policy,f.f.limits,case_seed=42,invocation='ordinary')
    record=f.f.store.get_artifact(next(r for r in result.artifacts if r.kind=='RolloutRecord'))
    assert record.run_id!=f.service.last_claim.job_id
    assert record.seeds.seeds==(42,) and len(f.f.grades)==1
    assert f.calls==['initialize','activate','close']
    parent=f.f.registry.job(f.service.last_claim.job_id)
    assert parent.state=='completed' and parent.result==result
    child=f.f.registry.job(record.run_id).result
    rows=f.f.registry.accounting(parent.job_id).observations
    assigned=[r for r in rows if r.observation.source=='m7-native-run-child']
    from feature_rl.agents.runner import aggregate
    assert len(assigned)==1 and assigned[0].observation.costs==aggregate(child.costs)
    assert f.service.run(f.f.task,f.policy,f.f.limits,case_seed=42,invocation='ordinary')==result
    assert len(f.f.backend.calls)==1 and f.calls==['initialize','activate','close']


def test_native_run_publication_retry_never_resamples(tmp_path,monkeypatch):
    from feature_rl.artifacts import ArtifactError
    f=setup(tmp_path,monkeypatch);put=f.f.store.put_bytes;failed=[]
    def broken(payload,kind,*args,**kwargs):
        if kind=='m7-native-run-outcome' and not failed:failed.append(True);raise ArtifactError('publication unavailable')
        return put(payload,kind,*args,**kwargs)
    monkeypatch.setattr(f.f.store,'put_bytes',broken)
    with pytest.raises(ArtifactError):f.service.run(f.f.task,f.policy,f.f.limits,invocation='retry')
    result=f.service.recover(f.service.last_claim)
    assert result.operation=='run' and len(f.f.backend.calls)==1
    assert f.calls==['initialize','activate','close']


def test_native_run_quarantine_still_closes_and_denies_publication(tmp_path,monkeypatch):
    f=setup(tmp_path,monkeypatch);grade=f.f.runner.grader.grade
    def quarantined(*args):
        result=grade(*args);f.f.registry.quarantine(f.f.task,notice_id='diagnostic',reason='diagnostic revocation',evidence=(f.service.configuration,))
        return result
    monkeypatch.setattr(f.f.runner.grader,'grade',quarantined)
    from feature_rl.agents import RunPublicationFailed
    with pytest.raises(RunPublicationFailed) as error:f.service.run(f.f.task,f.policy,f.f.limits,invocation='quarantine')
    assert isinstance(error.value.__cause__,QuarantinedError)
    assert f.calls[-1]=='close' and len(f.f.backend.calls)==1


def test_native_run_durable_parent_completion_retry_without_session(tmp_path,monkeypatch):
    from feature_rl.registry import RegistryIOError
    f=setup(tmp_path,monkeypatch);complete=f.f.registry.complete;failed=[]
    def outage(claim,*args,**kwargs):
        if claim==f.service.last_claim and not failed:failed.append(True);raise RegistryIOError('parent publication outage')
        return complete(claim,*args,**kwargs)
    monkeypatch.setattr(f.f.registry,'complete',outage)
    with pytest.raises(RegistryIOError):f.service.run(f.f.task,f.policy,f.f.limits,invocation='durable')
    f.service._states.clear() # Simulate lost in-process service handles after confirmed shutdown.
    result=f.service.recover(f.service.last_claim)
    assert result.operation=='run' and len(f.f.backend.calls)==1
    assert f.calls==['initialize','activate','close']


def test_native_run_startup_receipt_outage_retains_cleanup_without_generation(tmp_path,monkeypatch):
    from feature_rl.artifacts import ArtifactError
    from feature_rl.agents.native_service import NativeRunRecoveryRequired
    f=setup(tmp_path,monkeypatch);put=f.f.store.put_bytes;failed=[]
    def outage(payload,kind,*args,**kwargs):
        if kind=='m7-native-startup' and not failed:failed.append(True);raise ArtifactError('startup receipt outage')
        return put(payload,kind,*args,**kwargs)
    monkeypatch.setattr(f.f.store,'put_bytes',outage)
    with pytest.raises(ArtifactError):f.service.run(f.f.task,f.policy,f.f.limits,invocation='startup-outage')
    assert f.calls==['initialize','close']
    with pytest.raises(NativeRunRecoveryRequired):f.service.recover(f.service.last_claim)
    assert f.calls==['initialize','close'] and not f.f.backend.calls


def test_pending_startup_can_be_closed_after_quarantine(tmp_path,monkeypatch):
    from feature_rl.artifacts import ArtifactError
    f=setup(tmp_path,monkeypatch);put=f.f.store.put_bytes;failed=[]
    def outage(payload,kind,*args,**kwargs):
        if kind=='m7-native-startup' and not failed:failed.append(True);raise ArtifactError('startup receipt unavailable')
        return put(payload,kind,*args,**kwargs)
    monkeypatch.setattr(f.f.store,'put_bytes',outage)
    with pytest.raises(ArtifactError):f.service.run(f.f.task,f.policy,f.f.limits,invocation='quarantine-pending')
    f.f.registry.quarantine(f.f.task,notice_id='pending',reason='diagnostic quarantine',evidence=(f.service.configuration,))
    with pytest.raises(QuarantinedError):f.service.recover(f.service.last_claim)
    assert f.calls==['initialize','close'] and not f.f.backend.calls
    with pytest.raises(QuarantinedError):f.service.recover(f.service.last_claim)
    assert f.calls==['initialize','close']


def test_checkpoint_selection_ignores_unrelated_blocked_descendants(tmp_path,monkeypatch):
    from test_training_service import service_fixture
    from feature_rl.training.factory import NativeSessionFactory
    from feature_rl.agents.native_service import NativeRunService
    from feature_rl.registry import JobSpec
    f=service_fixture(tmp_path,monkeypatch)
    trained=f.service.train(f.config,invocation='selected-success',demonstrations=(f.demo,))
    ref=next(r for r in trained.artifacts if r.kind=='TrainingCheckpoint');checkpoint=f.fixture.store.get_artifact(ref)
    request=f.fixture.store.put_bytes(b'diagnostic later budget stop','diagnostic-request',c.Visibility.PRIVATE)
    f.fixture.registry.register(request,dependencies=(ref,))
    later=f.fixture.registry.enqueue(JobSpec(operation='train',inputs=(ref,),configuration=request,
        implementation='f'*40,invocation='blocked-descendant',attempt_limit=1))
    claim=f.fixture.registry.claim(later.job_id,owner='diagnostic',claim_key='blocked')
    later_ref=f.fixture.store.put_artifact(checkpoint.model_copy(update={'policy_version':'diagnostic-blocked'}))
    f.fixture.registry.register(later_ref)
    blocked=trained.model_copy(update={'disposition':c.Disposition.BLOCKED,'reason':'diagnostic later budget stop','artifacts':(later_ref,)})
    from feature_rl.registry import CostObservation
    from feature_rl.agents.runner import aggregate
    observation=f.fixture.registry.reconcile(claim,CostObservation(source='diagnostic',upstream_attempt_id='blocked',revision=1,
        receipts=(later_ref,),costs=aggregate(blocked.costs)))
    blocked=blocked.model_copy(update={'costs':aggregate(blocked.costs)})
    f.fixture.registry.complete(claim,blocked,observations=(observation.observation_id,))
    factory=NativeSessionFactory(store=f.fixture.store,registry=f.fixture.registry,settings=f.service.settings,
        configuration=f.config,revision='f'*40)
    service=NativeRunService(store=f.fixture.store,registry=f.fixture.registry,native_factory=factory,
        lifecycle=f.service.lifecycle,builder=f.service.builder,runtime=f.service.runtime,grader=f.service.grader,
        revision='f'*40,evidence_scope='unit_diagnostic')
    policy=f.config.initial_policy.model_copy(update={'identity':f.config.initial_policy.identity.model_copy(update={'weights':checkpoint.weights}),
        'policy_version':checkpoint.policy_version})
    assert service._checkpoint(policy)==ref
