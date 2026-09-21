"""Round-1 P1 reproductions on real CAS/Registry, with synthetic failed observations.

No worker is run; no key, successful human verification or approval is created.
"""
from datetime import datetime,timedelta,timezone
from types import SimpleNamespace
import base64
import hashlib
import json
import pytest
from feature_rl import contracts as c
from feature_rl.artifacts import canonical_json
from feature_rl.environments import SourceArchive
from feature_rl.environments.models import Ownership
from feature_rl.grading import AssertionResult,CaseResult
from feature_rl.grading.bootstrap import adapter_argv
from feature_rl.qualification import (QualificationRejected,derive_reference,assess_outcome)
from feature_rl.qualification.evidence import put_record,_assert_case_observation
from feature_rl.registry import QuarantinedError
from feature_rl.submission.source import Submission
from feature_rl.verifiers import load_verifier
from feature_rl.verifiers.loader import read_local
from feature_rl.verifiers.models import CaseComparison,RealizedCase
from test_qualification_service import service
from m5_fixtures import task_fixture


def source_run(tmp_path,visibility=c.Visibility.PRIVATE):
    q=service(tmp_path);task_ref=task_fixture(q.store);checked=load_verifier(q.store,task_ref)
    projection=derive_reference(q.store,task_ref,q.grader.runtime.policy)
    pref=put_record(q.store,projection,'m5-reference-projection')
    q.registry.register(pref,dependencies=(task_ref,projection.submission,projection.projected_source))
    parent=q._claim(q._job(task_ref,'m5-qualify'))
    submission=read_local(q.store,projection.submission,Submission,'m4-submission',65536)
    assigned=projection.submission
    if visibility!=c.Visibility.PRIVATE:
        assigned=q.store.put_bytes(canonical_json(submission.model_dump(mode='json')),'m4-submission',visibility)
    return q,checked,pref,assigned,submission.changes,parent


@pytest.mark.parametrize('visibility',[c.Visibility.PRIVATE,c.Visibility.PUBLIC])
def test_run_config_consumes_source_delta_and_quarantine_blocks_selected_reuse(tmp_path,visibility,monkeypatch):
    q,checked,projection,submission,delta,parent=source_run(tmp_path,visibility)
    # The complete synthetic recipe passes inert validation. Stop specifically
    # at the runtime boundary: this diagnostic has no configured worker engine.
    # M4 still constructs/publishes its real typed unsupported receipt and costs.
    def no_worker(prepared):
        raise ValueError('Synthetic unit runtime boundary: no worker configured')
    monkeypatch.setattr(q.grader.runtime,'recipe',no_worker)
    binding,result,receipt=q._run(checked,projection,submission,11,'fresh_0','positive',(),parent,False,set())
    assert receipt.disposition==c.Disposition.UNSUPPORTED
    assert 'Synthetic unit runtime boundary' in receipt.reason and not receipt.runtime_evidence
    trace=q.registry.trace(delta)
    assert binding in trace.artifacts and trace.jobs
    q.registry.quarantine(delta,notice_id='synthetic-delta-defect',reason='Synthetic source-delta quarantine regression',evidence=(delta,))
    with pytest.raises(QuarantinedError):q.registry.assert_usable(binding)
    with pytest.raises(QuarantinedError):q._run(checked,projection,submission,11,'fresh_0','positive',(),parent,False,set())


@pytest.mark.parametrize('visibility',[c.Visibility.PRIVATE,c.Visibility.PUBLIC])
def test_quarantined_source_delta_is_rejected_before_grade_job_enqueue(tmp_path,visibility):
    q,checked,projection,submission,delta,parent=source_run(tmp_path,visibility)
    q.registry.register(delta)
    q.registry.quarantine(delta,notice_id='synthetic-before-grade',reason='Synthetic preexisting quarantine',evidence=(delta,))
    before=q.registry.trace(checked.task_ref).jobs
    with pytest.raises(QuarantinedError):q._run(checked,projection,submission,11,'fresh_0','positive',(),parent,False,set())
    assert q.registry.trace(checked.task_ref).jobs==before


@pytest.mark.parametrize('defect',['malformed','wrong_baseline','wrong_delta_kind','wrong_manifest_kind'])
def test_source_dependency_manifest_must_bind_the_exact_submission(tmp_path,defect):
    q,checked,projection,submission,delta,parent=source_run(tmp_path)
    value=read_local(q.store,submission,Submission,'m4-submission',65536)
    kind='m4-submission'
    if defect=='wrong_baseline':value=value.model_copy(update={'baseline':delta})
    if defect=='wrong_delta_kind':value=value.model_copy(update={'changes':checked.task.baseline})
    if defect=='wrong_manifest_kind':kind='unit-diagnostic'
    body=b'not JSON' if defect=='malformed' else canonical_json(value.model_dump(mode='json'))
    bad=q.store.put_bytes(body,kind,c.Visibility.PRIVATE)
    with pytest.raises(QualificationRejected,match='submission'):
        q._source_dependencies(checked,bad,register=True)
