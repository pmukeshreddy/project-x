"""One real M2/M4 call per durable attempt, with bounded resource reservations."""
from datetime import datetime, timezone
import hashlib
import json
import uuid

from feature_rl import contracts as c
from feature_rl.artifacts import canonical_json
from feature_rl.generation import CodexGenerationProvider, GenerationResult, GenerationCallRecord
from feature_rl.generation.provider import GenerationProviderError
from feature_rl.requirements import (AuthoringEvidenceResolver, ContractAuthoringService,
    ContractFinalizationInputs, RequirementContractProposal, AuthoringExhausted,
    AuthoringPublicationPending, AuthoringJournalPublicationPending)
from feature_rl.requirements.service import semantic_request_sha256, validate_recovered_generation
from feature_rl.verifiers import (CheckerAuthoringService, CheckerFinalizationInputs,
    ControlAuthoringService, ControlFinalizationInputs, ControlProposal)
from feature_rl.verifiers.service import CheckerPublicationPending, AuthoringPreparationPending
from feature_rl.verifiers.control_authoring import ControlPublicationPending
from feature_rl.verifiers.behavioral import behavioral_schema
from feature_rl.qualification.evidence import unknown_cost, collapse_costs
from feature_rl.registry import JobSpec, Claim, CostObservation
from .authoring_models import (AuthoringSettings, AuthoringCall, AuthoringFrontier,
    AuthoringRequest, AuthoringReceipt, AuthoringBatch,AuthoringBudgetExceeded,AuthoringBudgetUnverified)
from .construction import put, references, identity
from .packaging import checked, document, typed, read_record, read_bytes, MAX_DOCUMENT
from .locking import candidate_lock
from .factory import FactoryRecoveryRequired, FactoryPublicationFailed


class AuthoringPending(FactoryRecoveryRequired):
    """A concrete upstream publication capability; never permission to infer again."""
    def __init__(self, claim, upstream):
        super().__init__('retain exact M2/M4 publication capability; no provider redispatch',claim)
        self.upstream=upstream


def read_authoring_receipt(store, ref):
    return read_record(store,ref,AuthoringReceipt,'m6-authoring-receipt')


def stage_lane(call):
    if isinstance(call.inputs,ContractFinalizationInputs):return 'authoring','contract'
    if isinstance(call.inputs,CheckerFinalizationInputs):return 'verifier','checker'
    role={'control_id':call.inputs.control_id,'category':call.inputs.category}
    return 'verifier','control-'+hashlib.sha256(canonical_json(role)).hexdigest()


def settings(factory):
    if factory.authoring is None:raise ValueError('actual AuthoringSettings/Codex configuration are required')
    return checked(AuthoringSettings,factory.authoring)


def policy_document(revision, configured):
    return {'version':'m6-authoring-policy-v4','revision':revision,
        'settings':document(configured),'attempts_per_role':3,
        'stage_map':{'initial_authoring':'authoring',
            'checker_generation':'verifier','control_authoring':'verifier'}}


def historical_settings(factory,job):
    from feature_rl.verifiers.loader import read_bytes as read_policy
    raw=read_policy(factory.store,job.spec.configuration,MAX_DOCUMENT,'m6-authoring-policy',private=True)
    value=json.loads(raw)
    configured=AuthoringSettings.model_validate_json(canonical_json(value['settings']))
    if raw!=canonical_json(policy_document(job.spec.implementation,configured)):
        raise ValueError('historical authoring policy differs from its Factory execution')
    return configured


def configuration(factory):
    value=policy_document(factory.revision,settings(factory))
    return put(factory,value,'m6-authoring-policy',dependencies=references(value))


def batch_reference(factory,candidate):
    batch=settings(factory).batch
    if candidate not in batch.candidates:raise ValueError('candidate is outside the frozen authoring batch budget')
    for ref in batch.candidates:typed(factory.store,ref,c.CandidateRecord)
    return put(factory,batch,'m6-authoring-batch',dependencies=references(document(batch)))


