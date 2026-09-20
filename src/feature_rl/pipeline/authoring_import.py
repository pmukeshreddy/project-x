"""Import actual retained rejected M2 journals; never call a provider or finalizer."""
from datetime import datetime,timezone
import json
import uuid
from feature_rl import contracts as c
from feature_rl.artifacts import canonical_json
from feature_rl.generation import GenerationRequest,GenerationCallRecord
from feature_rl.requirements import ContractFinalizationInputs,RequirementContractProposal,GenerationCandidate
from feature_rl.scenarios import ScenarioFinalizationInputs,ScenarioPlanProposal
from feature_rl.qualification.evidence import unknown_cost
from .authoring_models import AuthoringCall,AuthoringRequest,AuthoringReceipt
from .packaging import checked,read_bytes,document,typed
from .construction import put,references,identity
from .locking import candidate_lock
from .factory import FactoryRecoveryRequired
from . import authoring as a


def journal(store,ref,index,stage):
    kind='contract-authoring-journal' if stage=='initial_authoring' else 'scenario-authoring-journal'
    raw=read_bytes(store,ref,65536,kind=kind)
    value=json.loads(raw)
    if (canonical_json(value)!=raw or value.get('status')!='rejected'
            or value.get('stage')!=stage or value.get('attempt_index')!=index):
        raise ValueError('retained journals must be the complete sequential rejected M2 chain')
    record=GenerationCallRecord.model_validate_json(canonical_json(value['generation_record']))
    if 'request' not in record.archives:
        raise ValueError('this importer needs the exact retained request archive; incomplete variants remain unresolved')
    request=GenerationRequest.model_validate_json(read_bytes(store,record.archives['request'],1024*1024,kind='generation-request'))
    if (identity(request)!=value['request_sha256'] or ('semantic_request_sha256' in value
            and a.semantic_request_sha256(request)!=value['semantic_request_sha256'])):
        raise ValueError('journal request/semantic digest differs from its retained bytes')
    cost=c.CostRecord.model_validate_json(canonical_json(value['cost']))
    schema=RequirementContractProposal if stage=='initial_authoring' else ScenarioPlanProposal
    outcome=a.provider_outcome(store,request,record,schema,cost)
    if outcome.cost!=cost:raise ValueError('retained journal changed its actual provider cost')
    return value,request,outcome


def attributed_jobs(factory,attempt_ref):
    """Use the bounded authoritative event ledger; no second provider-attempt index."""
    seen=set();after=0
    while True:
        batch=factory.registry.events(after=after,limit=1000)
        if not batch:break
        for event in batch:
            if event.action=='reconcile' and event.data['observation']['source']=='m6-generation':
                if document(attempt_ref) in event.data['observation']['receipts']:
                    seen.add(event.data['claim']['job_id'])
        after=batch[-1].sequence
    return seen


def register_journal(factory,ref,value,request,outcome):
    for name,artifact in outcome.record.archives.items():
        if name=='request':deps=tuple(dict.fromkeys(context.source for context in request.contexts))
        elif name=='status':deps=tuple(r for key,r in outcome.record.archives.items() if key!='status')
        else:deps=()
        factory.registry.register(artifact,dependencies=deps)
    factory.registry.register(ref,dependencies=references(value))


