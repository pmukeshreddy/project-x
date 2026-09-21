"""Concrete M5 wiring; diagnostics do not create human approval or accepted Q."""
from m4_fixtures import runtime_policy
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
    runtime.store=store;runtime.policy=runtime_policy();runtime.revision='b'*40
    grader=GradingService(store=store,runtime=runtime,revision='c'*40)
    # Actual M5 missing package-validator denial runs without a worker/backend.
    q=QualificationService(store=store,registry=registry,grader=grader,builder=None,revision='d'*40)
    factory=Factory(store=store,registry=registry,builder=builder,qualification=q,revision='e'*40)
    candidate=store.get_artifact(inputs.source_pair).candidate
    built=factory.construct(candidate,inputs=inputs)
    return factory,built


def test_actual_m5_keeps_a_small_policy_and_reuses_the_selected_result(tmp_path,monkeypatch):
    factory,built=configured(tmp_path,monkeypatch)
    result=factory.qualify(built.artifacts[0])
    assert result.disposition!=c.Disposition.SUCCESS
    assert set(QualificationPolicy.model_fields)=={'version','policy_id','seed','max_wall_seconds'}
    assert factory.qualify(built.artifacts[0])==result


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
