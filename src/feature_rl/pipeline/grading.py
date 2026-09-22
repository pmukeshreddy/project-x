"""Selected ordinary grade jobs around the actual M4 service; no task admission."""
import json
import uuid
from typing import Literal
from feature_rl import contracts as c
from feature_rl.artifacts import canonical_json
from feature_rl.grading import GradingService,GradePublicationFailed,GradeReceipt,read_grade
from feature_rl.environments import EvidencePublicationFailed
from feature_rl.qualification.evidence import unknown_cost,collapse_costs
from feature_rl.registry import JobSpec,Claim,CostObservation
from .construction import put,references
from .packaging import checked,document,read_record,read_bytes,typed,MAX_DOCUMENT
from .factory import FactoryRecoveryRequired,FactoryPublicationFailed,FactoryUpstreamPending


class FrozenGrade(c.StrictModel):
    version: Literal['m6-frozen-grade-v1']='m6-frozen-grade-v1'
    claim: Claim
    request: c.ArtifactRef
    original: c.ArtifactRef
    costs: tuple[c.CostRecord,...]


class PendingGradeResult(c.StrictModel):
    version: Literal['m6-pending-grade-result-v1']='m6-pending-grade-result-v1'
    claim: Claim
    request: c.ArtifactRef
    result: c.OperationResult


def service(factory):
    if type(factory.grading) is not GradingService:raise ValueError('configure the actual same-store M4 GradingService')
    return factory.grading


def configuration(factory,request):
    actual=service(factory)
    task=typed(factory.store,request.task_version,c.TaskBundle)
    verifier=typed(factory.store,task.private_oracle,c.VerifierBundle)
    inner=[]
    for case in verifier.cases:
        for ref in (case.inputs,case.expected):
            try:value=json.loads(read_bytes(factory.store,ref,MAX_DOCUMENT))
            except (ValueError,UnicodeDecodeError):continue  # actual M4 records malformed input
            inner.extend(references(value))
    if request.submission.kind=='m4-submission':
        from feature_rl.submission import Submission
        try:submission=read_record(factory.store,request.submission,Submission,'m4-submission')
        except ValueError:submission=None  # actual M4 gives malformed source its ordinary zero
        if submission is not None:inner.extend((submission.baseline,submission.changes))
    # New consumer config declares inner opaque refs explicitly. Existing
    # immutable submission/comparison declarations are never retrofitted.
    value={'version':'m6-grade-policy-v1','revision':factory.revision,'m4_revision':actual.revision,
        'm3_revision':actual.runtime.revision,'policy':document(actual.runtime.base_policy),
        'max_wall_seconds':actual.max_wall_seconds,'opaque_inputs':[document(ref) for ref in dict.fromkeys(inner)]}
    return put(factory,value,'m6-grade-policy',dependencies=tuple(dict.fromkeys(inner)))


def spec(factory,request,ref,invocation):
    return JobSpec(operation='grade',inputs=(request.task_version,request.submission,ref),
        configuration=configuration(factory,request),implementation=factory.revision,invocation=invocation,attempt_limit=1)


def snapshot(factory,claim):
    values=[entry for entry in factory.registry.accounting(claim.job_id).observations if entry.attempt_id==claim.attempt_id]
    if len(values)!=1 or values[0].observation.source!='m6-grade':
        raise FactoryRecoveryRequired('grade accounting intent is missing or ambiguous',claim)
    return values[0]


def observe(factory,claim,refs,costs):
    values=[entry for entry in factory.registry.accounting(claim.job_id).observations if entry.attempt_id==claim.attempt_id]
    old=None if not values else snapshot(factory,claim).observation
    return factory.registry.reconcile(claim,CostObservation(source='m6-grade',upstream_attempt_id=claim.attempt_id,
        revision=1 if old is None else old.revision+1,receipts=tuple(dict.fromkeys((*(old.receipts if old else ()),*refs))),costs=costs))


def actual_costs(costs):
    categories={cost.category for cost in costs}
    return collapse_costs((*costs,*(unknown_cost(category,'M4 did not return a separate measurement for this channel')
        for category in ('construction','execution','verifier') if category not in categories),
        unknown_cost('storage','Factory/Registry publication overhead and monetary allocation remain unknown')))


