"""Authenticate exact frozen review subjects; never authorize from report booleans.

Only this boundary can add HUMAN claims, after an externally administered SSHSIG
verifier succeeds. There is no signing/enrollment-writing API or test bypass.
"""
from datetime import datetime, timezone
import time
from feature_rl import contracts as c
from feature_rl.artifacts import ArtifactError, canonical_json
from feature_rl.environments import SourceArchive
from feature_rl.registry import JobSpec, RegistryError
from feature_rl.verifiers import load_verifier
from feature_rl.verifiers.loader import read_local
from .attestation import SSHHumanVerifier
from .controls import assess_outcome, validate_control_plan
from .evidence import (put_record, evidence, collapse_costs, unknown_cost,
    validate_grade, validate_reset)
from .models import (QualificationRejected, ReferenceProjection, ReviewRequest,
    RunBinding, QualificationSummary, DetachedAttestation, VerifiedAttestation,
    QualificationPublicationFailed, FrozenPublication)
from .projection import derive_reference

HUMAN_MISSING='unverified_human_review: external authenticated human review is required'
ADDITIONS={'human_reviews','disposition','rejection_reasons','provenance','costs'}


def _compare_payload(left,right,excluded,detail):
    try:
        same=canonical_json(left.model_dump(mode='json',exclude=excluded))==canonical_json(right.model_dump(mode='json',exclude=excluded))
    except (TypeError,ValueError):same=False
    if not same:raise QualificationRejected('invalid_evidence',detail)


def assert_frozen_evidence(reviewed,accepted):
    """Pure invariant only; passing this comparison does not grant admission."""
    _compare_payload(reviewed,accepted,ADDITIONS,'accepted report changed frozen reviewed evidence')


def assert_lifecycle_subject(built,later):
    """M6 separately authenticates legal transition records for any later state."""
    _compare_payload(built,later,{'state','qualification'},'later manifest changed the exact BUILT subject payload')


def _verifier(service):
    if type(service.attestation_verifier) is not SSHHumanVerifier:
        raise QualificationRejected('unverified_human_review','configured external SSHHumanVerifier is required; callbacks and identity strings are not authentication')
    return service.attestation_verifier


def _artifact(service,ref,kind):
    service.registry.assert_usable(ref)
    item=service.store.get_artifact(ref,max_envelope_bytes=4*1024*1024)
    if type(item) is not kind:raise QualificationRejected('invalid_evidence','wrong qualification artifact type')
    return item


def _spec(service,inputs,invocation,operation='qualify',configuration=None):
    return JobSpec(operation=operation,inputs=inputs,configuration=configuration or service.configuration,
        implementation=service.revision,invocation=invocation,attempt_limit=3)


def _selected(service,job_id,spec,output):
    job=service.registry.job(job_id)
    if job.spec!=spec or job.state!='completed' or job.result is None or output not in job.result.artifacts:
        raise QualificationRejected('invalid_evidence','evidence is not selected by its exact completed controller operation')
    return job.result


def expected_runs(service,checked,projection):
    missing,targets=validate_control_plan(checked,service.policy)
    baseline=service.grader.submissions.create(checked.task.baseline,SourceArchive({}).to_tar(),(),checked.contract.allowed_changes)
    runs=[('baseline_absence',baseline,service.policy.fresh_seeds[0],'semantic_negative',targets,False)]
    runs.extend(('fresh_'+str(i),projection.submission,s,'positive',(),False) for i,s in enumerate(service.policy.fresh_seeds))
    by_id={d.control_id:d for d in service.policy.controls}
    for control in checked.verifier.controls:
        d=by_id.get(control.control_id)
        if d is not None and d.validity not in {'equivalent','unresolved'}:
            runs.append(('control_'+control.control_id,control.patch,service.policy.fresh_seeds[0],d.mode,d.targets,False))
    runs.extend(('reset_'+str(i),projection.submission,s,'positive',(),True) for i,s in enumerate(service.policy.reset_seeds))
    return tuple(runs),missing


