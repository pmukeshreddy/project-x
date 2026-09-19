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
from feature_rl.qualification import (QualificationRejected,ReviewRequest,DetachedAttestation,
    SSHHumanVerifier,derive_reference,assess_outcome)
from feature_rl.qualification.evidence import put_record,_assert_case_observation
from feature_rl.registry import QuarantinedError
from feature_rl.submission.source import Submission
from feature_rl.verifiers import load_verifier
from feature_rl.verifiers.loader import read_local
from feature_rl.verifiers.models import CaseComparison,RealizedCase
from test_qualification_service import service
from m5_fixtures import task_fixture


def process_observations(tmp_path,*,exit_code,stderr=b'',mode='process'):
    q=service(tmp_path);task_ref=task_fixture(q.store);task=q.store.get_artifact(task_ref)
    origin=q.store.get_artifact(task.contract).requirements[0].evidence[0]
    marker=put_record(q.store,{'synthetic':'process-comparison diagnostic; not worker evidence'},'unit-diagnostic')
    adapter=b'# Synthetic fixed adapter identity; never executed in this test'
    comparisons=[];cases=[]
    for index,requirement in enumerate(('F1','C1')):
        observations=[{'name':'stdout','type':'string'}] if mode=='process' else [{'name':'present','type':'boolean'}]
        comparison=CaseComparison.model_validate_json(json.dumps({'version':'m4-comparison-v1',
            'scenario_id':'s'+str(index),'requirement_ids':[requirement],'mode':mode,'observations':observations,
            'assertions':[{'assertion_id':'value','requirement_ids':[requirement],'oracle_origin':origin.model_dump(mode='json'),
                'actual':'stdout' if mode=='process' else 'present','operator':'equal',
                'expected':{'kind':'literal','value':'expected' if mode=='process' else True}}], 'timeout_seconds':2.0}))
        cref=put_record(q.store,comparison,'m4-case-comparison')
        case=RealizedCase(case_id='c'+str(index),scenario_id='s'+str(index),requirement_ids=(requirement,),
            inputs={},input_plan=marker,comparison=cref,mandatory=True)
        code=exit_code if index==0 else 0
        output=(b'' if index==0 else b'expected') if mode=='process' else canonical_json({'case_id':case.case_id,'observations':{'present':index!=0}})
        stdin=canonical_json({'case_id':case.case_id,'inputs':{}})
        owner=Ownership(operation_id=str(index+1)*32,owner_token='a'*32,daemon_id='synthetic',
            container_name='synthetic-'+str(index),container_id='synthetic-id-'+str(index),phase='removed',
            binding={},saved_source={},created_at='2026-09-19T00:00:00Z')
        row={'argv':['diagnostic-docker','exec',owner.container_id,'python','-c',adapter.decode()],
            'exit_code':code,'reason':'exited','stdin_bytes':len(stdin),'stdin_sha256':hashlib.sha256(stdin).hexdigest(),
            'stdout_b64':base64.b64encode(output).decode(),'stderr_b64':base64.b64encode(stderr if index==0 else b'').decode()}
        value={'phase':'execute','record':owner.model_dump(mode='json'),'cleanup_verified':True,'commands':[row],
            'extra':{'error':None,'failure_category':'candidate' if code else 'none','reason':'command_failed' if code else 'completed'}}
        ref=put_record(q.store,value,'environment-execution')
        actual=CaseResult(case_id=case.case_id,mandatory=True,status='completed',passed=index!=0,
            assertions=(AssertionResult(assertion_id='value',requirement_ids=(requirement,),passed=index!=0),),
            evidence=ref,reason='compared externally')
        comparisons.append(comparison);cases.append(actual)
        checked=SimpleNamespace(adapter=adapter,verifier=SimpleNamespace(permissions=SimpleNamespace(output_limit_bytes=65536)))
        _assert_case_observation(checked,actual,case,comparison,value,owner)  # M4's process observation remains valid.
    checked.comparisons=tuple(comparisons)
    checked.contract=SimpleNamespace(requirements=(SimpleNamespace(requirement_id='F1',mandatory=True),),
        compatibility_obligations=(SimpleNamespace(requirement_id='C1',mandatory=True),))
    receipt=SimpleNamespace(cases=tuple(cases),reward=0,cleanup_verified=True,build_evidence=marker,
        disposition=c.Disposition.REJECTED,reason='required comparison failed')
    return q,checked,receipt


@pytest.mark.parametrize('stderr',[b'SyntaxError: invalid syntax\n',b'ModuleNotFoundError: No module named required_dependency\n'])
def test_process_crash_is_not_semantic_omission_even_when_m4_completed_and_only_f1_failed(tmp_path,stderr):
    q,checked,receipt=process_observations(tmp_path,exit_code=1,stderr=stderr)
    assert not assess_outcome(checked,receipt,'semantic_negative',('F1',)).passed