def check_budget(factory,candidate,call,batch_ref):
    """Reserve whole call ceilings before enqueue, across the immutable batch.

    Reservations are never refunded by failures. Actual larger known
    usage raises the charge. Unknown controller/storage/USD costs remain unknown;
    the current provider has no spend meter, so real finite-spend dispatch is
    unsupported. A null cap explicitly declares unpriced Codex subscription usage; it is
    not a zero-cost claim or verified monetary comparison budget.
    """
    batch=read_record(factory.store,batch_ref,AuthoringBatch,'m6-authoring-batch')
    from feature_rl.registry import UnknownIdentity
    groups=[]
    for member in batch.candidates:
        try:member_jobs=jobs(factory,member)
        except UnknownIdentity:member_jobs=[]
        groups.extend((member,job,request) for job,request in member_jobs)
    def reserved(entries):
        result=dict(input_tokens=0,output_tokens=0,wall_seconds=0.0,cpu_seconds=0.0,commands=0,memory_bytes=0)
        for job,request in entries:
            limits=request.call.generation.request.limits
            observed=[] if job is None else [cost for entry in factory.registry.accounting(job.job_id).observations
                if entry.observation.source=='m6-generation' for cost in entry.observation.costs]
            for name in ('input_tokens','output_tokens','wall_seconds','cpu_seconds'):
                bound=getattr(limits,name)
                # RLIMIT_CPU has a one-second hard-limit guard. Polling/teardown
                # can still overrun declared wall/CPU; retain measured overruns.
                if name=='cpu_seconds':bound+=1
                known=sum(getattr(cost,name) or 0 for cost in observed)
                result[name]+=max(bound,known)
            result['commands']+=1
            result['memory_bytes']=max(result['memory_bytes'],limits.declared_memory_ceiling_bytes)
        return result
    from types import SimpleNamespace
    proposed=(None,SimpleNamespace(call=call))
    for name,caps,entries in (
        ('candidate',batch.candidate_caps,[(job,request) for member,job,request in groups if member==candidate]),
        ('batch',batch.batch_caps,[(job,request) for _,job,request in groups])):
        usage=reserved((*entries,proposed))
        for field,value in usage.items():
            if value>getattr(caps,field):raise AuthoringBudgetExceeded(f'budget_exhausted: {name} authoring budget: {field}')
        if caps.spend_usd is not None:
            raise AuthoringBudgetUnverified(f'{name} finite spend budget cannot admit unknown CodexGenerationProvider USD and controller/storage costs')


def spec(factory,ref,request):
    return JobSpec(operation='construct',inputs=(request.candidate,ref),configuration=configuration(factory),
        implementation=factory.revision,invocation='m6-author:'+request.lane,attempt_limit=1)


def jobs(factory,candidate):
    result=[]
    for job_id in factory.registry.trace(candidate).jobs:
        job=factory.registry.job(job_id)
        if job.spec.operation=='construct' and job.spec.invocation.startswith('m6-author:'):
            if len(job.spec.inputs)!=2:
                raise ValueError('authoring job changed its exact candidate inputs')
            # The shared batch config depends on every member; trace includes
            # siblings, whose own input still selects their exact candidate.
            if job.spec.inputs[0]!=candidate:continue
            request=read_record(factory.store,job.spec.inputs[1],AuthoringRequest,'m6-authoring-request')
            if request.candidate!=candidate or job.spec.invocation!='m6-author:'+request.lane:
                raise ValueError('authoring request/job lineage differs')
            result.append((job,request))
    return result


