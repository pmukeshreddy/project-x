"""Actual M2/M4 provider/parser/finalizer diagnostics with a TEST process only."""
import json
import pytest
from feature_rl import contracts as c
from feature_rl.artifacts import canonical_json
from feature_rl.registry import Registry
from feature_rl.pipeline import Factory
from feature_rl.pipeline.authoring_models import AuthoringSettings,AuthoringCall,ResolverInputs
from feature_rl.environments import PreparedEnvironment
from test_checker_authoring import authoring_fixture
from m5_fixtures import task_fixture


def setup(tmp_path,monkeypatch):
    store,proposal,inputs,sources,request,service,runner,generation=authoring_fixture(tmp_path)
    parent=store.get_artifact(task_fixture(store))
    pair=store.get_artifact(parent.source_pair)
    pair=pair.model_copy(update={'baseline':inputs.baseline})
    pair_ref=store.put_artifact(pair)
    recipe=store.get_artifact(inputs.environment)
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
    settings=AuthoringSettings(codex=service.provider._config,m2_revision='a'*40,m4_revision='a'*40,evidence_scope='unit_diagnostic',
        batch=AuthoringBatch(candidates=(pair.candidate,),candidate_caps=caps,batch_caps=caps))
    assert hasattr(Factory,'author'),'Factory actual authoring is missing'
    factory=Factory(store=store,registry=Registry(tmp_path/'registry',store),revision='e'*40,authoring=settings)
    resolver=service.resolver
    call=AuthoringCall(source_pair=pair_ref,environment=PreparedEnvironment(recipe=inputs.environment,policy=policy),
        resolver=ResolverInputs(request=resolver.request,baseline=resolver.baseline,runtime_discovery=resolver.runtime_discovery,
            public_checks=resolver.public_checks,retrieval_policy=resolver.retrieval_policy,request_provenance=resolver.request_provenance),
        generation=generation,inputs=inputs,sources=sources)
    return factory,pair.candidate,call,runner


def test_actual_checker_result_costs_archives_and_idempotent_completion(tmp_path,monkeypatch):
    factory,candidate,call,runner=setup(tmp_path,monkeypatch)
    result=factory.author(candidate,call=call)
    assert result.disposition==c.Disposition.SUCCESS
    assert result.artifacts[0].kind=='VerifierBundle'
    assert len(runner.calls)==1
    from feature_rl.pipeline.authoring import read_authoring_receipt
    receipt=read_authoring_receipt(factory.store,result.artifacts[-1])
    ledger=factory.registry.accounting(receipt.claim.job_id)
    assert any(o.observation.source=='m6-generation' and any(r.kind=='generation-status' for r in o.observation.receipts) for o in ledger.observations)
    assert any(cost.input_tokens==41 for cost in result.costs)
    assert factory.author(candidate,call=call)==result and len(runner.calls)==1


def test_new_request_ids_do_not_reset_repair_allowance_or_semantic_identity(tmp_path,monkeypatch):
    factory,candidate,call,runner=setup(tmp_path,monkeypatch)
    factory.author(candidate,call=call)
    request=call.generation.request.model_copy(update={'request_id':'OTHER','response_id':'OTHER_RESPONSE','prompt_id':'OTHER_PROMPT'})
    changed=call.model_copy(update={'generation':call.generation.model_copy(update={'request':request})})
    with pytest.raises(ValueError,match='diagnos|meaningful'):factory.author(candidate,call=changed)
    assert len(runner.calls)==1


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


def test_retained_publication_requires_selected_previous_journal_chain(tmp_path,monkeypatch):
    factory,candidate,base,runner=setup(tmp_path,monkeypatch)
    call=contract_call(factory,base,runner);runner.exit_status=1
    initial=factory.author(candidate,call=call)
    assert initial.disposition==c.Disposition.REJECTED
    changed=repaired(call,1);bind_events(runner,changed.generation.request)
    original=factory.store.put_bytes
    def lost(data,kind,visibility):
        if kind=='m6-authoring-receipt':raise OSError('TEST publication before selection')
        return original(data,kind,visibility)
    monkeypatch.setattr(factory.store,'put_bytes',lost)
    from feature_rl.pipeline import FactoryPublicationFailed
    with pytest.raises(FactoryPublicationFailed) as pending:factory.author(candidate,call=changed)
    monkeypatch.setattr(factory.store,'put_bytes',original)
    modified=json.loads(pending.value.payload)
    prior_ref=c.ArtifactRef.model_validate_json(canonical_json(modified['journal_refs'][0]))
    prior=json.loads(factory.store.get_bytes(prior_ref));prior['error']='TEST substituted unrelated prior outcome'
    changed_ref=original(canonical_json(prior),prior_ref.kind,prior_ref.visibility)
    modified['journal_refs'][0]=changed_ref.model_dump(mode='json')
    forged=FactoryPublicationFailed('TEST altered previous journal',pending.value.claim,
        canonical_json(modified),kind='m6-authoring-receipt')
    with pytest.raises(ValueError,match='previous|chain'):factory.retry_publication(forged)
    assert factory.retry_publication(pending.value).disposition==c.Disposition.REJECTED
    assert len(runner.calls)==2


def repaired(call,index):
    request=call.generation.request.model_copy(update={'request_id':'REPAIR_'+str(index),
        'response_id':'RESPONSE_'+str(index),'prompt_id':'PROMPT_'+str(index),
        'instruction':call.generation.request.instruction+'\nTEST diagnosed revision '+str(index)})
    return call.model_copy(update={'generation':call.generation.model_copy(update={'request':request,
        'diagnosis':'TEST exact observed defect','changed_input':'TEST meaningful instruction revision '+str(index)})})