@pytest.mark.parametrize('exit_code,stderr,want',[(1,b'SyntaxError: invalid syntax\n',False),
    (1,b'ModuleNotFoundError: No module named required_dependency\n',False),
    (0,b'',True)])
def test_targeted_process_semantics_are_bound_to_actual_normal_completion(tmp_path,exit_code,stderr,want):
    q,checked,receipt=process_observations(tmp_path,exit_code=exit_code,stderr=stderr)
    outcome=assess_outcome(checked,receipt,'semantic_negative',('F1',),store=q.store)
    assert outcome.passed is want
    if not want:assert outcome.code!='environment_failure'


def test_exact_absent_new_public_symbol_can_be_an_explicit_successful_adapter_observation(tmp_path):
    q,checked,receipt=process_observations(tmp_path,exit_code=0,mode='json')
    assert assess_outcome(checked,receipt,'semantic_negative',('F1',),store=q.store).passed


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
def test_run_config_consumes_source_delta_and_quarantine_blocks_selected_reuse(tmp_path,visibility):
    q,checked,projection,submission,delta,parent=source_run(tmp_path,visibility)
    # This synthetic recipe has no runtime policy, so actual M4 returns an
    # unsupported receipt before any workspace/engine invocation.
    binding,result,receipt=q._run(checked,projection,submission,11,'fresh_0','positive',(),parent,False,set())
    assert receipt.disposition==c.Disposition.UNSUPPORTED
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


def failed_attestation(tmp_path):
    q=service(tmp_path);task=task_fixture(q.store);report=q.qualify(task).artifacts[0]
    now=datetime.now(timezone.utc)
    request=ReviewRequest(task=task,report=report,policy=q.policy_ref,challenge='0'*64,
        issued_at=now,expires_at=now+timedelta(hours=1),qualification_job=q._job(task,'m5-qualify').job_id)
    request_ref=put_record(q.store,request,'m5-review-request')
    q.registry.register(request_ref,dependencies=(task,report,q.policy_ref))
    payload=q.store.put_bytes(b'{"actor_type":"model","diagnostic":"NO_TASK_APPROVAL"}', 'm5-human-review-payload',c.Visibility.PRIVATE)
    signature=q.store.put_bytes(b'not a signature; synthetic failed verification only','m5-sshsig',c.Visibility.PRIVATE)
    attestation=put_record(q.store,DetachedAttestation(payload=payload,signature=signature),'m5-sshsig-attestation')
    # Simulate an already-registered opaque envelope. Its dependencies must not
    # be retrofitted; new M5 records and current leaf checks provide the boundary.
    q.registry.register(attestation)
    q.attestation_verifier=SSHHumanVerifier(enrollment_path=tmp_path/'absent-enrollment',expected_enrollment_sha256='0'*64)
    return q,request_ref,attestation,payload,signature


@pytest.mark.parametrize('leaf',['payload','signature'])
def test_failed_verification_records_consumed_leaves_and_quarantine_blocks_reuse(tmp_path,leaf):
    from feature_rl.qualification.admission import _verification
    q,request,attestation,payload,signature=failed_attestation(tmp_path)
    with pytest.raises(QualificationRejected,match='payload schema/origin invalid'):_verification(q,request,attestation)
    ref=payload if leaf=='payload' else signature
    trace=q.registry.trace(ref)
    jobs=[q.registry.job(job) for job in trace.jobs if q.registry.job(job).spec.invocation=='m5-human-verification']
    assert len(jobs)==1 and jobs[0].result.disposition==c.Disposition.PROVISIONAL
    output=jobs[0].result.artifacts[0]
    q.registry.quarantine(ref,notice_id='synthetic-signed-leaf',reason='Synthetic consumed-leaf quarantine',evidence=(ref,))
    with pytest.raises(QuarantinedError):q.registry.assert_usable(output)
    with pytest.raises(QuarantinedError):_verification(q,request,attestation)
    assert all(r.kind!='QualificationReport' for r in jobs[0].result.artifacts)


def test_signature_quarantine_is_checked_before_native_verification_attempt(tmp_path):
    from feature_rl.qualification.admission import _verification
    q,request,attestation,payload,signature=failed_attestation(tmp_path)
    q.registry.register(signature)
    q.registry.quarantine(signature,notice_id='synthetic-before-verify',reason='Synthetic preexisting signature quarantine',evidence=(signature,))
    before=q.registry.trace(request).jobs
    with pytest.raises(QuarantinedError):_verification(q,request,attestation)
    assert q.registry.trace(request).jobs==before
