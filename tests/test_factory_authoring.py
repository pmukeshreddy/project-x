"""Actual M2/M4 provider/parser/finalizer diagnostics with a TEST process only."""
import json
import pytest
from feature_rl import contracts as c
from feature_rl.artifacts import canonical_json
from feature_rl.registry import Registry
from feature_rl.pipeline import Factory
from feature_rl.pipeline.authoring_models import AuthoringSettings,AuthoringCall,ResolverInputs
from feature_rl.environments import PreparedEnvironment
from feature_rl.requirements import (AuthoringEvidenceResolver, GroundedSource, RetrievalPolicy,
    ContractFinalizationInputs, RequirementContractProposal, ContractAuthoringService, build_contract_request)


def authoring_fixture(tmp_path):
    from feature_rl.artifacts import ArtifactStore
    from test_authoring import bound_discovery_text
    from test_generation import limits
    from codex_fixtures import config, CodexRunner, events, response
    from feature_rl.generation import CodexGenerationProvider
    store=ArtifactStore(tmp_path/'objects',c.ActorRole.CONTROLLER)
    task=store.get_artifact(task_fixture(store))
    contract=store.get_artifact(task.contract)
    text='DIAGNOSTIC ONLY echo supplied word'
    request_ref=store.put_bytes(text.encode(),'authoring-request',c.Visibility.AUTHORING)
    discovery_text=bound_discovery_text(baseline=task.baseline,recipe=task.environment)
    discovery=store.put_bytes(discovery_text.encode(),'runtime-discovery',c.Visibility.AUTHORING)
    sources=(GroundedSource(context_id='REQUEST',role='request',source=request_ref,locator='authoring-request:whole',text=text,provenance_label='reconstructed_specification'),
        GroundedSource(context_id='BASELINE',role='baseline',source=task.baseline,locator='src/click/__init__.py:1-1',text='# synthetic baseline\n',provenance_label='existing_obligation'),
        GroundedSource(context_id='DISCOVERY',role='baseline',source=discovery,locator='m3:runtime-discovery-v1',text=discovery_text,provenance_label='existing_obligation'))
    def link(source):
        return c.EvidenceLink(source=source.source,locator=source.locator,quote=source.text.strip(),provenance_label=source.provenance_label)
    req=contract.requirements[0].model_copy(update={'evidence':(link(sources[0]),),'observable':'combined terminal output'})
    feature=contract.feature_files[0].model_copy(update={'evidence':(link(sources[1]),)})
    data=contract.model_dump()
    data.update(requirements=(req,),feature_files=(feature,),entry_points=tuple(json.loads(discovery_text)['entry_points']))
    proposal=RequirementContractProposal(**{name:data[name] for name in RequirementContractProposal.model_fields})
    inputs=ContractFinalizationInputs(visible_request=text,allowed_requirement_ids=('echo',),entry_points=data['entry_points'],
        supported_observables=tuple(json.loads(discovery_text)['supported_observables']),runtime_discovery=discovery,
        allowed_changes=contract.allowed_changes,public_checks=(),episode_limits=contract.episode_limits,
        provenance_label='reconstructed_specification',visibility=c.Visibility.AUTHORING,
        provenance=contract.provenance.model_copy(update={'inputs':(request_ref,task.baseline,discovery)}),costs=contract.costs)
    resolver=AuthoringEvidenceResolver(store=store,request=request_ref,baseline=task.baseline,runtime_discovery=discovery,
        public_checks=(),retrieval_policy=RetrievalPolicy(allowed_paths=('src/click/__init__.py',),max_archive_bytes=1048576,max_files=10,max_selected_bytes=10000,max_spans=10))
    request=build_contract_request(request_id='CONTRACT_1',response_id='RESPONSE_1',prompt_id='PROMPT_1',sources=sources,
        allowed_requirement_ids=('echo',),entry_points=inputs.entry_points,supported_observables=inputs.supported_observables,
        allowed_changes=inputs.allowed_changes,limits=limits())
    runner=CodexRunner(events(response(request,proposal.model_dump(mode='json'))))
    provider=CodexGenerationProvider(config=config(store.root),archive=store.put_bytes,runner=runner)
    service=ContractAuthoringService(provider=provider,store=store,resolver=resolver,revision='a'*40,evidence_scope='unit_diagnostic')
    return store,proposal,inputs,sources,request,service,runner,request

