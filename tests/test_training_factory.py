"""Native startup orchestration with a declared constructor boundary double."""
import pytest
from types import SimpleNamespace
from feature_rl.registry import JobSpec
from feature_rl.training.factory import NativeSessionFactory,NativeStartupRecoveryRequired


def factory_fixture(tmp_path,monkeypatch):
    from test_agent_runner import fixture
    from test_training_native import settings
    from feature_rl.training.files import publish_directory
    from feature_rl import contracts as c
    from feature_rl.training.skyrl_bridge import PINNED_SKYRL,PINNED_HARBOR
    f=fixture(tmp_path,monkeypatch)
    root=tmp_path/'model';root.mkdir();(root/'weights').write_bytes(b'inert diagnostic weights')
    weights=publish_directory(store=f.store,registry=f.registry,path=root)
    policy=f.policy.model_copy(update={'identity':f.policy.identity.model_copy(update={'weights':weights})})
    config=c.TrainingConfig(initial_policy=policy,reference_checkpoint=weights,tasks=(f.task,),limits=f.limits,
        seeds=c.SeedPolicy(algorithm='diagnostic',seeds=(19,),same_cases_within_group=True),algorithm='sft',
        group_size=4,max_updates=1,learning_rate=.01,framework='skyrl',framework_version=PINNED_SKYRL,
        backend_version=PINNED_HARBOR,budget_usd=None)
    return SimpleNamespace(config=config,service=SimpleNamespace(store=f.store,registry=f.registry,settings=settings(tmp_path),revision='f'*40))


def test_native_factory_requires_selected_config_and_records_intent_before_constructor(tmp_path,monkeypatch):
    import feature_rl.training.factory as module
    f=factory_fixture(tmp_path,monkeypatch);service=f.service
    factory=NativeSessionFactory(store=service.store,registry=service.registry,settings=service.settings,
        configuration=f.config,revision=service.revision)
    calls=[]
    def native(**kwargs):
        rows=service.registry.accounting(claim.job_id).observations
        assert any(r.observation.source=='m7-native-startup' and r.observation.revision==1 for r in rows)
        calls.append('initialize')
        assert kwargs['settings'].work_directory==str(__import__('pathlib').Path(service.settings.work_directory)/'jobs'/claim.job_id)
        return SimpleNamespace(last_probe={'scope':'unit_diagnostic'},close=lambda:calls.append('close'))
    monkeypatch.setattr(module,'NativeSession',native)
    assert not calls
    job=service.registry.enqueue(JobSpec(operation='evaluate',inputs=f.config.tasks,configuration=factory.configuration,
        implementation=service.revision,invocation='factory-diagnostic',attempt_limit=1))
    claim=service.registry.claim(job.job_id,owner='diagnostic',claim_key='diagnostic-native')
    handle=factory.create(claim,startup_key='native-first')
    assert calls==['initialize'] and factory.create(claim,startup_key='native-first') is handle
    assert any(r.observation_id==handle.observation_id and r.observation.revision==2 for r in service.registry.accounting(job.job_id).observations)
    factory.close(handle,claim,shutdown_key='native-close')
    assert calls==['initialize','close']
    with pytest.raises(NativeStartupRecoveryRequired):factory.create(claim,startup_key='native-first')


def test_factory_preserves_unknown_failed_startup_without_implicit_retry(tmp_path,monkeypatch):
    import feature_rl.training.factory as module
    f=factory_fixture(tmp_path,monkeypatch);service=f.service
    factory=NativeSessionFactory(store=service.store,registry=service.registry,settings=service.settings,
        configuration=f.config,revision=service.revision)
    job=service.registry.enqueue(JobSpec(operation='run',inputs=f.config.tasks,configuration=factory.configuration,
        implementation=service.revision,invocation='failure-diagnostic',attempt_limit=1))
    claim=service.registry.claim(job.job_id,owner='diagnostic',claim_key='diagnostic-failure')
    calls=[]
    def unavailable(**kwargs):calls.append('once');raise RuntimeError('diagnostic worker startup failed')
    monkeypatch.setattr(module,'NativeSession',unavailable)
    with pytest.raises(RuntimeError,match='startup failed'):factory.create(claim,startup_key='one')
    with pytest.raises(NativeStartupRecoveryRequired):factory.create(claim,startup_key='one')
    assert calls==['once']
    costs=service.registry.accounting(job.job_id).observations[0].observation.costs
    assert all(cost.wall_seconds is None and cost.usd is None for cost in costs)


def test_startup_and_shutdown_publication_outages_keep_exact_live_or_closed_result(tmp_path,monkeypatch):
    import feature_rl.training.factory as module
    from feature_rl.artifacts import ArtifactError
    f=factory_fixture(tmp_path,monkeypatch);service=f.service
    factory=NativeSessionFactory(store=service.store,registry=service.registry,settings=service.settings,
        configuration=f.config,revision=service.revision)
    job=service.registry.enqueue(JobSpec(operation='evaluate',inputs=f.config.tasks,configuration=factory.configuration,
        implementation=service.revision,invocation='publication-diagnostic',attempt_limit=1))
    claim=service.registry.claim(job.job_id,owner='diagnostic',claim_key='publication')
    calls=[]
    def native(**kwargs):calls.append('init');return SimpleNamespace(last_probe={'scope':'unit_diagnostic'},close=lambda:calls.append('close'))
    monkeypatch.setattr(module,'NativeSession',native)
    put=service.store.put_bytes;failed=set()
    def flaky(data,kind,visibility):
        if kind in ('m7-native-startup','m7-native-shutdown') and kind not in failed:
            failed.add(kind);raise ArtifactError('diagnostic receipt outage')
        return put(data,kind,visibility)
    monkeypatch.setattr(service.store,'put_bytes',flaky)
    with pytest.raises(ArtifactError):factory.create(claim,startup_key='start')
    handle=factory.create(claim,startup_key='start')
    assert calls==['init']
    with pytest.raises(ArtifactError):factory.close(handle,claim,shutdown_key='end')
    receipt=factory.close(handle,claim,shutdown_key='end')
    assert factory.close(handle,claim,shutdown_key='end')==receipt
    assert calls==['init','close']


def test_current_quarantine_denies_use_but_never_owned_native_cleanup(tmp_path,monkeypatch):
    import feature_rl.training.factory as module
    from feature_rl.registry import QuarantinedError
    f=factory_fixture(tmp_path,monkeypatch);service=f.service
    factory=NativeSessionFactory(store=service.store,registry=service.registry,settings=service.settings,
        configuration=f.config,revision=service.revision)
    job=service.registry.enqueue(JobSpec(operation='train',inputs=f.config.tasks,configuration=factory.configuration,
        implementation=service.revision,invocation='revocation-diagnostic',attempt_limit=1))
    claim=service.registry.claim(job.job_id,owner='diagnostic',claim_key='revocation')
    closed=[]
    monkeypatch.setattr(module,'NativeSession',lambda **kw:SimpleNamespace(last_probe={'scope':'unit_diagnostic'},close=lambda:closed.append(True)))
    handle=factory.create(claim,startup_key='start')
    service.registry.quarantine(f.config.tasks[0],notice_id='diagnostic-revocation',reason='TEST ONLY',evidence=(factory.configuration,))
    with pytest.raises(QuarantinedError):factory.create(claim,startup_key='another')
    receipt=factory.close(handle,claim,shutdown_key='end')
    assert closed==[True] and receipt[0].kind=='m7-native-shutdown'
    with pytest.raises(QuarantinedError):service.registry.assert_usable(receipt[0])
    assert factory.close(handle,claim,shutdown_key='end')==receipt
    assert closed==[True]