def validate_call(factory,candidate,call):
    pair=typed(factory.store,call.source_pair,c.SourcePair)
    value=typed(factory.store,candidate,c.CandidateRecord)
    recipe=typed(factory.store,call.environment.recipe,c.EnvironmentRecipe)
    if (pair.candidate!=candidate or pair.relationship!=value.commits or pair.baseline!=call.resolver.baseline
            or recipe.baseline!=pair.baseline or call.environment.policy not in recipe.provenance.inputs):
        raise ValueError('authoring candidate/source pair/B/environment binding differs')
    from feature_rl.environments import SandboxPolicy
    from feature_rl.environments.profiles import validate_recipe_profile
    policy=read_record(factory.store,call.environment.policy,SandboxPolicy,'sandbox-policy')
    validate_recipe_profile(recipe,policy,factory.store)
    resolver=AuthoringEvidenceResolver(store=factory.store,**call.resolver.model_dump())
    resolver.resolve(call.sources)
    if isinstance(call.inputs,ContractFinalizationInputs):
        from feature_rl.requirements.service import contexts_from_sources
        if call.generation.request.contexts!=contexts_from_sources(call.sources):
            raise ValueError('contract request contexts differ from resolved evidence')
        if call.inputs.runtime_discovery!=call.resolver.runtime_discovery:
            raise ValueError('contract runtime discovery differs from actual resolver')
    else:
        contract=typed(factory.store,call.inputs.contract,c.RequirementContract)
        if call.resolver.request not in contract.provenance.inputs or pair.baseline not in contract.provenance.inputs:
            raise ValueError('frozen contract does not bind the exact authoring request/B')
    if isinstance(call.inputs,(CheckerFinalizationInputs,ControlFinalizationInputs)):
        if call.inputs.baseline!=pair.baseline or call.inputs.environment!=call.environment.recipe:
            raise ValueError('M4 inputs changed the selected B/environment')
    return recipe


def frontier(factory,candidate,source,call):
    model=AuthoringFrontier(candidate=candidate,source=source,source_pair=call.source_pair,
        environment=call.environment,batch=batch_reference(factory,candidate),revision=factory.revision)
    found=[ref for ref in factory.registry.trace(candidate).artifacts if ref.kind=='m6-authoring-frontier'
        and read_record(factory.store,ref,AuthoringFrontier,'m6-authoring-frontier').candidate==candidate]
    if len(found)>1:raise ValueError('ambiguous candidate authoring frontier')
    if found:
        old=read_record(factory.store,found[0],AuthoringFrontier,'m6-authoring-frontier')
        if old.model_copy(update={'revision':factory.revision})!=model:
            raise ValueError('candidate frontier source/environment cannot be replaced')
        return found[0]
    return put(factory,model,'m6-authoring-frontier',dependencies=references(document(model)))


def author(factory,candidate,call):
    candidate=checked(c.ArtifactRef,candidate);call=checked(AuthoringCall,call);settings(factory)
    batch_ref=batch_reference(factory,candidate)
    # One process may consume a given batch at a time. This is an ephemeral
    # lock only; all durable reservations are the existing Registry jobs.
    with candidate_lock(factory.store,batch_ref), candidate_lock(factory.store,candidate):
        source=factory._screen_source(candidate)
        if source.disposition!=c.Disposition.SUCCESS:return source
        validate_call(factory,candidate,call)
        front=frontier(factory,candidate,source.artifacts[0],call)
        stage,lane=stage_lane(call);previous=None
        existing=jobs(factory,candidate)
        # Stable call replay precedes new budget reservation; request IDs alone
        # still cannot turn a changed call into another initial attempt.
        for job,old in existing:
            if old.call==call and old.frontier==front:
                if job.spec.implementation!=factory.revision:
                    if job.state=='completed':return job.result
                    raise FactoryRecoveryRequired('recover the original authoring revision',factory.registry.attempts(job.job_id)[-1].claim)
                if job.state=='completed':return job.result
                if job.attempts:raise FactoryRecoveryRequired('authoring attempt already exists',factory.registry.attempts(job.job_id)[-1].claim)
                return dispatch(factory,job,old)
        if any(job.state!='completed' for job,_ in existing):
            raise ValueError('all earlier candidate authoring attempts require reconciliation before another dispatch')
        lanes=[(job,old) for job,old in existing if old.lane==lane]
        if lanes:
            predecessors={old.previous for _,old in lanes}
            terminal=[(job,old) for job,old in lanes if job.spec.inputs[1] not in predecessors]
            if len(terminal)!=1:raise ValueError('ambiguous authoring lane version chain')
            prior_job,prior=terminal[0];previous=prior_job.spec.inputs[1]
            if call.generation.diagnosis is None:raise ValueError('every repeated authoring role requires a diagnosis and changed input')
            if semantic_request_sha256(prior.call.generation.request)==semantic_request_sha256(call.generation.request):
                raise ValueError('retry must change meaningful request content, not only IDs')
        elif call.generation.diagnosis is not None:
            raise ValueError('initial role cannot claim a retry without a retained predecessor')
        if len(lanes)>=3:
            raise AuthoringBudgetExceeded('budget_exhausted: each authoring role permits at most three attempts')
        check_budget(factory,candidate,call,batch_ref)
        request=AuthoringRequest(candidate=candidate,frontier=front,call=call,previous=previous,
            stage=stage,lane=lane)
        ref=put(factory,request,'m6-authoring-request',dependencies=references(document(request)))
        job=factory.registry.enqueue(spec(factory,ref,request))
        return dispatch(factory,job,request)