def grade(factory,task,submission,case_seed,invocation):
    request=c.GradeRequest(task_version=checked(c.ArtifactRef,task),submission=checked(c.ArtifactRef,submission),case_seed=case_seed)
    if case_seed>=2**63:raise ValueError('case seed exceeds M4 domain')
    ref=put(factory,request,'m6-grade-request',dependencies=references(document(request)))
    job=factory.registry.enqueue(spec(factory,request,ref,invocation))
    if job.state=='completed':return job.result
    if job.state!='queued':raise FactoryRecoveryRequired('grade already dispatched; recover without worker reexecution',factory.registry.attempts(job.job_id)[-1].claim)
    claim=factory.registry.claim(job.job_id,owner='feature_rl.pipeline.Factory.grade',claim_key=uuid.uuid4().hex)
    try:observe(factory,claim,(ref,),tuple(unknown_cost(category,'Actual M4 call not yet reconciled')
        for category in ('construction','execution','storage','verifier')))
    except Exception as exc:raise FactoryRecoveryRequired('grade intent unconfirmed; no M4 dispatch',claim) from exc
    try:result=service(factory).grade(task,submission,case_seed)
    except GradePublicationFailed as pending:
        retain_pending(factory,claim,ref,pending)
        raise FactoryUpstreamPending('actual M4 publication retained; no grade reexecution',claim,pending) from pending
    except Exception as exc:raise FactoryRecoveryRequired('M4 outcome unknown; no automatic grade redispatch',claim) from exc
    return record(factory,claim,ref,result)


def validated(factory,claim):
    claim=checked(Claim,claim);job=factory.registry.job(claim.job_id)
    if len(job.spec.inputs)!=3 or not any(item.claim==claim for item in factory.registry.attempts(job.job_id)):
        raise ValueError('unknown Factory grade claim')
    ref=job.spec.inputs[2];request=read_record(factory.store,ref,c.GradeRequest,'m6-grade-request')
    if job.spec!=spec(factory,request,ref,job.spec.invocation):raise ValueError('grade configuration/request changed')
    return job,ref,request


def verify_result(factory,request,result):
    if result.operation!='grade' or len(result.artifacts)!=1 or len(result.evidence)!=1:raise ValueError('one actual M4 grade outcome required')
    receipt=read_grade(factory.store,result.artifacts[0]);evidence=result.evidence[0]
    if ((receipt.task,receipt.submission,receipt.case_seed)!=(request.task_version,request.submission,request.case_seed)
            or receipt.implementation_revision!=service(factory).revision or result.disposition!=receipt.disposition
            or result.reason!=receipt.reason or evidence.producer!='feature_rl.grading'
            or evidence.command!=('GradingService.grade',receipt.task.sha256,receipt.submission.sha256,str(receipt.case_seed))
            or evidence.recorded_at!=receipt.recorded_at or evidence.revision!=service(factory).revision
            or evidence.artifacts!=(result.artifacts[0],*receipt.runtime_evidence)):
        raise ValueError('grade result lost exact actual request/receipt/evidence equality')
    inner=list(references(document(receipt)))
    for runtime in receipt.runtime_evidence:
        if runtime.kind=='environment-execution':
            value=json.loads(read_bytes(factory.store,runtime,32*1024*1024))
            inner.extend(references(value))
    return tuple(dict.fromkeys((*references(document(result)),*inner)))


def record(factory,claim,ref,result):
    job,selected,request=validated(factory,claim)
    if ref!=selected:raise ValueError('actual grade result changed selected request')
    if job.state=='completed':return job.result
    deps=verify_result(factory,request,result);costs=actual_costs(result.costs)
    current=snapshot(factory,claim).observation
    if current.revision>1 and current.costs!=costs:raise ValueError('actual grade result changed selected accounting')
    retained=canonical_json(document(PendingGradeResult(claim=claim,request=ref,result=result)))
    if len(retained)>MAX_DOCUMENT:raise ValueError('actual M4 result exceeds Factory publication bound')
    try:
        observe(factory,claim,(ref,),costs)
        original=put(factory,result,'m6-grade-original',dependencies=deps)
        observe(factory,claim,(original,),costs)
    except Exception as exc:
        raise FactoryPublicationFailed('retain actual M4 result for publication-only retry',claim,retained,
            kind='m6-pending-grade-result') from exc
    frozen=FrozenGrade(claim=claim,request=ref,original=original,costs=costs)
    return publish(factory,canonical_json(document(frozen)),claim)


def retry_result(factory,payload,claim):
    if type(payload) is not bytes or len(payload)>MAX_DOCUMENT:raise ValueError('pending grade result byte cap')
    pending=PendingGradeResult.model_validate_json(payload)
    if pending.claim!=claim or canonical_json(document(pending))!=payload:raise ValueError('pending actual grade claim/payload changed')
    return record(factory,claim,pending.request,pending.result)