def _gate(service,gate,name,task,binding,targets,outcome):
    if gate is None or (gate.name,gate.subject,gate.disposition,gate.passed,gate.requirement_ids,gate.reason)!=(name,task,c.Disposition.SUCCESS,True,targets,outcome.detail) or not outcome.passed:
        raise QualificationRejected('invalid_evidence','frozen gate does not match actual scheduled outcome: '+name)
    if len(gate.evidence)!=1:
        raise QualificationRejected('invalid_evidence','unexpected gate evidence replacement: '+name)
    ev=gate.evidence[0]
    command_name='baseline_absence' if name=='baseline_health' else name
    if (ev.producer,ev.revision,ev.exit_status,ev.scope,ev.artifacts,ev.command)!=('feature_rl.qualification',service.revision,0,'real_integration',(binding,),('QualificationService.execute',command_name,task.sha256)):
        raise QualificationRejected('invalid_evidence','gate receipt provenance differs from reviewed execution: '+name)


def review_context(service,request_ref):
    """Revalidate the complete produced execution package before human consumption."""
    service.registry.assert_usable(request_ref)
    request=read_local(service.store,request_ref,ReviewRequest,'m5-review-request')
    if request.policy!=service.policy_ref:
        raise QualificationRejected('invalid_evidence','review request belongs to a different frozen policy')
    result=_selected(service,request.qualification_job,_spec(service,(request.task,),'m5-qualify'),request_ref)
    if result.disposition!=c.Disposition.PROVISIONAL or not result.artifacts or result.artifacts[0]!=request.report:
        raise QualificationRejected('invalid_evidence','review request is not the selected provisional package')
    report=_artifact(service,request.report,c.QualificationReport)
    if report.task!=request.task or report.disposition!=c.Disposition.PROVISIONAL or report.human_reviews or report.rejection_reasons!=(HUMAN_MISSING,) or report.policy_version!=service.policy.policy_id:
        raise QualificationRejected('invalid_evidence','review package has incomplete or substituted qualification gates')
    if report.provenance.producer!='feature_rl.qualification' or report.provenance.producer_version!=service.revision or len(report.provenance.inputs)!=3:
        raise QualificationRejected('invalid_evidence','review package origin/revision mismatch')
    task_ref,policy_ref,summary_ref=report.provenance.inputs
    if (task_ref,policy_ref)!=(request.task,request.policy) or summary_ref not in result.artifacts:
        raise QualificationRejected('invalid_evidence','review package summary/policy/task mismatch')
    service.registry.assert_usable(summary_ref)
    summary=read_local(service.store,summary_ref,QualificationSummary,'m5-qualification-summary',4*1024*1024)
    if (summary.task,summary.policy,summary.qualification_job)!=(request.task,request.policy,request.qualification_job) or summary.issues!=(HUMAN_MISSING,) or summary.projection is None:
        raise QualificationRejected('invalid_evidence','review summary does not bind the complete frozen package')
    if summary.wall_seconds is None or summary.wall_seconds>service.policy.max_wall_seconds:
        raise QualificationRejected('budget_exhausted','review package lacks a completed bounded qualification wall measurement')
    checked=load_verifier(service.store,request.task)
    if checked.task.state!=c.TaskState.BUILT or checked.task.qualification is not None:
        raise QualificationRejected('invalid_evidence','Q.task must be exact unqualified BUILT T0')
    if any(a.disposition=='unresolved' for a in checked.contract.ambiguities):
        raise QualificationRejected('ambiguous_requirement','unresolved frozen contract ambiguity')
    if service.builder is None:raise QualificationRejected('provisional','actual M6 frozen package validator is required')
    service.builder.solver_package(request.task)
    service.registry.assert_usable(summary.projection)
    projection=read_local(service.store,summary.projection,ReferenceProjection,'m5-reference-projection',1024*1024)
    if projection!=derive_reference(service.store,request.task,service.grader.runtime.policy):
        raise QualificationRejected('invalid_evidence','reviewed H projection differs from exact B/H bytes and policy')
    count,missing_history=service._history(checked)
    if missing_history or count is None or report.repair_attempts!=count or summary.repair_count!=count:
        raise QualificationRejected('invalid_evidence','complete selected global repair history is required for acceptance')
    runs,missing=expected_runs(service,checked,projection)
    if missing:raise QualificationRejected('provisional','; '.join(missing))
    if len(runs)>service.policy.max_grade_calls or len(summary.bindings)!=len(runs) or len(set(summary.bindings))!=len(runs):
        raise QualificationRejected('invalid_evidence','missing, excessive or replayed qualification run bindings')
    gates=[report.baseline_absence,*report.fresh_runs,*report.controls,*report.interrupted_reset_runs]
    if len(gates)!=len(runs) or report.reference_run!=(report.fresh_runs[0] if report.fresh_runs else None):
        raise QualificationRejected('invalid_evidence','incomplete frozen reference/control/reset gate ledger')
    seen=set();costs=[]
    for scheduled,binding_ref,gate in zip(runs,summary.bindings,gates):
        name,submission,seed,mode,targets,reset=scheduled
        service.registry.assert_usable(binding_ref)
        binding=read_local(service.store,binding_ref,RunBinding,'m5-run-binding')
        if (binding.name,binding.task,binding.projection,binding.submission,binding.seed,binding.mode,binding.targets)!=(name,request.task,summary.projection,submission,seed,mode,targets) or (binding.reset is not None)!=reset:
            raise QualificationRejected('invalid_evidence','scheduled run identity or source/projection mismatch')
        job=service.registry.job(binding.grade_job)
        config={'version':'m5-run-configuration-v1','configuration':service.configuration.model_dump(mode='json'),
            'projection':summary.projection.model_dump(mode='json'),'submission':submission.model_dump(mode='json'),
            'seed':seed,'name':name,'mode':mode,'targets':list(targets),'reset':reset}
        from feature_rl.verifiers.loader import read_bytes
        if read_bytes(service.store,job.spec.configuration,1024*1024,'m5-run-configuration',True)!=canonical_json(config):
            raise QualificationRejected('invalid_evidence','run configuration differs from the frozen schedule')
        actual=_selected(service,binding.grade_job,_spec(service,(request.task,),
            'm5-run:'+request.qualification_job+':'+name,'grade',job.spec.configuration),binding.grade)
        if actual.artifacts[0]!=binding.grade:
            raise QualificationRejected('invalid_evidence','run substituted its selected grade receipt')
        receipt,ids=validate_grade(service.store,checked,submission,seed,actual,service.grader,seen=seen)
        if binding.operation_ids!=ids:
            raise QualificationRejected('invalid_evidence','run operation identities differ from actual runtime')
        if reset:
            if binding.reset not in actual.artifacts:
                raise QualificationRejected('invalid_evidence','reset was not produced by this selected grade operation')
            validate_reset(service.store,checked,summary.projection,binding.reset,service.grader,seen=seen)
        outcome=assess_outcome(checked,receipt,mode,targets)
        _gate(service,gate,name,request.task,binding_ref,targets,outcome)
        if name=='baseline_absence':
            health=assess_outcome(checked,receipt,'baseline_health',())
            _gate(service,report.baseline_health,'baseline_health',request.task,binding_ref,
                tuple(r.requirement_id for r in checked.contract.compatibility_obligations if r.mandatory),health)
        costs.extend(actual.costs)
    if report.costs!=collapse_costs((*costs,unknown_cost())):
        raise QualificationRejected('invalid_evidence','review package substituted original execution costs')
    return request,report,checked


