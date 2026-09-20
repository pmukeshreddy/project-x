"""Actual M4 pre-execution/Registry joins; no worker, model or approval."""
import json
import pytest
from feature_rl import contracts as c
from feature_rl.artifacts import ArtifactStore,canonical_json
from feature_rl.environments import EnvironmentRuntime,SandboxPolicy
from feature_rl.grading import GradingService,read_grade
from feature_rl.pipeline import Factory,FactoryPublicationFailed,FactoryRecoveryRequired
from feature_rl.registry import Registry,QuarantinedError
from m5_fixtures import task_fixture


def setup(tmp_path):
    store=ArtifactStore(tmp_path/'store',c.ActorRole.CONTROLLER);registry=Registry(tmp_path/'registry',store)
    task=task_fixture(store)
    runtime=object.__new__(EnvironmentRuntime)
    runtime.store=store;runtime.policy=SandboxPolicy();runtime.revision='a'*40
    grader=GradingService(store=store,runtime=runtime,revision='b'*40)
    assert hasattr(Factory,'grade'),'actual Factory grade orchestration is missing'
    factory=Factory(store=store,registry=registry,revision='c'*40,grading=grader)
    bad=store.put_bytes(b'{}','m4-submission',c.Visibility.PRIVATE)
    return factory,task,bad


def test_actual_grade_is_selected_costed_and_same_invocation_replays(tmp_path,monkeypatch):
    factory,task,submission=setup(tmp_path)
    result=factory.grade(task,submission,11,invocation='TEST_GRADE')
    grade=read_grade(factory.store,result.artifacts[0])
    assert result.disposition==c.Disposition.REJECTED and grade.reward==0
    assert grade.task==task and grade.submission==submission and grade.case_seed==11
    job=next(factory.registry.job(j) for j in factory.registry.trace(task).jobs if factory.registry.job(j).spec.operation=='grade')
    assert job.result==result
    assert tuple(cost for entry in factory.registry.accounting(job.job_id).observations for cost in entry.observation.costs)==result.costs
    def forbidden(*args,**kwargs):raise AssertionError('selected grade must not execute again')
    monkeypatch.setattr(factory.grading,'grade',forbidden)
    assert factory.grade(task,submission,11,invocation='TEST_GRADE')==result


def test_frozen_grade_parent_publication_recovers_without_m4_reexecution(tmp_path,monkeypatch):
    factory,task,submission=setup(tmp_path)
    original=factory.store.put_bytes
    def lost(data,kind,visibility):
        if kind=='m6-frozen-grade':raise OSError('TEST frozen parent publication loss')
        return original(data,kind,visibility)
    monkeypatch.setattr(factory.store,'put_bytes',lost)
    with pytest.raises(FactoryPublicationFailed) as pending:factory.grade(task,submission,11,invocation='TEST_GRADE')
    monkeypatch.setattr(factory.store,'put_bytes',original)
    def forbidden(*args,**kwargs):raise AssertionError('M4 grade reexecuted')
    monkeypatch.setattr(factory.grading,'grade',forbidden)
    changed=json.loads(pending.value.payload);changed['costs'][0]['wall_seconds']=999.0
    altered=FactoryPublicationFailed('TEST changed costs',pending.value.claim,canonical_json(changed),kind='m6-frozen-grade')
    with pytest.raises(ValueError,match='cost|accounting'):factory.retry_publication(altered)
    result=factory.retry_publication(pending.value)
    assert factory.recover(pending.value.claim)==result


def test_actual_m4_pending_publication_is_bound_and_recoverable(tmp_path,monkeypatch):
    factory,task,submission=setup(tmp_path);original=factory.store.put_bytes
    def outage(data,kind,visibility):
        if kind=='m4-grade-receipt':raise OSError('TEST M4 receipt outage')
        return original(data,kind,visibility)
    monkeypatch.setattr(factory.store,'put_bytes',outage)
    from feature_rl.pipeline import FactoryUpstreamPending
    with pytest.raises(FactoryUpstreamPending) as pending:factory.grade(task,submission,12,invocation='TEST_M4_PENDING')
    monkeypatch.setattr(factory.store,'put_bytes',original)
    def forbidden(*args,**kwargs):raise AssertionError('M4 grade reexecuted')
    monkeypatch.setattr(factory.grading,'grade',forbidden)
    result=factory.recover(pending.value.claim)
    assert read_grade(factory.store,result.artifacts[0]).case_seed==12
    assert factory.retry_publication(pending.value)==result


def test_unknown_grade_attempt_never_reexecutes_and_submission_delta_quarantine_is_current(tmp_path,monkeypatch):
    factory,task,submission=setup(tmp_path)
    def stop(*args,**kwargs):raise SystemExit('TEST unknown work after claim')
    monkeypatch.setattr(factory.grading,'grade',stop)
    with pytest.raises(SystemExit):factory.grade(task,submission,11,invocation='TEST_UNKNOWN')
    job=next(factory.registry.job(j) for j in factory.registry.trace(task).jobs if factory.registry.job(j).spec.operation=='grade')
    with pytest.raises(FactoryRecoveryRequired):factory.recover(factory.registry.attempts(job.job_id)[0].claim)
    from feature_rl.submission import Submission
    baseline=factory.store.get_artifact(task).baseline
    delta=factory.store.put_bytes(b'TEST inert quarantined delta','m4-source-delta',c.Visibility.PRIVATE)
    ref=factory.store.put_bytes(canonical_json(Submission(version='m4-submission-v1',baseline=baseline,changes=delta,deletions=()).model_dump(mode='json')),
        'm4-submission',c.Visibility.PRIVATE)
    factory.registry.register(ref)  # an old leaf declaration is preserved
    factory.registry.register(delta)
    factory.registry.quarantine(delta,notice_id='TEST_DELTA',reason='TEST current defect',evidence=(delta,))
    with pytest.raises(QuarantinedError):factory.grade(task,ref,11,invocation='TEST_BLOCKED_DELTA')


def test_initial_original_publication_failure_retains_actual_result_for_retry(tmp_path,monkeypatch):
    factory,task,submission=setup(tmp_path);put=factory.store.put_bytes
    def outage(data,kind,visibility):
        if kind=='m6-grade-original':raise OSError('TEST first known M4 result publication outage')
        return put(data,kind,visibility)
    monkeypatch.setattr(factory.store,'put_bytes',outage)
    with pytest.raises(FactoryPublicationFailed) as pending:factory.grade(task,submission,13,invocation='TEST_ORIGINAL')
    assert pending.value.kind=='m6-pending-grade-result'
    assert json.loads(pending.value.payload)['result']['artifacts'][0]['kind']=='m4-grade-receipt'
    monkeypatch.setattr(factory.store,'put_bytes',put)
    def forbidden(*args,**kwargs):raise AssertionError('actual known M4 result must not execute again')
    monkeypatch.setattr(factory.grading,'grade',forbidden)
    result=factory.retry_publication(pending.value)
    assert read_grade(factory.store,result.artifacts[0]).case_seed==13
    assert factory.recover(pending.value.claim)==result