def import_rejected(factory,candidate,call,journal_refs):
    candidate=checked(c.ArtifactRef,candidate);call=checked(AuthoringCall,call)
    if not isinstance(call.inputs,(ContractFinalizationInputs,ScenarioFinalizationInputs)):
        raise ValueError('retained importer currently supports actual M2 contract/scenario rejection chains')
    if type(journal_refs) is not tuple or not 1<=len(journal_refs)<=3:
        raise ValueError('one initial and at most two retained repair journals required')
    values=[journal(factory.store,ref,index,call.generation.request.stage.value)
        for index,ref in enumerate(journal_refs,1)]
    if values[-1][1]!=call.generation.request:
        raise ValueError('import endpoint must match the exact final retained request')
    with candidate_lock(factory.store,a.batch_reference(factory,candidate)), candidate_lock(factory.store,candidate):
        source=factory._screen_source(candidate)
        # A present source gate cannot erase costs incurred in the past. This
        # path reads retained bytes only; it does not grant authoring admission.
        # Aggregate source records are already charged by source admission. A
        # provider import with potentially overlapping authoring costs needs an
        # explicit original-source allocation instead of silently double charging.
        value=typed(factory.store,candidate,c.CandidateRecord)
        if any(cost.category=='authoring' for cost in value.costs):
            raise ValueError('source aggregate includes authoring costs; reconcile that original allocation before importing individual calls')
        a.validate_call(factory,candidate,call)
        front=a.frontier(factory,candidate,source.artifacts[0],call,retained=True)
        selected=None;previous=None
        for index,(ref,(entry,request,outcome)) in enumerate(zip(journal_refs,values),1):
            generated=GenerationCandidate(request=request,diagnosis=entry['diagnosis'],changed_input=entry['changed_input'])
            current=call.model_copy(update={'generation':generated})
            a.validate_call(factory,candidate,current)
            stage,lane=a.stage_lane(current)
            old_jobs=a.jobs(factory,candidate)
            matches=[]
            for job,old in old_jobs:
                if job.state=='completed' and job.result is not None:
                    receipt=a.read_authoring_receipt(factory.store,job.result.artifacts[-1])
                    if receipt.journal_refs==journal_refs[:index]:matches.append((job,old))
            if matches:
                if len(matches)!=1 or matches[0][1].call.generation.request!=request:
                    raise ValueError('retained journal already has ambiguous/mismatched attribution')
                selected=matches[0][0].result;previous=matches[0][0].spec.inputs[1];continue
            if index>1 and previous is None:raise ValueError('retained repair lacks its selected predecessor')
            if any(old.lane==lane and job.spec.inputs[1]!=previous for job,old in old_jobs
                    if job.state!='completed' or not old.imported_journals or old.imported_journals!=journal_refs[:len(old.imported_journals)]):
                raise ValueError('retained import conflicts with the selected authoring lane; reconcile existing history first')
            actual=AuthoringRequest(candidate=candidate,frontier=front,call=current,previous=previous,
                repair=index>1,stage=stage,lane=lane,origin='retained_journal',imported_journals=journal_refs[:index])
            # Opaque dependencies are declared before a consumer registers the
            # request; immutable old declarations are never retrofitted.
            register_journal(factory,ref,entry,request,outcome)
            request_ref=put(factory,actual,'m6-authoring-request',dependencies=references(document(actual)))
            specification=a.spec(factory,request_ref,actual)
            assigned=attributed_jobs(factory,outcome.record.archives['attempt'])
            if assigned-{identity(specification)}:
                raise ValueError('this cost-bearing provider attempt is already attributed to another Registry job')
            job=factory.registry.enqueue(specification)
            if job.state=='completed':selected=job.result;previous=request_ref;continue
            if job.attempts:raise FactoryRecoveryRequired('recover the original retained import; no new provider attempt',factory.registry.attempts(job.job_id)[-1].claim)
            claim=factory.registry.claim(job.job_id,owner='feature_rl.pipeline.Factory',claim_key=uuid.uuid4().hex)
            try:
                a.observe(factory,claim,'m6-generation',(request_ref,),(
                    unknown_cost('authoring','Retained provider attempt costs not yet reconciled into this import'),
                    unknown_cost('construction','Inert historical import/controller overhead unmeasured'),unknown_cost('storage')))
            except Exception as exc:raise FactoryRecoveryRequired('retained import intent unconfirmed',claim) from exc
            selected=finish_import(factory,claim,job,actual);previous=request_ref
        return selected


def finish_import(factory,claim,job,request):
    index=len(request.imported_journals)
    entry,generated,outcome=journal(factory.store,request.imported_journals[-1],index,request.call.generation.request.stage.value)
    if generated!=request.call.generation.request:raise ValueError('retained import request changed')
    assigned=attributed_jobs(factory,outcome.record.archives['attempt'])
    if assigned-{job.job_id}:raise ValueError('provider attempt already has another cost attribution')
    current=a.observation(factory,claim,'m6-generation')
    costs=tuple(cost for cost in current.costs if cost.category!=outcome.cost.category)+(outcome.cost,)
    a.observe(factory,claim,'m6-generation',(job.spec.inputs[1],*outcome.record.archives.values(),*request.imported_journals),costs)
    frozen=AuthoringReceipt(claim=claim,request=job.spec.inputs[1],outputs=(),journal_refs=request.imported_journals,
        disposition=c.Disposition.REJECTED,reason='Retained original M2 rejection imported without provider/finalizer execution: '+str(entry['error'])[:3000],
        repair=request.repair,stage=request.stage,lane=request.lane,costs=a.observation(factory,claim,'m6-generation').costs,
        revision=factory.revision,recorded_at=datetime.now(timezone.utc))
    return a.publish(factory,canonical_json(document(frozen)),claim)
