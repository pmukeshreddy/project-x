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
    runtime=object.__new__(EnvironmentRuntime);runtime.store=store;runtime.policy=runtime_policy();runtime.revision='a'*40
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




def test_verified_history_uses_actual_registry_candidate_trace_jobs(tmp_path):
    from feature_rl.qualification import QualificationService, QualificationPolicy, RepairHistory
    from feature_rl.qualification.evidence import put_record
    from feature_rl.registry import JobSpec
    from feature_rl.verifiers import load_verifier
    q=service(tmp_path);task_ref=task_fixture(q.store);checked=load_verifier(q.store,task_ref)
    pair=q.store.get_artifact(checked.task.source_pair)
    history=RepairHistory(candidate=pair.candidate,complete=True,initial_evidence=pair.verification,
        attempts=(),journal_refs=(pair.verification[0].artifacts[0],))
    history_ref=put_record(q.store,history,'m5-repair-history')
    q.registry.register(history_ref,dependencies=(pair.candidate,*history.journal_refs))
    config=put_record(q.store,{'diagnostic_only':True},'unit-diagnostic')
    job=q.registry.enqueue(JobSpec(operation='construct',inputs=(pair.candidate,),configuration=config,
        implementation='d'*40,invocation='synthetic-history-only',attempt_limit=1))
    claim=q.registry.claim(job.job_id,owner='synthetic-diagnostic',claim_key='synthetic-history')
    result=c.OperationResult(operation='construct',disposition=c.Disposition.SUCCESS,artifacts=(history_ref,),
        evidence=pair.verification,costs=checked.task.costs,reason='Synthetic history fixture, not real construction')
    q._complete(claim,result)
    policy=QualificationPolicy(repair_history=history_ref,repair_history_job=job.job_id,factory_revision='d'*40)
    bound=QualificationService(store=q.store,registry=q.registry,grader=q.grader,builder=None,revision='c'*40,policy=policy)
    assert bound._history(checked)==(0,None)