def observation(factory,claim,source):
    values=[o for o in factory.registry.accounting(claim.job_id).observations
        if o.attempt_id==claim.attempt_id and o.observation.source==source]
    if len(values)>1:raise ValueError('one selected snapshot per authoring observation source required')
    return values[0].observation if values else None


def observe(factory,claim,source,refs,costs):
    previous=observation(factory,claim,source)
    return factory.registry.reconcile(claim,CostObservation(source=source,upstream_attempt_id=claim.attempt_id,
        revision=1 if previous is None else previous.revision+1,
        receipts=tuple(dict.fromkeys((*(previous.receipts if previous else ()),*refs))),costs=collapse_costs(costs)))


def authoring_costs(factory,claim):
    return observation(factory,claim,'m6-generation').costs


def archive_writer(factory,claim,request_ref):
    def archive(data,kind,visibility):
        ref=factory.store.put_bytes(data,kind,visibility)
        deps=()
        if kind=='generation-request':deps=references(json.loads(data))
        if kind=='generation-status':deps=tuple(c.ArtifactRef.model_validate_json(canonical_json(v)) for v in json.loads(data)['archive_refs'].values())
        factory.registry.register(ref,dependencies=tuple(dict.fromkeys(deps)))
        previous=observation(factory,claim,'m6-generation')
        costs=previous.costs
        if kind=='generation-cost':
            actual=c.CostRecord.model_validate_json(data)
            costs=tuple(cost for cost in costs if cost.category!=actual.category)+(actual,)
        observe(factory,claim,'m6-generation',(request_ref,ref),costs)
        return ref
    return archive


def checker_schema(factory,call):
    plan=typed(factory.store,call.inputs.scenario_plan,c.ScenarioPlan)
    return behavioral_schema(plan)


def service(factory,call,claim,request_ref):
    conf=settings(factory)
    provider=CodexGenerationProvider(config=conf.codex,archive=archive_writer(factory,claim,request_ref))
    resolver=AuthoringEvidenceResolver(store=factory.store,**call.resolver.model_dump())
    if isinstance(call.inputs,ContractFinalizationInputs):cls,schema=ContractAuthoringService,RequirementContractProposal
    elif isinstance(call.inputs,CheckerFinalizationInputs):cls,schema=CheckerAuthoringService,checker_schema(factory,call)
    else:cls,schema=ControlAuthoringService,ControlProposal
    revision=conf.m2_revision if cls is ContractAuthoringService else conf.m4_revision
    return cls(provider=provider,store=factory.store,resolver=resolver,revision=revision,evidence_scope=conf.evidence_scope),schema


def dispatch(factory,job,request):
    claim=factory.registry.claim(job.job_id,owner='feature_rl.pipeline.Factory',claim_key=uuid.uuid4().hex)
    ref=job.spec.inputs[1]
    try:
        observe(factory,claim,'m6-generation',(ref,),(unknown_cost('authoring','Authoring intent is not yet reconciled; provider dispatch and usage remain unconfirmed'),
            unknown_cost('construction','Finalization/controller work remains separately unmeasured'),unknown_cost('storage')))
    except Exception as exc:raise FactoryRecoveryRequired('authoring intent unconfirmed; do not dispatch provider',claim) from exc
    return execute(factory,claim,ref,request)