from m5_fixtures import task_fixture


def setup(tmp_path,monkeypatch):
    store,proposal,inputs,sources,request,service,runner,generation=authoring_fixture(tmp_path)
    parent=store.get_artifact(task_fixture(store))
    pair=store.get_artifact(parent.source_pair)
    pair=pair.model_copy(update={'baseline':service.resolver.baseline})
    pair_ref=store.put_artifact(pair)
    recipe=store.get_artifact(parent.environment)
    policy=next(ref for ref in recipe.provenance.inputs if ref.kind=='sandbox-policy')
    from feature_rl.generation.provider import CodexGenerationProvider
    actual_init=CodexGenerationProvider.__init__
    # Keep the real provider and replace only its external Codex process.
    def init(provider,*,config,archive,runner=None):
        return actual_init(provider,config=service.provider._config,archive=archive,runner=service.provider._runner)
    monkeypatch.setattr(CodexGenerationProvider,'__init__',init)
    from feature_rl.pipeline.authoring_models import AuthoringBatch,AuthoringCaps
    caps=AuthoringCaps(input_tokens=20_000_000,output_tokens=20_000_000,wall_seconds=100_000.0,
        cpu_seconds=100_000.0,commands=1000,memory_bytes=5_368_709_120,spend_usd=None)
    settings=AuthoringSettings(codex=service.provider._config,m2_revision='a'*40,evidence_scope='unit_diagnostic',
        batch=AuthoringBatch(candidates=(pair.candidate,),candidate_caps=caps,batch_caps=caps))
    assert hasattr(Factory,'author'),'Factory actual authoring is missing'
    factory=Factory(store=store,registry=Registry(tmp_path/'registry',store),revision='e'*40,authoring=settings)
    resolver=service.resolver
    call=AuthoringCall(source_pair=pair_ref,environment=PreparedEnvironment(recipe=parent.environment,policy=policy),
        resolver=ResolverInputs(request=resolver.request,baseline=resolver.baseline,runtime_discovery=resolver.runtime_discovery,
            public_checks=resolver.public_checks,retrieval_policy=resolver.retrieval_policy,request_provenance=resolver.request_provenance),
        generation=generation,inputs=inputs,sources=sources)
    return factory,pair.candidate,call,runner


def test_actual_contract_result_costs_archives_and_idempotent_completion(tmp_path,monkeypatch):
    factory,candidate,call,runner=setup(tmp_path,monkeypatch)
    result=factory.author(candidate,call=call)
    assert result.disposition==c.Disposition.SUCCESS
    assert result.artifacts[0].kind=='RequirementContract'
    assert len(runner.calls)==1
    from feature_rl.pipeline.authoring import read_authoring_receipt
    receipt=read_authoring_receipt(factory.store,result.artifacts[-1])
    ledger=factory.registry.accounting(receipt.claim.job_id)
    assert any(o.observation.source=='m6-generation' and any(r.kind=='generation-status' for r in o.observation.receipts) for o in ledger.observations)
    assert any(cost.input_tokens==41 for cost in result.costs)
    assert factory.author(candidate,call=call)==result and len(runner.calls)==1


def test_rejected_generation_retries_same_prompt_without_history(tmp_path,monkeypatch):
    factory,candidate,call,runner=setup(tmp_path,monkeypatch)
    runner.exit_status=1
    rejected=factory.author(candidate,call=call)
    assert rejected.disposition==c.Disposition.REJECTED
    runner.exit_status=0
    changed=retry_call(call,1);bind_events(runner,changed.generation)
    result=factory.author(candidate,call=changed)
    assert result.disposition==c.Disposition.SUCCESS
    from feature_rl.pipeline.authoring import read_authoring_receipt
    receipt=read_authoring_receipt(factory.store,result.artifacts[-1])
    assert len(receipt.journal_refs)==1
    assert len(runner.calls)==2