def bind_events(runner,request):
    events=[json.loads(line) for line in runner.stdout.splitlines()]
    for event in events:
        if event['type']=='item.completed' and event['item']['type']=='agent_message':
            envelope=json.loads(event['item']['text']);envelope['response_id']=request.response_id
            event['item']['text']=json.dumps(envelope)
    runner.stdout=('\n'.join(json.dumps(event) for event in events)+'\n').encode()


def test_attempt_limit_is_per_role_and_new_controls_have_their_own_limit(tmp_path,monkeypatch):
    factory,candidate,call,runner=setup(tmp_path,monkeypatch)
    factory.author(candidate,call=call)
    for index in (1,2):
        changed=repaired(call,index);bind_events(runner,changed.generation.request)
        assert factory.author(candidate,call=changed).disposition==c.Disposition.SUCCESS
    with pytest.raises(ValueError,match='three attempts'):
        factory.author(candidate,call=repaired(call,3))
    assert len(runner.calls)==3
    from feature_rl.verifiers import ControlFinalizationInputs,ControlProposal,SourceChange,TextReplacement,build_control_request
    from feature_rl.requirements import AuthoringEvidenceResolver,GenerationCandidate
    from test_checker_authoring import configured_diagnostic_provider
    inputs=ControlFinalizationInputs(control_id='PARTIAL',category='partial',requirement_ids=('echo',),
        expected_reason='TEST ONLY unverified partial implementation',baseline=call.inputs.baseline,
        contract=call.inputs.contract,environment=call.inputs.environment,provenance=call.inputs.provenance,costs=call.inputs.costs)
    resolver=AuthoringEvidenceResolver(store=factory.store,**call.resolver.model_dump())
    request=build_control_request(request_id='CONTROL',response_id='CONTROL_RESPONSE',prompt_id='CONTROL_PROMPT',
        store=factory.store,resolver=resolver,inputs=inputs,sources=call.sources,limits=call.generation.request.limits)
    proposal=ControlProposal(files=(SourceChange(path='src/click/__init__.py',replacements=(TextReplacement(before='# diagnostic', after='# TEST control only\n'),)),),
        deletions=(),rationale='TEST semantic validity unknown')
    _,prepared_runner=configured_diagnostic_provider(factory.store,request,proposal)
    runner.stdout=prepared_runner.stdout
    control=call.model_copy(update={'inputs':inputs,'generation':GenerationCandidate(request=request)})
    result=factory.author(candidate,call=control)
    assert result.disposition==c.Disposition.SUCCESS and result.artifacts[0].kind=='m4-control-record'
    assert len(runner.calls)==4


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


def contract_call(factory,base,runner):
    from feature_rl.requirements import ContractFinalizationInputs,RequirementContractProposal,build_contract_request,GenerationCandidate
    from test_checker_authoring import configured_diagnostic_provider
    contract=factory.store.get_artifact(base.inputs.contract)
    from feature_rl.requirements.runtime_discovery import RuntimeDiscoveryObservation
    discovery=RuntimeDiscoveryObservation.model_validate_json(factory.store.get_bytes(base.resolver.runtime_discovery))
    inputs=ContractFinalizationInputs(visible_request=contract.visible_request,
        allowed_requirement_ids=tuple(r.requirement_id for r in contract.requirements+contract.compatibility_obligations),
        entry_points=discovery.entry_points,supported_observables=discovery.supported_observables,
        runtime_discovery=base.resolver.runtime_discovery,allowed_changes=contract.allowed_changes,
        public_checks=contract.public_checks,episode_limits=contract.episode_limits,provenance_label=contract.provenance_label,
        visibility=c.Visibility.AUTHORING,provenance=contract.provenance,costs=contract.costs)
    request=build_contract_request(request_id='CONTRACT',response_id='CONTRACT_RESPONSE',prompt_id='CONTRACT_PROMPT',
        sources=base.sources,allowed_requirement_ids=inputs.allowed_requirement_ids,entry_points=inputs.entry_points,
        supported_observables=inputs.supported_observables,allowed_changes=inputs.allowed_changes,
        limits=base.generation.request.limits)
    proposed={name:contract.model_dump(mode='json')[name] for name in RequirementContractProposal.model_fields}
    proposed['entry_points']=list(discovery.entry_points)
    for requirement in proposed['requirements']+proposed['compatibility_obligations']:
        requirement['observable']='combined terminal output'
    proposal=RequirementContractProposal.model_validate_json(canonical_json(proposed))
    _,prepared=configured_diagnostic_provider(factory.store,request,proposal)
    runner.stdout=prepared.stdout
    return base.model_copy(update={'inputs':inputs,'generation':GenerationCandidate(request=request)})


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
    with pytest.raises(ValueError,match='frontier|budget'):factory.author(candidate,call=repaired(call,1))


def test_authoring_contracts_have_no_removed_control_or_repair_configuration():
    assert 'control_plan' not in AuthoringCall.model_fields
    assert 'attack' not in AuthoringCall.model_fields
    assert 'semantic_repair_authorization' not in AuthoringSettings.model_fields
    assert not hasattr(Factory,'assemble_checker')
    assert not hasattr(Factory,'import_rejected_authoring')