def prior_journals(factory,request):
    if request.previous is None:return ()
    matches=[job for job,old in jobs(factory,request.candidate) if job.spec.inputs[1]==request.previous]
    if len(matches)!=1 or matches[0].state!='completed':raise ValueError('selected previous authoring result is missing')
    receipt=read_authoring_receipt(factory.store,matches[0].result.artifacts[-1])
    if (receipt.request!=request.previous or receipt.claim.job_id!=matches[0].job_id
            or receipt.lane!=request.lane or receipt.disposition!=matches[0].result.disposition
            or matches[0].result.artifacts!=(*receipt.outputs,matches[0].result.artifacts[-1])):
        raise ValueError('previous journal chain lacks the exact selected lane result')
    return receipt.journal_refs if receipt.disposition==c.Disposition.REJECTED else ()


def execute(factory,claim,ref,request,*,recovered=None):
    factory.registry.assert_usable(ref)
    actual,schema=service(factory,request.call,claim,ref)
    prior=prior_journals(factory,request)
    candidate=request.call.generation
    if not prior:
        # A changed previously accepted artifact starts a new local finalizer
        # chain; the outer frozen request retains its diagnosis and resource reservation.
        candidate=candidate.model_copy(update={'diagnosis':None,'changed_input':None})
    try:
        result=actual.generate((candidate,),request.call.inputs,request.call.sources,prior_journal_refs=prior,
            recovered_result=recovered if isinstance(recovered,GenerationResult) else None,
            recovered_error=recovered if isinstance(recovered,GenerationProviderError) else None)
        validate_recovered_generation(factory.store,candidate.request,schema,result.generation)
        outputs=tuple(getattr(result,name) for name in ('contract_ref','verifier_ref','record_ref') if hasattr(result,name))
        if len(outputs)!=1:raise ValueError('actual authoring result must select one concrete output')
        journals=result.journal_refs;disposition=c.Disposition.SUCCESS;reason='Actual M2/M4 authored artifact selected; qualification remains pending'
    except AuthoringExhausted as exc:
        outputs=();journals=exc.journal_refs;disposition=c.Disposition.REJECTED;reason=str(exc)
    except (GenerationProviderError,AuthoringPublicationPending,AuthoringJournalPublicationPending,
            CheckerPublicationPending,ControlPublicationPending) as exc:
        raise AuthoringPending(claim,exc) from exc
    except AuthoringPreparationPending as exc:
        raise FactoryRecoveryRequired('validated provider result is retained; recover inert finalization without provider execution',claim) from exc
    except Exception as exc:
        raise FactoryRecoveryRequired('authoring controller outcome unknown; inspect retained archives before any new call',claim) from exc
    # Provider wall is already in its own cumulative record. Controller overhead
    # is left unknown here because callbacks and finalization interleave with it.
    frozen=AuthoringReceipt(claim=claim,request=ref,outputs=outputs,journal_refs=journals,
        disposition=disposition,reason=reason,stage=request.stage,lane=request.lane,
        costs=authoring_costs(factory,claim),revision=factory.revision,recorded_at=datetime.now(timezone.utc))
    return publish(factory,canonical_json(document(frozen)),claim)


def register_output(factory,ref):
    if ref.encoding=='json':
        value=factory.store.get_artifact(ref,max_envelope_bytes=MAX_DOCUMENT)
        for child in references(document(value)):
            if child.kind in ('m4-case-input','m4-case-comparison'):register_output(factory,child)
        factory.registry.register(ref)
    elif ref.kind in ('m4-control-record','m4-submission','m4-case-input','m4-case-comparison'):
        raw=read_bytes(factory.store,ref,MAX_DOCUMENT)
        deps=references(json.loads(raw))
        for child in deps:
            if child.kind=='m4-submission':register_output(factory,child)
        factory.registry.register(ref,dependencies=deps)
    else:factory.registry.register(ref)