def _verification(service,request_ref,attestation_ref):
    """Bounded, selected native verification attempt. Failed signatures do not consume admission."""
    verifier=_verifier(service)
    spec=_spec(service,(request_ref,attestation_ref),'m5-human-verification','audit')
    jobs=[service.registry.job(j) for j in service.registry.trace(request_ref).jobs]
    attempts=[j for j in jobs if j.spec.invocation=='m5-human-verification' and j.spec.inputs[0]==request_ref]
    if not any(j.spec==spec for j in attempts) and len(attempts)>=3:
        raise QualificationRejected('budget_exhausted','at most three distinct human signature-verification attempts per frozen review request')
    job=service.registry.enqueue(spec)
    if job.state=='completed':
        if job.result.disposition!=c.Disposition.SUCCESS:
            raise QualificationRejected('unverified_human_review',job.result.reason)
        return job.result,job.job_id
    claim=service._claim(job);service._intent(claim);started=time.monotonic();cpu=time.process_time()
    payload=None;verified=None;failure=None
    try:payload,verified=verifier.verify(service.store,request_ref,attestation_ref)
    except QualificationRejected as exc:failure={'code':exc.code,'detail':exc.detail,'verification':getattr(exc,'verification',None)}
    now=datetime.now(timezone.utc)
    frozen={'request':request_ref,'attestation':attestation_ref,'payload':payload,'verification':verified,
        'failure':failure,'recorded_at':now,'wall_seconds':max(0.0,time.monotonic()-started),'cpu_seconds':max(0.0,time.process_time()-cpu)}
    try:result=publish_verification(service,claim,frozen)
    except QualificationPublicationFailed:raise
    except Exception as exc:
        raise QualificationPublicationFailed('retain native verification and costs without signing or re-verifying',
            pending=FrozenPublication(claim,frozen,'human_verification'),claim=claim) from exc
    if result.disposition!=c.Disposition.SUCCESS:raise QualificationRejected('unverified_human_review',result.reason)
    return result,job.job_id


