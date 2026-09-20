"""Factory selection around the actual builder; no generated prerequisite substitutes."""
from datetime import datetime, timezone
import hashlib
import time
import uuid
from typing import Literal

from feature_rl import contracts as c
from feature_rl.artifacts import ArtifactError, canonical_json
from feature_rl.qualification import RepairAttempt, RepairHistory
from feature_rl.qualification.evidence import unknown_cost
from feature_rl.registry import JobSpec, Claim, CostObservation, RegistryError, UnknownIdentity
from .models import BuildInputs, BuildPublicationFailed, BuildRecoveryRequired
from .packaging import MAX_DOCUMENT, checked, document, typed, read_record, read_bytes
from .locking import candidate_lock
from .factory import FactoryPublicationFailed, FactoryRecoveryRequired, FactoryUpstreamPending, read_source_disposition


class ConstructionRequest(c.StrictModel):
    version: Literal['m6-construction-request-v1']='m6-construction-request-v1'
    candidate: c.ArtifactRef
    source: c.ArtifactRef
    inputs: BuildInputs | None
    builder_job: JobSpec | None


class ConstructionRequestV2(ConstructionRequest):
    version: Literal['m6-construction-request-v2']='m6-construction-request-v2'
    history: c.ArtifactRef


def read_construction_request(store,ref):
    import json
    raw=read_bytes(store,ref,MAX_DOCUMENT,kind='m6-construction-request')
    cls=ConstructionRequestV2 if json.loads(raw).get('version')=='m6-construction-request-v2' else ConstructionRequest
    return read_record(store,ref,cls,'m6-construction-request')


class ConstructionResult(c.StrictModel):
    version: Literal['m6-construction-result-v1']='m6-construction-result-v1'
    claim: Claim
    request: c.ArtifactRef
    build_job: c.Digest | None
    build_result: c.OperationResult | None
    history: c.ArtifactRef | None
    disposition: c.Disposition
    reason: str
    costs: tuple[c.CostRecord,...]
    revision: c.Revision
    recorded_at: c.UTCDateTime


def references(value):
    """Declare all actual refs in a validated consumer record, including opaque leaves."""
    result=[]
    def walk(node):
        if isinstance(node,dict):
            if set(node)==set(c.ArtifactRef.model_fields):
                result.append(c.ArtifactRef.model_validate_json(canonical_json(node)))
            else:
                for child in node.values():walk(child)
        elif isinstance(node,(tuple,list)):
            for child in node:walk(child)
    walk(value)
    return tuple(dict.fromkeys(result))


def put(factory,value,kind,*,dependencies=()):
    raw=canonical_json(document(value) if hasattr(value,'model_dump') else value)
    if len(raw)>MAX_DOCUMENT:raise ValueError('Factory record exceeds consumer byte cap')
    ref=factory.store.put_bytes(raw,kind,c.Visibility.PRIVATE)
    factory.registry.register(ref,dependencies=tuple(dict.fromkeys(dependencies)))
    return ref


def configuration(factory):
    return put(factory,{'version':'m6-construction-policy-v1','revision':factory.revision,
        'builder_revision':factory.builder.revision,'source_policy':document(factory.source_configuration),
        'repair_budget':{'per_stage':2,'candidate':4}},'m6-construction-policy',dependencies=(factory.source_configuration,))


def spec(factory,request_ref,request):
    return JobSpec(operation='construct',inputs=(request.candidate,request.source,request_ref),
        configuration=configuration(factory),implementation=factory.revision,invocation='m6-construct',attempt_limit=3)


def identity(value):return hashlib.sha256(canonical_json(document(value))).hexdigest()


def costs(elapsed=None):
    return (c.CostRecord(category='construction',wall_seconds=elapsed,cpu_seconds=None,gpu_seconds=None,
        input_tokens=None,output_tokens=None,human_minutes=None,usd=None,
        measurement='unknown' if elapsed is None else 'partial',
        note='Factory controller wall excludes the source job and builder call; recovery overhead unknown'),
        unknown_cost('storage','Factory record/Registry publication overhead unmeasured; upstream costs remain in their original jobs'))


def observe(factory,claim,request_ref,values,revision,reference=None):
    return factory.registry.reconcile(claim,CostObservation(source='m6-construction',upstream_attempt_id=claim.attempt_id,
        revision=revision,receipts=(request_ref,) if reference is None else (request_ref,reference),costs=values))