def validated(factory,claim):
    claim=checked(Claim,claim);job=factory.registry.job(claim.job_id)
    if len(job.spec.inputs)!=2 or not any(a.claim==claim for a in factory.registry.attempts(job.job_id)):
        raise ValueError('unknown authoring claim')
    request=read_record(factory.store,job.spec.inputs[1],AuthoringRequest,'m6-authoring-request')
    expected=JobSpec(operation='construct',inputs=(request.candidate,job.spec.inputs[1]),
        configuration=job.spec.configuration,implementation=factory.revision,
        invocation='m6-author:'+request.lane,attempt_limit=1)
    if job.spec!=expected or historical_settings(factory,job)!=settings(factory):
        raise ValueError('recover with the original authoring service configuration')
    return job,request


def publish(factory,payload,claim):
    if type(payload) is not bytes or len(payload)>MAX_DOCUMENT:raise ValueError('authoring receipt reader cap')
    receipt=AuthoringReceipt.model_validate_json(payload)
    job,request=validated(factory,claim)
    if (canonical_json(document(receipt))!=payload or receipt.claim!=claim or receipt.request!=job.spec.inputs[1]
            or receipt.revision!=factory.revision or (receipt.stage,receipt.lane)!=(request.stage,request.lane)):
        raise ValueError('authoring frozen receipt changed exact request/claim')
    if job.state=='completed':return job.result
    snapshot=observation(factory,claim,'m6-generation')
    if snapshot is None or authoring_costs(factory,claim)!=receipt.costs:
        raise ValueError('frozen authoring costs differ from selected generation accounting')
    selected=[r for r in snapshot.receipts if r.kind=='m6-authoring-receipt']
    if selected and (len(selected)!=1 or read_bytes(factory.store,selected[0],MAX_DOCUMENT)!=payload):
        raise ValueError('authoring publication differs from the already selected frozen receipt')
    validate_receipt(factory,request,receipt)
    try:
        for output in receipt.outputs:register_output(factory,output)
        for journal in receipt.journal_refs:
            value=json.loads(read_bytes(factory.store,journal,65536))
            factory.registry.register(journal,dependencies=references(value))
        ref=selected[0] if selected else put(factory,receipt,'m6-authoring-receipt',dependencies=references(document(receipt)))
        if not selected:observe(factory,claim,'m6-generation',(ref,),snapshot.costs)
    except Exception as exc:raise FactoryPublicationFailed('retain exact authored outputs/journals/costs',claim,payload,kind='m6-authoring-receipt') from exc
    ev=c.EvidenceRecord(producer='feature_rl.pipeline.Factory',command=('Factory.author',receipt.request.sha256),
        recorded_at=receipt.recorded_at,exit_status=0 if receipt.disposition==c.Disposition.SUCCESS else 1,
        artifacts=(ref,),revision=factory.revision,scope=settings(factory).evidence_scope)
    result=c.OperationResult(operation='construct',disposition=receipt.disposition,artifacts=(*receipt.outputs,ref),
        evidence=(ev,),costs=receipt.costs,reason=receipt.reason)
    selected=factory.registry.accounting(claim.job_id)
    observations=tuple(o.observation_id for o in selected.observations if o.attempt_id==claim.attempt_id)
    result=result.model_copy(update={'costs':tuple(cost for o in selected.observations
        if o.attempt_id==claim.attempt_id for cost in o.observation.costs)})
    try:return factory.registry.complete(claim,result,observations=observations).result
    except Exception as exc:raise FactoryRecoveryRequired('authoring completion unconfirmed; recover frozen result',claim) from exc


