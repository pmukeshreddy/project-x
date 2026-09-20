"""Concrete M5 wiring; diagnostics do not create human approval or accepted Q."""
import pytest

from feature_rl import contracts as c
from feature_rl.pipeline import Factory, AdmissionRejected
from feature_rl.qualification import QualificationService, QualificationPolicy
from feature_rl.environments import EnvironmentRuntime, SandboxPolicy
from feature_rl.grading import GradingService
from test_factory import fixture


def configured(tmp_path,monkeypatch):
    _,store,registry,builder,inputs,*_=fixture(tmp_path,monkeypatch)
    assert hasattr(Factory,'qualify'), 'Factory qualification orchestration is missing'
    runtime=object.__new__(EnvironmentRuntime)
    runtime.store=store;runtime.policy=SandboxPolicy();runtime.revision='b'*40
    grader=GradingService(store=store,runtime=runtime,revision='c'*40)
    # Actual M5 missing package-validator denial runs without a worker/backend.
    q=QualificationService(store=store,registry=registry,grader=grader,builder=None,revision='d'*40)
    factory=Factory(store=store,registry=registry,builder=builder,qualification=q,revision='e'*40)
    candidate=store.get_artifact(inputs.source_pair).candidate
    built=factory.construct(candidate,inputs=inputs)
    return factory,built


def test_actual_m5_receives_selected_history_and_retains_failure_classification(tmp_path,monkeypatch):
    factory,built=configured(tmp_path,monkeypatch)
    result=factory.qualify(built.artifacts[0])
    assert result.disposition==c.Disposition.INFRASTRUCTURE
    task=factory.store.get_artifact(built.artifacts[0])
    jobs=[factory.registry.job(j) for j in factory.registry.trace(task.source_pair).jobs]
    qjob=next(j for j in jobs if j.spec.invocation=='m5-qualify')
    from feature_rl.pipeline.packaging import read_record
    from feature_rl.pipeline.resolver import QualificationConfiguration
    config=read_record(factory.store,qjob.spec.configuration,QualificationConfiguration,'m5-qualification-configuration')
    policy=read_record(factory.store,config.policy,QualificationPolicy,'m5-qualification-policy')
    assert policy.repair_history==built.artifacts[1]
    selected=factory.registry.job(policy.repair_history_job)
    assert selected.result==built and policy.factory_revision==selected.spec.implementation
    assert factory.qualify(built.artifacts[0])==result


def test_caller_cannot_replace_or_invent_complete_repair_history(tmp_path,monkeypatch):
    factory,built=configured(tmp_path,monkeypatch)
    wrong=factory.store.put_bytes(b'TEST ONLY nonexistent history','m5-repair-history',c.Visibility.PRIVATE)
    with pytest.raises(AdmissionRejected):
        factory.qualify(built.artifacts[0],policy=QualificationPolicy(repair_history=wrong,
            repair_history_job='f'*64,factory_revision='f'*40))


def test_provisional_report_cannot_release_through_actual_m5(tmp_path,monkeypatch):
    factory,built=configured(tmp_path,monkeypatch)
    report=factory.qualify(built.artifacts[0]).artifacts[0]
    result=factory.release(built.artifacts[0],accepted_report=report)
    assert result.disposition==c.Disposition.PROVISIONAL
    assert all(ref.kind!='TaskBundle' for ref in result.artifacts)


def test_mechanical_release_uses_both_real_registry_transitions_under_test_gate(tmp_path,monkeypatch):
    factory,built=configured(tmp_path,monkeypatch)
    report=factory.qualify(built.artifacts[0]).artifacts[0]
    def diagnostic_gate(q,task,selected):
        assert selected==report
        return q.store.get_artifact(report)  # original nonaccepted report; no human/accepted Q is fabricated
    monkeypatch.setattr(QualificationService,'verify_accepted',diagnostic_gate)
    result=factory.release(built.artifacts[0],accepted_report=report)
    assert result.disposition==c.Disposition.SUCCESS
    assert factory.store.get_artifact(result.artifacts[0]).state==c.TaskState.RELEASED
    assert factory.release(built.artifacts[0],accepted_report=report)==result
    jobs=[factory.registry.job(j) for j in factory.registry.trace(report).jobs]
    assert {'m6-transition-qualified','m6-transition-released'}<={j.spec.invocation for j in jobs}


def test_accept_uses_frozen_request_policy_and_actual_human_denial(tmp_path,monkeypatch):
    factory,built=configured(tmp_path,monkeypatch)
    result=factory.qualify(built.artifacts[0])
    from datetime import timedelta
    from feature_rl.qualification import ReviewRequest
    from feature_rl.artifacts import canonical_json
    report=factory.store.get_artifact(result.artifacts[0])
    qjob=next(factory.registry.job(j) for j in factory.registry.trace(built.artifacts[0]).jobs
        if factory.registry.job(j).spec.invocation=='m5-qualify')
    # Deliberately unselected TEST request: actual M5 must deny it, not ask for approval.
    model=ReviewRequest(task=built.artifacts[0],report=result.artifacts[0],policy=report.provenance.inputs[1],
        challenge='a'*64,issued_at=report.provenance.created_at,
        expires_at=report.provenance.created_at+timedelta(seconds=60),qualification_job=qjob.job_id)
    request=factory.store.put_bytes(canonical_json(model.model_dump(mode='json')),'m5-review-request',c.Visibility.PRIVATE)
    attestation=factory.store.put_bytes(b'TEST ONLY not a human signature','m5-sshsig-attestation',c.Visibility.PRIVATE)
    from feature_rl.qualification import QualificationRejected
    with pytest.raises(QualificationRejected):factory.accept(request,attestation)