def test_provider_registration_crash_stays_unknown_without_redispatch(tmp_path,monkeypatch):
    factory,candidate,call,runner=setup(tmp_path,monkeypatch)
    original=factory.registry.reconcile
    def crash(claim,observation):
        result=original(claim,observation)
        if any(ref.kind=='generation-attempt' for ref in observation.receipts):
            raise SystemExit('TEST process boundary after durable provider-attempt receipt')
        return result
    monkeypatch.setattr(factory.registry,'reconcile',crash)
    with pytest.raises(SystemExit):factory.author(candidate,call=call)
    monkeypatch.setattr(factory.registry,'reconcile',original)
    jobs=[factory.registry.job(j) for j in factory.registry.trace(candidate).jobs]
    job=next(j for j in jobs if j.spec.invocation.startswith('m6-author:'))
    claim=factory.registry.attempts(job.job_id)[0].claim
    from feature_rl.pipeline import FactoryRecoveryRequired
    with pytest.raises(FactoryRecoveryRequired):factory.recover(claim)
    assert not runner.calls


def test_frozen_factory_publication_loss_replays_without_provider(tmp_path,monkeypatch):
    factory,candidate,call,runner=setup(tmp_path,monkeypatch)
    original=factory.store.put_bytes
    def lost(data,kind,visibility):
        result=original(data,kind,visibility)
        if kind=='m6-authoring-receipt':raise OSError('TEST lost outer publication reply')
        return result
    monkeypatch.setattr(factory.store,'put_bytes',lost)
    from feature_rl.pipeline import FactoryPublicationFailed
    with pytest.raises(FactoryPublicationFailed) as error:factory.author(candidate,call=call)
    monkeypatch.setattr(factory.store,'put_bytes',original)
    result=factory.retry_publication(error.value)
    assert factory.store.get_bytes(result.artifacts[-1])==error.value.payload
    assert factory.recover(error.value.claim)==result and len(runner.calls)==1


def test_retained_publication_cannot_replace_selected_cost_snapshot(tmp_path,monkeypatch):
    factory,candidate,call,runner=setup(tmp_path,monkeypatch)
    original=factory.store.put_bytes
    def lost(data,kind,visibility):
        if kind=='m6-authoring-receipt':raise OSError('TEST publication before selection')
        return original(data,kind,visibility)
    monkeypatch.setattr(factory.store,'put_bytes',lost)
    from feature_rl.pipeline import FactoryPublicationFailed
    with pytest.raises(FactoryPublicationFailed) as pending:factory.author(candidate,call=call)
    monkeypatch.setattr(factory.store,'put_bytes',original)
    before=factory.registry.accounting(pending.value.claim.job_id)
    modified=json.loads(pending.value.payload)
    cost=next(cost for cost in modified['costs'] if cost['category']=='authoring')
    cost['input_tokens']=410
    forged=FactoryPublicationFailed('TEST altered cost capability',pending.value.claim,
        canonical_json(modified),kind='m6-authoring-receipt')
    with pytest.raises(ValueError,match='accounting|cost'):factory.retry_publication(forged)
    assert factory.registry.accounting(pending.value.claim.job_id)==before
    assert factory.retry_publication(pending.value).disposition==c.Disposition.SUCCESS
    assert len(runner.calls)==1


def retry_call(call,index):
    request=call.generation.model_copy(update={'request_id':'RETRY_'+str(index),
        'response_id':'RESPONSE_'+str(index),'prompt_id':'PROMPT_'+str(index)})
    return call.model_copy(update={'generation':request})


def bind_events(runner,request):
    events=[json.loads(line) for line in runner.stdout.splitlines()]
    for event in events:
        if event['type']=='item.completed' and event['item']['type']=='agent_message':
            envelope=json.loads(event['item']['text']);envelope['response_id']=request.response_id
            event['item']['text']=json.dumps(envelope)
    runner.stdout=('\n'.join(json.dumps(event) for event in events)+'\n').encode()


def test_contract_authoring_attempt_limit(tmp_path,monkeypatch):
    factory,candidate,call,runner=setup(tmp_path,monkeypatch)
    factory.author(candidate,call=call)
    for index in (1,2):
        changed=retry_call(call,index);bind_events(runner,changed.generation)
        assert factory.author(candidate,call=changed).disposition==c.Disposition.SUCCESS
    with pytest.raises(ValueError,match='three attempts'):
        factory.author(candidate,call=retry_call(call,3))
    assert len(runner.calls)==3