def publish_verification(service,claim,frozen):
    value={'version':'m5-human-verification-v1','request':frozen['request'].model_dump(mode='json'),
        'attestation':frozen['attestation'].model_dump(mode='json'),'verified_at':frozen['recorded_at'].isoformat(),
        'verification':frozen['verification'],'failure':frozen['failure']}
    ref=put_record(service.store,value,'m5-human-verification')
    service.registry.register(ref,dependencies=(frozen['request'],frozen['attestation']))
    ev=evidence(ref,service.revision,('SSHHumanVerifier.verify',frozen['request'].sha256),scope='real_integration').model_copy(update={'recorded_at':frozen['recorded_at']})
    cost=c.CostRecord(category='verifier',wall_seconds=frozen['wall_seconds'],cpu_seconds=frozen['cpu_seconds'],
        gpu_seconds=None,input_tokens=None,output_tokens=None,human_minutes=None,usd=None,measurement='partial',
        note='Measured native external signature verification; no signing or synthetic human minutes')
    result=c.OperationResult(operation='audit',disposition=c.Disposition.SUCCESS if frozen['failure'] is None else c.Disposition.PROVISIONAL,
        artifacts=(ref,),evidence=(ev,),costs=(cost,unknown_cost('storage','Verification publication overhead unmeasured')),
        reason='Externally enrolled human signature verified' if frozen['failure'] is None else 'unverified_human_review: '+frozen['failure']['detail'])
    return service._complete(claim,result)


def accept(service,review_request,attestation):
    _verifier(service)
    try:return _accept(service,c.ArtifactRef.model_validate(review_request),c.ArtifactRef.model_validate(attestation))
    except QualificationRejected:raise
    except (ArtifactError,RegistryError,ValueError) as exc:
        raise QualificationRejected('invalid_evidence','admission dependency/selected origin rejected: '+str(exc)[:1000]) from exc


def _accept(service,request_ref,attestation_ref):
    request,report,checked=review_context(service,request_ref)
    # The stable consumption identity omits the signature: one challenge may
    # select one attestation only. Verification attempts have separate audit jobs.
    job=service.registry.enqueue(_spec(service,(request_ref,),'m5-accept'))
    if job.state=='completed':
        accepted=verify_accepted(service,request.task,job.result.artifacts[0])
        verified=read_local(service.store,accepted.provenance.inputs[-1],VerifiedAttestation,'m5-verified-attestation')
        if verified.attestation!=attestation_ref:
            raise QualificationRejected('invalid_evidence','frozen challenge already consumed by a different attestation')
        return job.result
    if job.state!='queued':service._claim(job)  # Refuse ambiguous redispatch.
    verification,verification_job=_verification(service,request_ref,attestation_ref)
    envelope=read_local(service.store,attestation_ref,DetachedAttestation,'m5-sshsig-attestation')
    from feature_rl.verifiers.language import decode_json
    from feature_rl.verifiers.loader import read_bytes
    native=decode_json(read_bytes(service.store,verification.artifacts[0],1024*1024,'m5-human-verification',True),1024*1024)
    consumed=datetime.fromisoformat(native['verified_at'])
    # Recheck current external enrollment/revocation immediately before selection;
    # the original successful verification time controls challenge expiry.
    payload,_=_verifier(service).verify(service.store,request_ref,attestation_ref,consumed_at=consumed)
    claim=service._claim(service.registry.job(job.job_id));service._intent(claim)
    frozen={'request_ref':request_ref,'request':request,'report':report,'attestation':attestation_ref,
        'envelope':envelope,'payload':payload,'verification':verification,'verification_job':verification_job,
        'consumed_at':consumed,'recorded_at':datetime.now(timezone.utc)}
    try:return publish_admission(service,claim,frozen)
    except QualificationPublicationFailed:raise
    except Exception as exc:
        raise QualificationPublicationFailed('retain exact accepted additions without replacing reviewed evidence',
            pending=FrozenPublication(claim,frozen,'admission'),claim=claim) from exc


