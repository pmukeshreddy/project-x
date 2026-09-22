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
    return QualificationService(store=store,registry=registry,builder=None,revision='c'*40)


def test_missing_real_package_validator_returns_precise_provisional_without_worker_execution(tmp_path):
    q=service(tmp_path);task=task_fixture(q.store)
    result=q.qualify(task)
    assert result.operation=='qualify' and result.disposition==c.Disposition.PROVISIONAL
    assert 'package validator' in result.reason


def test_forged_accepted_report_without_authenticated_origin_is_rejected(tmp_path):
    from feature_rl.qualification import QualificationRejected
    q=service(tmp_path);task=task_fixture(q.store)
    with pytest.raises(QualificationRejected):q.verify_accepted(task,task)




def test_qualification_does_not_create_or_reconcile_jobs(tmp_path,monkeypatch):
    q=service(tmp_path);task=task_fixture(q.store)
    def obsolete(*args,**kwargs):
        raise AssertionError('qualification must not replay or reconcile Registry jobs')
    for method in ('enqueue','claim','reconcile','complete','accounting','trace'):
        monkeypatch.setattr(q.registry,method,obsolete)
    result=q.qualify(task)
    assert [ref.kind for ref in result.artifacts]==['QualificationReport']
    assert result.disposition==c.Disposition.PROVISIONAL


def test_qualification_preserves_existing_validation_dependencies(tmp_path, monkeypatch):
    """Registry regression only; the B/H validator and package are unit doubles."""
    from types import SimpleNamespace
    from feature_rl.qualification import service as module
    q = service(tmp_path)
    task_ref = task_fixture(q.store)
    task = q.store.get_artifact(task_ref)
    verifier = q.store.get_artifact(task.private_oracle)
    run_evidence = q.store.put_bytes(b'UNIT run evidence', 'unit-execution', c.Visibility.PRIVATE)
    pair = q.store.get_artifact(task.source_pair)
    run = lambda source: SimpleNamespace(source=source, build=run_evidence,
        executions=(run_evidence,), outputs=(verifier.cases[0].expected,))
    proof = SimpleNamespace(source_pair=task.source_pair, environment=task.environment,
        inputs=(verifier.cases[0].inputs,), expected=(verifier.cases[0].expected,),
        baseline=run(pair.baseline), reference=run(pair.reference), repeated_reference=run(pair.reference))
    monkeypatch.setattr(module, 'validate_reference', lambda *args: proof)
    q.builder = SimpleNamespace(solver_package=lambda ref: b'UNIT package')
    q.registry.register(task_ref)
    before = q.registry.register(verifier.validation)
    result = q.qualify(task_ref)
    assert result.disposition == c.Disposition.SUCCESS
    assert q.registry.register(verifier.validation) == before
    assert result.artifacts[0] in q.registry.trace(run_evidence).artifacts
