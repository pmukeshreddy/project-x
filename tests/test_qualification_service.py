"""M5 orchestration boundary diagnostics. Full worker path belongs to TEST fixture."""
from m4_fixtures import runtime_policy
import pytest
from feature_rl import contracts as c
from feature_rl.artifacts import ArtifactStore
from feature_rl.environments import EnvironmentRuntime, SandboxPolicy
from feature_rl.grading import GradingService
from feature_rl.registry import Registry
from m5_fixtures import task_fixture


def service(tmp_path):
    from feature_rl.qualification import QualificationService
    store=ArtifactStore(tmp_path/'store',c.ActorRole.CONTROLLER)
    registry=Registry(tmp_path/'registry',store)
    runtime=object.__new__(EnvironmentRuntime);runtime.store=store;runtime.policy=runtime_policy();runtime.base_policy=runtime.policy;runtime.revision='a'*40
    grader=GradingService(store=store,runtime=runtime,revision='b'*40)
    return QualificationService(store=store,registry=registry,grader=grader,builder=None,revision='c'*40)


def test_missing_real_package_validator_returns_precise_provisional_without_worker_execution(tmp_path):
    q=service(tmp_path);task=task_fixture(q.store)
    result=q.qualify(task)
    assert result.operation=='qualify' and result.disposition==c.Disposition.PROVISIONAL
    assert 'package validator' in result.reason
    assert result==q.qualify(task)


def test_forged_accepted_report_without_authenticated_origin_is_rejected(tmp_path):
    from feature_rl.qualification import QualificationRejected
    q=service(tmp_path);task=task_fixture(q.store)
    with pytest.raises(QualificationRejected):q.verify_accepted(task,task)




def test_unknown_worker_attempt_is_never_redispatched(tmp_path):
    from feature_rl.qualification import QualificationUnavailable
    q=service(tmp_path);task=task_fixture(q.store)
    job=q._job(task,'m5-qualify');q._claim(job)
    with pytest.raises(QualificationUnavailable):q.qualify(task)
    assert q.registry.job(job.job_id).state=='running'
    assert q.registry.accounting(job.job_id).unobserved_attempts==q.registry.job(job.job_id).attempts
    assert q.registry.job(job.job_id).spec.attempt_limit==1


def test_qualification_completion_accounts_only_for_its_actual_results(tmp_path):
    q=service(tmp_path);task=task_fixture(q.store)
    result=q.qualify(task);job=q._job(task,'m5-qualify')
    assert [ref.kind for ref in result.artifacts]==['QualificationReport','m5-qualification-summary']
    assert job.spec.attempt_limit==1
    accounting=q.registry.accounting(job.job_id)
    assert not accounting.unobserved_attempts and len(accounting.observations)==1
    observation=accounting.observations[0].observation
    assert observation.revision==1 and observation.receipts==result.artifacts and observation.costs==result.costs