def construct(factory,candidate,inputs):
    candidate=checked(c.ArtifactRef,candidate)
    with candidate_lock(factory.store,candidate):
        source_result=factory._screen_source(candidate)
        if source_result.disposition!=c.Disposition.SUCCESS:return source_result
        if inputs is not None:inputs=checked(BuildInputs,inputs)
        child=None if inputs is None else factory.builder.job_spec(inputs)
        request=ConstructionRequest(candidate=candidate,source=source_result.artifacts[0],inputs=inputs,builder_job=child)
        if inputs is not None:
            from .authoring_history import selected_history
            history=selected_history(factory,request)
            if history is not None:
                request=ConstructionRequestV2(**request.model_dump(exclude={'version'}),history=history)
        request_ref=put(factory,request,'m6-construction-request',dependencies=references(document(request)))
        job=factory.registry.enqueue(spec(factory,request_ref,request))
        if job.state=='completed':return job.result
        if job.state!='queued':
            raise FactoryRecoveryRequired('construction exists; recover its selected builder attempt',factory.registry.attempts(job.job_id)[-1].claim)
        claim=factory.registry.claim(job.job_id,owner='feature_rl.pipeline.Factory',claim_key=uuid.uuid4().hex)
        try:observe(factory,claim,request_ref,costs(),1)
        except (ArtifactError,RegistryError,OSError) as exc:
            raise FactoryRecoveryRequired('construction intent unconfirmed; recover before builder dispatch',claim) from exc
        return execute(factory,claim,request_ref,request,recovery=False)


def validated_request(factory,claim):
    job=factory.registry.job(claim.job_id)
    if not any(a.claim==claim for a in factory.registry.attempts(job.job_id)) or len(job.spec.inputs)!=3:
        raise ValueError('construction claim is unknown')
    ref=job.spec.inputs[2]
    request=read_construction_request(factory.store,ref)
    if job.spec!=spec(factory,ref,request):raise ValueError('construction belongs to another Factory configuration')
    source=read_source_disposition(factory.store,request.source)
    source_job=factory.registry.job(source.claim.job_id)
    if (source.candidate!=request.candidate or source.source_status!='eligible'
            or source_job.state!='completed' or source_job.result is None
            or source_job.result.disposition!=c.Disposition.SUCCESS
            or source_job.result.artifacts!=(request.source,)):
        raise ValueError('construction lacks exact selected eligible source prerequisite')
    if (request.inputs is None)!=(request.builder_job is None):raise ValueError('construction builder identity missing')
    if request.inputs is not None and factory.builder.job_spec(request.inputs)!=request.builder_job:
        raise ValueError('construction changed its exact builder inputs/configuration')
    return job,ref,request


def incomplete_history(factory,request):
    """Retain evidenced neutral repairs; absent authoring closure never means zero."""
    value=typed(factory.store,request.candidate,c.CandidateRecord)
    recipe=typed(factory.store,request.inputs.environment.recipe,c.EnvironmentRecipe)
    from feature_rl.environments import SandboxPolicy
    from feature_rl.environments.profiles import validate_recipe_profile
    policy=read_record(factory.store,request.inputs.environment.policy,SandboxPolicy,'sandbox-policy')
    validate_recipe_profile(recipe,policy,factory.store)
    attempts=[];journals=[request.source]
    for repair in recipe.neutral_repairs:
        before=put(factory,{'version':'m6-environment-before-v1','base_manifest':policy.image,
            'repair':document(repair.patch)},'m6-environment-before',dependencies=(repair.patch,))
        attempts.append(RepairAttempt(stage='environment',before=before,after=repair.patch,
            diagnosis='Neutral environment repair declared by the frozen runtime profile',change=repair.description,
            evidence=repair.neutrality_evidence,costs=(unknown_cost('construction',
                'Per-candidate allocation of the retained neutral image repair is unknown; original recipe evidence/costs remain resolvable'),)))
        journals.append(repair.patch)
    history=RepairHistory(candidate=request.candidate,complete=False,initial_evidence=value.provenance.evidence,
        attempts=tuple(attempts),journal_refs=tuple(journals))
    return put(factory,history,'m5-repair-history',dependencies=references(document(history)))


def execute(factory,claim,request_ref,request,*,recovery):
    factory.registry.assert_usable(request_ref)
    started=time.monotonic();child_elapsed=0.0;result=None;history=None
    if request.inputs is None:
        disposition=c.Disposition.BLOCKED;reason='Complete authored BuildInputs are missing; no generated prerequisites or BUILT root were manufactured'
    elif typed(factory.store,request.inputs.source_pair,c.SourcePair).candidate!=request.candidate:
        disposition=c.Disposition.INVALID;reason='SourcePair.candidate differs from the exact construction candidate'
    else:
        child_id=identity(request.builder_job)
        before=time.monotonic()
        try:
            try:child=factory.registry.job(child_id)
            except UnknownIdentity:child=None
            if child is not None and child.state=='completed':result=child.result
            elif child is not None and child.state=='running':
                result=factory.builder.recover(factory.registry.attempts(child_id)[-1].claim)
            elif child is None or (child.state=='queued' and not factory.registry.attempts(child_id)):
                result=factory.builder.build(request.inputs,owner='feature_rl.pipeline.Factory',claim_key=claim.attempt_id+'.build')
            else:raise FactoryRecoveryRequired('builder attempt requires explicit accounting/recovery',claim)
        except BuildPublicationFailed as exc:
            raise FactoryUpstreamPending('retain original builder publication; do not assemble again',claim,exc) from exc
        except BuildRecoveryRequired as exc:
            raise FactoryRecoveryRequired('builder attempt remains unresolved: '+str(exc),claim) from exc
        finally:child_elapsed=time.monotonic()-before
        disposition=result.disposition;reason=result.reason
        if disposition==c.Disposition.SUCCESS:
            factory.builder.solver_package(result.artifacts[0])
            history=request.history if isinstance(request,ConstructionRequestV2) else incomplete_history(factory,request)
    receipt=ConstructionResult(claim=claim,request=request_ref,
        build_job=None if result is None else identity(request.builder_job),build_result=result,history=history,
        disposition=disposition,reason=reason,costs=costs(None if recovery else max(0.0,time.monotonic()-started-child_elapsed)),
        revision=factory.revision,recorded_at=datetime.now(timezone.utc))
    return publish(factory,canonical_json(document(receipt)),claim)