def publish_admission(service,claim,frozen):
    request,report,_=review_context(service,frozen['request_ref'])
    payload,_=_verifier(service).verify(service.store,frozen['request_ref'],frozen['attestation'],consumed_at=frozen['consumed_at'])
    if request!=frozen['request'] or report!=frozen['report'] or payload!=frozen['payload']:
        raise QualificationRejected('invalid_evidence','retained admission changed its authenticated review package or payload')
    binding=VerifiedAttestation(request=frozen['request_ref'],attestation=frozen['attestation'],payload=frozen['envelope'].payload,
        verification=frozen['verification'].artifacts[0],consumed_at=frozen['consumed_at'],
        verification_job=frozen['verification_job'],admission_job=claim.job_id)
    ref=put_record(service.store,binding,'m5-verified-attestation')
    service.registry.register(ref,dependencies=(binding.request,binding.attestation,binding.payload,binding.verification))
    ev=evidence(ref,service.revision,('QualificationService.accept',request.task.sha256,request.report.sha256),scope='real_integration').model_copy(update={'recorded_at':frozen['recorded_at']})
    human=c.HumanReview(actor_type='human',human_identity=payload.human_identity,subject_sha256=request.task.sha256,
        decision='approved',evidence=(ev,),attestation=frozen['envelope'].payload)
    human_cost=c.CostRecord(category='human_review',wall_seconds=None,cpu_seconds=None,gpu_seconds=None,input_tokens=None,
        output_tokens=None,human_minutes=payload.human_minutes,usd=None,measurement='partial',note='Minutes declared in externally authenticated human review payload')
    data=report.model_dump(mode='json')
    data.update(disposition=c.Disposition.SUCCESS.value,human_reviews=[human.model_dump(mode='json')],rejection_reasons=[],
        provenance=c.Provenance(producer='feature_rl.qualification.admission',producer_version=service.revision,created_at=frozen['recorded_at'],
            inputs=(request.report,frozen['request_ref'],frozen['attestation'],ref),evidence=(ev,)).model_dump(mode='json'),
        costs=[x.model_dump(mode='json') for x in collapse_costs((*report.costs,*frozen['verification'].costs,human_cost,unknown_cost('storage','Admission publication and current trust recheck overhead unmeasured')))])
    accepted=c.QualificationReport.model_validate_json(canonical_json(data))
    assert_frozen_evidence(report,accepted)
    accepted_ref=service.store.put_artifact(accepted)
    service.registry.assert_usable(request.task)
    result=c.OperationResult(operation='qualify',disposition=c.Disposition.SUCCESS,artifacts=(accepted_ref,ref),evidence=(ev,),
        costs=collapse_costs((*frozen['verification'].costs,human_cost,unknown_cost('storage','Admission publication and current trust recheck overhead unmeasured'))),
        reason='accepted: exact frozen task, reviewed evidence and external human attestation verified')
    return service._complete(claim,result)


def validate_pending_admission(service,result):
    """Publication retries still consult current external trust and quarantine."""
    accepted=_artifact(service,result.artifacts[0],c.QualificationReport)
    if accepted.disposition!=c.Disposition.SUCCESS or accepted.provenance.producer!='feature_rl.qualification.admission':
        raise QualificationRejected('invalid_evidence','retained successful result is not an M5 admission')
    binding=read_local(service.store,accepted.provenance.inputs[-1],VerifiedAttestation,'m5-verified-attestation')
    request,reviewed,_=review_context(service,binding.request)
    assert_frozen_evidence(reviewed,accepted)
    if accepted.task!=request.task:raise QualificationRejected('invalid_evidence','retained accepted task changed')
    payload,_=_verifier(service).verify(service.store,binding.request,binding.attestation,consumed_at=binding.consumed_at)
    envelope=read_local(service.store,binding.attestation,DetachedAttestation,'m5-sshsig-attestation')
    if len(accepted.human_reviews)!=1 or binding.payload!=envelope.payload:
        raise QualificationRejected('invalid_evidence','retained human evidence was substituted')
    human=accepted.human_reviews[0]
    if (human.actor_type,human.human_identity,human.subject_sha256,human.decision,human.attestation)!=('human',payload.human_identity,request.task.sha256,'approved',envelope.payload):
        raise QualificationRejected('invalid_evidence','retained human claim differs from the actual signed payload')


def verify_accepted(service,task_ref,report_ref):
    _verifier(service)
    try:return _verify_accepted(service,c.ArtifactRef.model_validate(task_ref),c.ArtifactRef.model_validate(report_ref))
    except QualificationRejected:raise
    except (ArtifactError,RegistryError,ValueError) as exc:
        raise QualificationRejected('invalid_evidence','accepted qualification dependency/origin rejected: '+str(exc)[:1000]) from exc