def test_failed_generation_cost_link_is_reconciled_by_publication_replay(tmp_path,monkeypatch):
    factory,candidate,call,runner=setup(tmp_path,monkeypatch)
    original=factory.registry.reconcile;failed=False
    def fail(claim,observation):
        nonlocal failed
        if not failed and any(ref.kind=='generation-cost' for ref in observation.receipts):
            failed=True;raise OSError('TEST cost CAS exists before Registry linkage')
        return original(claim,observation)
    monkeypatch.setattr(factory.registry,'reconcile',fail)
    from feature_rl.pipeline.authoring import AuthoringPending
    with pytest.raises(AuthoringPending) as error:factory.author(candidate,call=call)
    assert error.value.upstream.cost.input_tokens==41 and len(runner.calls)==1
    monkeypatch.setattr(factory.registry,'reconcile',original)
    result=factory.retry_publication(error.value)
    assert result.disposition==c.Disposition.SUCCESS and len(runner.calls)==1
    assert any(cost.input_tokens==41 for cost in result.costs)


def test_recovery_after_provider_status_reuses_archived_outcome_only(tmp_path,monkeypatch):
    factory,candidate,call,runner=setup(tmp_path,monkeypatch)
    original=factory.store.put_artifact
    def crash(artifact):
        if type(artifact) is c.VerifierBundle:raise SystemExit('TEST crash before final artifact selection')
        return original(artifact)
    monkeypatch.setattr(factory.store,'put_artifact',crash)
    with pytest.raises(SystemExit):factory.author(candidate,call=call)
    job=next(factory.registry.job(j) for j in factory.registry.trace(candidate).jobs
        if factory.registry.job(j).spec.invocation.startswith('m6-author:'))
    monkeypatch.setattr(factory.store,'put_artifact',original)
    result=factory.recover(factory.registry.attempts(job.job_id)[0].claim)
    assert result.disposition==c.Disposition.SUCCESS and len(runner.calls)==1


@pytest.mark.parametrize('field,value',[('input_tokens',1),('output_tokens',1),('wall_seconds',0.1),
    ('cpu_seconds',0.1),('memory_bytes',1),('spend_usd',1.0)])
def test_frozen_candidate_caps_block_before_provider(tmp_path,monkeypatch,field,value):
    factory,candidate,call,runner=setup(tmp_path,monkeypatch)
    caps=factory.authoring.batch.candidate_caps.model_copy(update={field:value})
    factory.authoring=factory.authoring.model_copy(update={'batch':factory.authoring.batch.model_copy(update={'candidate_caps':caps})})
    with pytest.raises(ValueError,match='budget|spend'):factory.author(candidate,call=call)
    assert not runner.calls


def test_frozen_batch_command_cap_combines_distinct_candidates(tmp_path,monkeypatch):
    factory,candidate,call,runner=setup(tmp_path,monkeypatch)
    original=factory.store.get_artifact(candidate)
    other=factory.store.put_artifact(original.model_copy(update={'request_lineage':('TEST_OTHER_CANDIDATE',)}))
    pair=factory.store.get_artifact(call.source_pair)
    second=call.model_copy(update={'source_pair':factory.store.put_artifact(pair.model_copy(update={'candidate':other}))})
    batch=factory.authoring.batch.model_copy(update={'candidates':(candidate,other),
        'batch_caps':factory.authoring.batch.batch_caps.model_copy(update={'commands':1})})
    factory.authoring=factory.authoring.model_copy(update={'batch':batch})
    assert factory.author(candidate,call=call).disposition==c.Disposition.SUCCESS
    with pytest.raises(ValueError,match='batch.*budget'):factory.author(other,call=second)
    assert len(runner.calls)==1
    # Replacing a frozen batch/revision cannot turn existing candidate use into zero.
    expanded=batch.model_copy(update={'batch_caps':batch.batch_caps.model_copy(update={'commands':2})})
    factory.authoring=factory.authoring.model_copy(update={'batch':expanded})
    with pytest.raises(ValueError,match='frontier|budget'):factory.author(candidate,call=retry_call(call,1))


def test_authoring_contracts_have_no_removed_control_or_repair_configuration():
    assert 'control_plan' not in AuthoringCall.model_fields
    assert 'attack' not in AuthoringCall.model_fields
    assert 'semantic_repair_authorization' not in AuthoringSettings.model_fields
    assert not hasattr(Factory,'assemble_checker')
    assert not hasattr(Factory,'import_rejected_authoring')