def retain_pending(factory,claim,ref,pending):
    costs=actual_costs(pending.costs)
    try:
        observe(factory,claim,(ref,),costs)
        publications=[]
        for item in pending.runtime_publications:
            payload=factory.store.put_bytes(item.payload,'m6-pending-runtime',c.Visibility.PRIVATE)
            publications.append({'payload':document(payload),'kind':item.kind,'visibility':item.visibility.value})
        value={'version':'m6-pending-grade-v1','claim':document(claim),'request':document(ref),
            'receipt':document(pending.receipt),'costs':[document(cost) for cost in pending.costs],
            'scope':pending.scope,'runtime_publications':publications}
        selected=put(factory,value,'m6-pending-grade',dependencies=references(value))
        observe(factory,claim,(selected,),costs)
    except Exception as exc:raise FactoryUpstreamPending('retain exact M4 pending publication; durable linkage is unconfirmed',claim,pending) from exc


def recover_pending(factory,claim,ref):
    raw=read_bytes(factory.store,ref,MAX_DOCUMENT,kind='m6-pending-grade');value=json.loads(raw)
    if (set(value)!={'version','claim','request','receipt','costs','scope','runtime_publications'}
            or value['version']!='m6-pending-grade-v1' or value['claim']!=document(claim)):
        raise ValueError('invalid retained M4 publication')
    publications=[]
    for item in value['runtime_publications']:
        payload=c.ArtifactRef.model_validate_json(canonical_json(item['payload']))
        publications.append(EvidencePublicationFailed('Retained actual M3 publication',
            payload=read_bytes(factory.store,payload,32*1024*1024,kind='m6-pending-runtime'),
            kind=item['kind'],visibility=c.Visibility(item['visibility'])))
    pending=GradePublicationFailed(GradeReceipt.model_validate_json(canonical_json(value['receipt'])),
        tuple(c.CostRecord.model_validate_json(canonical_json(cost)) for cost in value['costs']),value['scope'],tuple(publications))
    if actual_costs(pending.costs)!=snapshot(factory,claim).observation.costs:raise ValueError('pending M4 costs changed selected accounting')
    try:result=service(factory).retry_publication(pending)
    except GradePublicationFailed as newer:
        retain_pending(factory,claim,c.ArtifactRef.model_validate_json(canonical_json(value['request'])),newer)
        raise FactoryUpstreamPending('actual M4 publication remains pending',claim,newer) from newer
    return record(factory,claim,c.ArtifactRef.model_validate_json(canonical_json(value['request'])),result)


def publish(factory,payload,claim):
    if type(payload) is not bytes or len(payload)>MAX_DOCUMENT:raise ValueError('grade receipt byte cap')
    frozen=FrozenGrade.model_validate_json(payload);job,ref,request=validated(factory,claim)
    if canonical_json(document(frozen))!=payload or frozen.claim!=claim or frozen.request!=ref:raise ValueError('frozen grade changed claim/request')
    if job.state=='completed':return job.result
    current=snapshot(factory,claim).observation
    if frozen.costs!=current.costs or frozen.original not in current.receipts:raise ValueError('frozen grade changed selected accounting/original result')
    original=read_record(factory.store,frozen.original,c.OperationResult,'m6-grade-original')
    verify_result(factory,request,original)
    try:
        selected=put(factory,frozen,'m6-frozen-grade',dependencies=references(document(frozen)))
        observed=observe(factory,claim,(selected,),frozen.costs)
    except Exception as exc:raise FactoryPublicationFailed('retain exact frozen Factory grade',claim,payload,kind='m6-frozen-grade') from exc
    evidence=c.EvidenceRecord(producer='feature_rl.pipeline.Factory.grade',command=('Factory.grade',ref.sha256),
        recorded_at=original.evidence[0].recorded_at,exit_status=0 if original.disposition==c.Disposition.SUCCESS else 1,
        artifacts=(selected,),revision=factory.revision,scope='source_inspection')
    result=original.model_copy(update={'artifacts':(*original.artifacts,selected),'evidence':(*original.evidence,evidence),'costs':frozen.costs})
    try:return factory.registry.complete(claim,result,observations=(observed.observation_id,)).result
    except Exception as exc:raise FactoryRecoveryRequired('Factory grade completion unconfirmed; recover frozen result',claim) from exc


def recover(factory,claim):
    job,ref,_=validated(factory,claim)
    if job.state=='completed':return job.result
    current=snapshot(factory,claim).observation
    frozen=[item for item in current.receipts if item.kind=='m6-frozen-grade']
    if frozen:return publish(factory,read_bytes(factory.store,frozen[-1],MAX_DOCUMENT),claim)
    originals=[item for item in current.receipts if item.kind=='m6-grade-original']
    if originals:return publish(factory,canonical_json(document(FrozenGrade(claim=claim,request=ref,original=originals[-1],costs=current.costs))),claim)
    pending=[item for item in current.receipts if item.kind=='m6-pending-grade']
    if pending:return recover_pending(factory,claim,pending[-1])
    raise FactoryRecoveryRequired('grade has no selected M4 outcome; incurred work remains unknown and will not be repeated',claim)