def validate_receipt(factory,request,receipt):
    """Frozen replay binds the actual journal/archive selection and output lineage."""
    classes={ContractFinalizationInputs:(RequirementContractProposal,'contract-authoring-journal','RequirementContract','feature_rl.requirements.ContractAuthoringService'),
        CheckerFinalizationInputs:(None,'checker-authoring-journal','VerifierBundle','feature_rl.verifiers.CheckerAuthoringService'),
        ControlFinalizationInputs:(ControlProposal,'control-authoring-journal','m4-control-record','feature_rl.verifiers.ControlAuthoringService')}
    schema,journal_kind,output_kind,producer=classes[type(request.call.inputs)]
    if schema is None:schema=checker_schema(factory,request.call)
    if not 1<=len(receipt.journal_refs)<=3:raise ValueError('actual bounded authoring journal chain required')
    if receipt.journal_refs[:-1]!=prior_journals(factory,request):
        raise ValueError('receipt prior journals differ from the selected previous lane chain')
    values=[]
    for index,ref in enumerate(receipt.journal_refs,1):
        raw=read_bytes(factory.store,ref,65536,kind=journal_kind)
        value=json.loads(raw)
        if canonical_json(value)!=raw or value.get('attempt_index')!=index or value.get('stage')!=request.call.generation.request.stage.value:
            raise ValueError('authoring journal changed canonical stage/index')
        if index<len(receipt.journal_refs) and value.get('status')!='rejected':
            raise ValueError('prior authoring journals must be rejected attempts')
        values.append(value)
    last=values[-1]
    semantic=semantic_request_sha256(request.call.generation.request)
    if (last.get('request_sha256')!=identity(request.call.generation.request)
            or last.get('semantic_request_sha256')!=semantic):
        raise ValueError('authoring journal belongs to another exact request')
    expected='accepted' if receipt.disposition==c.Disposition.SUCCESS else 'rejected'
    if last.get('status')!=expected or receipt.disposition not in (c.Disposition.SUCCESS,c.Disposition.REJECTED):
        raise ValueError('authoring selected disposition differs from journal')
    outcome=archived_outcome(factory,request,receipt.claim,schema)
    if last.get('generation_record')!=document(outcome.record) or last.get('cost')!=document(outcome.cost):
        raise ValueError('journal changed its actual provider record/cost')
    if receipt.disposition!=c.Disposition.SUCCESS:
        if receipt.outputs:raise ValueError('rejected authoring cannot select a successful artifact')
        return
    if len(receipt.outputs)!=1 or receipt.outputs[0].kind!=output_kind or not isinstance(outcome,GenerationResult):
        raise ValueError('successful authoring requires exact output kind and successful archived generation')
    output=receipt.outputs[0]
    if output_kind=='m4-control-record':
        from feature_rl.verifiers.control_authoring import ControlRecord
        model=read_record(factory.store,output,ControlRecord,output_kind);provenance=model.generation_provenance
        if (model.baseline,model.contract,model.environment)!=(request.call.inputs.baseline,request.call.inputs.contract,request.call.inputs.environment):
            raise ValueError('control output changed exact B/contract/environment')
    else:
        model=factory.store.get_artifact(output,max_envelope_bytes=MAX_DOCUMENT);provenance=model.provenance
        if isinstance(request.call.inputs,CheckerFinalizationInputs) and model.contract!=request.call.inputs.contract:
            raise ValueError('authored output changed its exact frozen contract')
    revision=settings(factory).m2_revision if isinstance(request.call.inputs,ContractFinalizationInputs) else settings(factory).m4_revision
    ev=provenance.evidence[-1]
    if (provenance.producer!=producer or provenance.producer_version!=revision
            or ev.producer!='feature_rl.generation.CodexGenerationProvider'
            or ev.command!=('generate',request.call.generation.request.request_id)
            or len(ev.artifacts)!=len(outcome.record.archives)
            or set(ev.artifacts)!=set(outcome.record.archives.values()) or ev.recorded_at!=outcome.record.recorded_at
            or ev.revision!=revision or ev.scope!=settings(factory).evidence_scope
            or model.costs[-1]!=outcome.cost):
        raise ValueError('authored output lost its actual service/provider provenance and costs')


def archived_outcome(factory,request,claim,schema):
    snapshot=observation(factory,claim,'m6-generation')
    status_refs=[r for r in snapshot.receipts if r.kind=='generation-status']
    if not status_refs:raise FactoryRecoveryRequired('provider status/costs are incomplete; no provider redispatch',claim)
    ref=status_refs[-1];status=json.loads(read_bytes(factory.store,ref,MAX_DOCUMENT))
    record=GenerationCallRecord(attempt_id=status['attempt_id'],recorded_at=datetime.fromisoformat(status['recorded_at']),
        request_id=status['request_id'],response_id=status['response_id'],success=status['success'],
        generation_succeeded=status['generation_succeeded'],publication_complete=status['publication_complete'],
        error_code=status['error_type'],archives={**{k:c.ArtifactRef.model_validate_json(canonical_json(v)) for k,v in status['archive_refs'].items()},'status':ref})
    return provider_outcome(factory.store,request.call.generation.request,record,schema)