def _verify_accepted(service,task_ref,report_ref):
    accepted=_artifact(service,report_ref,c.QualificationReport)
    if accepted.disposition!=c.Disposition.SUCCESS or accepted.provenance.producer!='feature_rl.qualification.admission' or accepted.provenance.producer_version!=service.revision or len(accepted.provenance.inputs)!=4:
        raise QualificationRejected('invalid_evidence','accepted report lacks actual admission origin')
    reviewed_ref,request_ref,attestation_ref,binding_ref=accepted.provenance.inputs
    binding=read_local(service.store,binding_ref,VerifiedAttestation,'m5-verified-attestation')
    if (binding.request,binding.attestation)!=(request_ref,attestation_ref):
        raise QualificationRejected('invalid_evidence','accepted attestation selection mismatch')
    selection=_selected(service,binding.admission_job,_spec(service,(request_ref,),'m5-accept'),report_ref)
    if selection.disposition!=c.Disposition.SUCCESS or selection.artifacts[:2]!=(report_ref,binding_ref):
        raise QualificationRejected('invalid_evidence','accepted report is not the original selected admission result')
    request,reviewed,checked=review_context(service,request_ref)
    if (accepted.task,reviewed_ref)!=(request.task,request.report):
        raise QualificationRejected('invalid_evidence','accepted Q.task is not exact reviewed BUILT T0')
    assert_frozen_evidence(reviewed,accepted)
    later=_artifact(service,task_ref,c.TaskBundle)
    assert_lifecycle_subject(checked.task,later)
    if task_ref!=request.task and (later.state not in {c.TaskState.QUALIFIED,c.TaskState.CALIBRATED,c.TaskState.RELEASED} or later.qualification!=report_ref):
        raise QualificationRejected('invalid_evidence','later task does not reference this exact accepted Q')
    if len(accepted.human_reviews)!=1:
        raise QualificationRejected('invalid_evidence','unexpected human review substitution')
    native_result=_selected(service,binding.verification_job,_spec(service,(request_ref,attestation_ref),'m5-human-verification','audit'),binding.verification)
    if native_result.disposition!=c.Disposition.SUCCESS:
        raise QualificationRejected('unverified_human_review','selected native signature check was not successful')
    from feature_rl.verifiers.language import decode_json
    from feature_rl.verifiers.loader import read_bytes
    native=decode_json(read_bytes(service.store,binding.verification,1024*1024,'m5-human-verification',True),1024*1024)
    if native.get('verified_at')!=binding.consumed_at.isoformat() or native.get('failure') is not None or native.get('verification',{}).get('human_origin_verified') is not True:
        raise QualificationRejected('invalid_evidence','consumed challenge time lacks selected native verification')
    payload,verification=_verifier(service).verify(service.store,request_ref,attestation_ref,consumed_at=binding.consumed_at)
    envelope=read_local(service.store,attestation_ref,DetachedAttestation,'m5-sshsig-attestation')
    if binding.payload!=envelope.payload:
        raise QualificationRejected('invalid_evidence','accepted payload differs from the exact detached attestation')
    human=accepted.human_reviews[0]
    if (human.actor_type,human.human_identity,human.subject_sha256,human.decision,human.attestation)!=('human',payload.human_identity,request.task.sha256,'approved',binding.payload) or len(human.evidence)!=1 or human.evidence[0]!=accepted.provenance.evidence[0] or human.evidence[0].artifacts!=(binding_ref,):
        raise QualificationRejected('invalid_evidence','accepted HUMAN claim differs from authenticated payload and review evidence')
    expected_human=c.CostRecord(category='human_review',wall_seconds=None,cpu_seconds=None,gpu_seconds=None,input_tokens=None,
        output_tokens=None,human_minutes=payload.human_minutes,usd=None,measurement='partial',note='Minutes declared in externally authenticated human review payload')
    expected_costs=collapse_costs((*reviewed.costs,*native_result.costs,expected_human,unknown_cost('storage','Admission publication and current trust recheck overhead unmeasured')))
    incremental=collapse_costs((*native_result.costs,expected_human,unknown_cost('storage','Admission publication and current trust recheck overhead unmeasured')))
    if accepted.costs!=expected_costs or selection.costs!=incremental:
        raise QualificationRejected('invalid_evidence','accepted costs changed authenticated human minutes or reviewed evidence')
    for ref in (request.task,task_ref,report_ref,reviewed_ref,request_ref,attestation_ref,binding_ref):service.registry.assert_usable(ref)
    return accepted