def validate_result(factory,payload,claim):
    if type(payload) is not bytes or len(payload)>MAX_DOCUMENT:raise ValueError('construction result exceeds reader cap')
    receipt=ConstructionResult.model_validate_json(payload)
    if canonical_json(document(receipt))!=payload or receipt.claim!=claim or receipt.revision!=factory.revision:
        raise ValueError('construction result claim/configuration differs')
    job,request_ref,request=validated_request(factory,claim)
    if receipt.request!=request_ref:raise ValueError('construction result changed its selected request')
    if receipt.build_result is not None:
        child=factory.registry.job(receipt.build_job)
        if (request.builder_job is None or child.spec!=request.builder_job or child.state!='completed'
                or child.result!=receipt.build_result or receipt.disposition!=child.result.disposition
                or receipt.reason!=child.result.reason):raise ValueError('construction result lacks exact selected builder outcome')
    elif receipt.build_job is not None or receipt.disposition==c.Disposition.SUCCESS:
        raise ValueError('construction success lacks selected builder result')
    if receipt.disposition==c.Disposition.SUCCESS:
        history=request.history if isinstance(request,ConstructionRequestV2) else incomplete_history(factory,request)
        if receipt.history!=history:raise ValueError('construction repair history changed')
        factory.builder.solver_package(receipt.build_result.artifacts[0])
    elif receipt.history is not None:raise ValueError('failed construction cannot select successful build history')
    return job,receipt


def publish(factory,payload,claim):
    job,receipt=validate_result(factory,payload,claim)
    if job.state=='completed':return job.result
    try:
        reference=put(factory,receipt,'m6-construction-result',dependencies=references(document(receipt)))
        observe(factory,claim,receipt.request,receipt.costs,2,reference)
    except (ArtifactError,RegistryError,OSError) as exc:
        raise FactoryPublicationFailed('retain exact Factory result without repeating builder work',claim,payload,kind='m6-construction-result') from exc
    evidence=c.EvidenceRecord(producer='feature_rl.pipeline.Factory',command=('Factory.construct',receipt.request.sha256),
        recorded_at=receipt.recorded_at,exit_status=0 if receipt.disposition==c.Disposition.SUCCESS else 1,
        artifacts=(reference,),revision=factory.revision,scope='source_inspection')
    outputs=(reference,) if receipt.disposition!=c.Disposition.SUCCESS else (receipt.build_result.artifacts[0],receipt.history,reference)
    result=c.OperationResult(operation='construct',disposition=receipt.disposition,artifacts=outputs,
        evidence=(evidence,),costs=receipt.costs,reason=receipt.reason)
    snapshots=[x for x in factory.registry.accounting(claim.job_id).observations if x.attempt_id==claim.attempt_id]
    if len(snapshots)!=1 or snapshots[0].observation.costs!=receipt.costs or snapshots[0].observation.revision!=2:
        raise FactoryRecoveryRequired('construction lacks exact frozen accounting',claim)
    try:return factory.registry.complete(claim,result,observations=(snapshots[0].observation_id,)).result
    except (ArtifactError,RegistryError,OSError) as exc:
        raise FactoryRecoveryRequired('Factory completion unconfirmed; recover frozen result',claim) from exc


def recover(factory,claim):
    job,request_ref,request=validated_request(factory,claim)
    if job.state=='completed':return job.result
    with candidate_lock(factory.store,request.candidate):
        snapshots=[x for x in factory.registry.accounting(job.job_id).observations if x.attempt_id==claim.attempt_id]
        if len(snapshots)!=1:raise FactoryRecoveryRequired('construction intent not confirmed',claim)
        observation=snapshots[0].observation
        if observation.revision==2:
            refs=[r for r in observation.receipts if r.kind=='m6-construction-result']
            if len(refs)!=1:raise ValueError('construction result receipt missing')
            return publish(factory,read_bytes(factory.store,refs[0],MAX_DOCUMENT,kind='m6-construction-result'),claim)
        if observation.revision!=1:raise FactoryRecoveryRequired('unknown construction accounting phase',claim)
        return execute(factory,claim,request_ref,request,recovery=True)


def retry_build(factory,pending):
    _,_,request=validated_request(factory,pending.claim)
    if type(pending.upstream) is not BuildPublicationFailed or pending.upstream.claim.job_id!=identity(request.builder_job):
        raise ValueError('pending builder publication belongs to another construction')
    factory.builder.retry_publication(pending.upstream)
    return recover(factory,pending.claim)