def provider_outcome(store,request,record,schema):
    from feature_rl.requirements.service import archive_read_caps
    def raw(name):
        payload_cap,envelope_cap=archive_read_caps(name,request)
        return store.get_bytes(record.archives[name],max_payload_bytes=payload_cap,max_envelope_bytes=envelope_cap)
    cost=c.CostRecord.model_validate_json(raw('cost'))
    if record.success:
        from feature_rl.generation.events import parse_events
        from feature_rl.generation.schema import _parse_envelope
        text,usage=parse_events(raw('events'))
        content=_parse_envelope(text,request,schema)
        value=GenerationResult(content=content,usage=usage,cost=cost,record=record)
    else:
        response=json.loads(raw('response')) if 'response' in record.archives else None
        observed=json.loads(raw('usage')).get('observed') if 'usage' in record.archives else None
        status=json.loads(raw('status'))
        value=GenerationProviderError(status['error'],record,cost=cost,response=response,usage_observation=observed)
    validate_recovered_generation(store,request,schema,value)
    return value


def recover(factory,claim):
    job,request=validated(factory,claim)
    if job.state=='completed':return job.result
    with candidate_lock(factory.store,request.candidate):
        snapshot=observation(factory,claim,'m6-generation')
        refs=[] if snapshot is None else [r for r in snapshot.receipts if r.kind=='m6-authoring-receipt']
        if refs:return publish(factory,read_bytes(factory.store,refs[-1],MAX_DOCUMENT),claim)
        _,schema=service(factory,request.call,claim,job.spec.inputs[1])
        value=archived_outcome(factory,request,claim,schema)
        return execute(factory,claim,job.spec.inputs[1],request,recovered=value)


def retry(factory,pending):
    job,request=validated(factory,pending.claim)
    if job.state=='completed':return job.result
    with candidate_lock(factory.store,request.candidate):
        upstream=pending.upstream
        if isinstance(upstream,GenerationProviderError):
            archive=archive_writer(factory,pending.claim,job.spec.inputs[1])
            try:
                value=upstream.replay_result(archive) if upstream.recovery.generation_succeeded else upstream.replay_error(archive)
            except GenerationProviderError as exc:raise AuthoringPending(pending.claim,exc) from exc
            return execute(factory,pending.claim,job.spec.inputs[1],request,recovered=value)
        # Exact finalizer capabilities publish frozen artifact bytes; the provider
        # archive is already selected and is not called again.
        if isinstance(upstream,AuthoringJournalPublicationPending):
            upstream.replay(factory.store)
        elif isinstance(upstream,(AuthoringPublicationPending,CheckerPublicationPending,ControlPublicationPending)):
            output=upstream.replay(factory.store)
            if isinstance(upstream,AuthoringPublicationPending):outputs,journals=(output[0],),output[1]
            else:
                outputs=(output.verifier_ref,) if hasattr(output,'verifier_ref') else (output.record_ref,)
                journals=output.journal_refs
            receipt=AuthoringReceipt(claim=pending.claim,request=job.spec.inputs[1],outputs=outputs,journal_refs=journals,
                disposition=c.Disposition.SUCCESS,reason='Actual retained authoring publication selected without provider redispatch',
                stage=request.stage,lane=request.lane,costs=authoring_costs(factory,pending.claim),
                revision=factory.revision,recorded_at=datetime.now(timezone.utc))
            return publish(factory,canonical_json(document(receipt)),pending.claim)
        else:raise ValueError('unsupported actual authoring recovery capability')
        _,schema=service(factory,request.call,pending.claim,job.spec.inputs[1])
        return execute(factory,pending.claim,job.spec.inputs[1],request,
            recovered=archived_outcome(factory,request,pending.claim,schema))
