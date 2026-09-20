"""Thin concrete M5/lifecycle orchestration with selected Factory history."""
from feature_rl import contracts as c
from feature_rl.qualification import QualificationPolicy, QualificationService, ReviewRequest
from .packaging import checked, read_record, typed
from .lifecycle import AdmissionRejected, TaskLifecycle
from .construction import ConstructionResult, read_construction_request


def template(factory):
    if factory.qualification is None:
        raise AdmissionRejected('configure an actual QualificationService, runtime/package verifier and external human trust before qualification/admission')
    return factory.qualification


def configured_service(factory,policy):
    current=template(factory)
    return QualificationService(store=factory.store,registry=factory.registry,grader=current.grader,
        builder=current.builder,revision=current.revision,policy=policy,
        attestation_verifier=current.attestation_verifier)


def construction_history(factory,task_ref):
    task=typed(factory.store,task_ref,c.TaskBundle)
    pair=typed(factory.store,task.source_pair,c.SourcePair)
    found=[]
    for job_id in factory.registry.trace(pair.candidate).jobs:
        job=factory.registry.job(job_id)
        if (job.spec.operation!='construct' or job.spec.invocation!='m6-construct'
                or job.state!='completed' or job.result is None
                or job.result.disposition!=c.Disposition.SUCCESS or job.result.artifacts[:1]!=(task_ref,)):
            continue
        if len(job.result.artifacts)!=3 or len(job.spec.inputs)!=3:
            raise AdmissionRejected('Factory construction selected result has invalid shape')
        receipt=read_record(factory.store,job.result.artifacts[2],ConstructionResult,'m6-construction-result')
        request=read_construction_request(factory.store,receipt.request)
        if (receipt.claim.job_id!=job_id or receipt.revision!=job.spec.implementation
                or receipt.request!=job.spec.inputs[2] or request.candidate!=pair.candidate
                or job.spec.inputs[:2]!=(request.candidate,request.source)
                or request.inputs is None or request.inputs.source_pair!=task.source_pair
                or receipt.history!=job.result.artifacts[1] or receipt.build_result is None
                or receipt.build_result.artifacts!=(task_ref,)
                or not any(a.claim==receipt.claim and a.state=='completed' for a in factory.registry.attempts(job_id))):
            raise AdmissionRejected('Factory repair history lacks exact selected construction lineage')
        child=factory.registry.job(receipt.build_job)
        if child.state!='completed' or child.spec!=request.builder_job or child.result!=receipt.build_result:
            raise AdmissionRejected('Factory history changed the selected builder outcome')
        found.append((receipt.history,job_id,job.spec.implementation))
    if len(found)>1:raise AdmissionRejected('ambiguous Factory construction history selection')
    return found[0] if found else (None,None,None)


def qualify(factory,task_ref,policy):
    task_ref=checked(c.ArtifactRef,task_ref)
    task=typed(factory.store,task_ref,c.TaskBundle)
    if task.state!=c.TaskState.BUILT or task.qualification is not None:
        raise AdmissionRejected('qualification requires the complete original BUILT root')
    selected=construction_history(factory,task_ref)
    policy=template(factory).policy if policy is None else checked(QualificationPolicy,policy)
    names=('repair_history','repair_history_job','factory_revision')
    provided=tuple(getattr(policy,name) for name in names)
    if any(value is not None for value in provided) and provided!=selected:
        raise AdmissionRejected('supplied repair history differs from the actual selected Factory construction')
    bound=policy.model_copy(update=dict(zip(names,selected)))
    return configured_service(factory,bound).qualify(task_ref)


def for_request(factory,request_ref):
    request=read_record(factory.store,request_ref,ReviewRequest,'m5-review-request')
    policy=read_record(factory.store,request.policy,QualificationPolicy,'m5-qualification-policy')
    service=configured_service(factory,policy)
    if service.policy_ref!=request.policy:raise AdmissionRejected('review request policy identity changed')
    return service


def for_report(factory,report_ref):
    report=typed(factory.store,report_ref,c.QualificationReport)
    current=template(factory)
    if report.provenance.producer_version!=current.revision:
        raise AdmissionRejected('qualification report uses an unsupported M5 service revision')
    inputs=report.provenance.inputs
    if report.provenance.producer=='feature_rl.qualification.admission' and len(inputs)==4:
        return for_request(factory,inputs[1])
    if report.provenance.producer=='feature_rl.qualification' and len(inputs)==3:
        # An actual provisional report still reaches actual M5 denial. No state
        # field or caller assertion is promoted to accepted qualification.
        policy=read_record(factory.store,inputs[1],QualificationPolicy,'m5-qualification-policy')
        return configured_service(factory,policy)
    raise AdmissionRejected('qualification report lacks actual M5 origin')


def accept(factory,request_ref,attestation_ref):
    return for_request(factory,checked(c.ArtifactRef,request_ref)).accept(request_ref,checked(c.ArtifactRef,attestation_ref))


def release(factory,task_ref,report_ref):
    task_ref=checked(c.ArtifactRef,task_ref)
    task=typed(factory.store,task_ref,c.TaskBundle)
    report=task.qualification if report_ref is None else checked(c.ArtifactRef,report_ref)
    if report is None:raise AdmissionRejected('release requires an actual accepted qualification report')
    q=for_report(factory,report)
    lifecycle=TaskLifecycle(store=factory.store,registry=factory.registry,qualification=q,revision=factory.revision)
    if task.state==c.TaskState.BUILT:
        selected=lifecycle.qualify(task_ref,report)
        if selected.disposition!=c.Disposition.SUCCESS:return selected
        return lifecycle.release(selected.artifacts[0])
    if task.state!=c.TaskState.QUALIFIED or task.qualification!=report:
        raise AdmissionRejected('release requires BUILT plus accepted Q or its exact QUALIFIED predecessor')
    return lifecycle.release(task_ref)


def recover(factory,claim):
    """Reconstruct only the exact actual M5/lifecycle policy selected by this job."""
    from .resolver import QualificationConfiguration,LifecyclePolicy
    job=factory.registry.job(claim.job_id)
    if job.spec.invocation=='m5-qualify':
        config=read_record(factory.store,job.spec.configuration,QualificationConfiguration,'m5-qualification-configuration')
        q=configured_service(factory,read_record(factory.store,config.policy,QualificationPolicy,'m5-qualification-policy'))
        if q.configuration!=job.spec.configuration or q.revision!=job.spec.implementation:
            raise AdmissionRejected('recovery M5 service configuration or implementation changed')
        return q.recover(claim)
    config=read_record(factory.store,job.spec.configuration,LifecyclePolicy,'m6-lifecycle-policy')
    q=configured_service(factory,read_record(factory.store,config.qualification_policy,QualificationPolicy,'m5-qualification-policy'))
    lifecycle=TaskLifecycle(store=factory.store,registry=factory.registry,qualification=q,revision=factory.revision)
    if lifecycle.configuration!=job.spec.configuration:raise AdmissionRejected('recovery lifecycle service configuration changed')
    return lifecycle.recover(claim)
