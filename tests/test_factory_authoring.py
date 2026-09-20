"""Actual M2/M4 provider/parser/finalizer diagnostics with a TEST process only."""
import json
from pathlib import Path
import pytest
from feature_rl import contracts as c
from feature_rl.artifacts import canonical_json
from feature_rl.registry import Registry
from feature_rl.pipeline import Factory
from feature_rl.pipeline.authoring_models import AuthoringSettings,AuthoringCall,ResolverInputs
from feature_rl.generation.backend import BackendConfig
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
    from feature_rl.generation.provider import LocalGenerationProvider
    actual_init=LocalGenerationProvider.__init__
    # The actual provider/archive/parser still runs. Only its backend verification
    # and subprocess outcome are TEST doubles, matching the focused M4 diagnostics.
    backend=service.provider._backend
    def init(provider,*,backend,archive,runner=None):
        return actual_init(provider,backend=service.provider._backend,archive=archive,runner=service.provider._runner)
    monkeypatch.setattr(LocalGenerationProvider,'__init__',init)
    from feature_rl.pipeline.authoring_models import AuthoringBatch,AuthoringCaps
    caps=AuthoringCaps(input_tokens=20_000_000,output_tokens=20_000_000,wall_seconds=100_000.0,
        cpu_seconds=100_000.0,commands=1000,memory_bytes=5_368_709_120,spend_usd=None)
    settings=AuthoringSettings(backend=BackendConfig(python_executable=Path('/TEST/python'),
        model_directory=Path('/TEST/model'),model_manifest=Path('/TEST/model.json'),
        dependency_manifest=Path('/TEST/deps.json')),m2_revision='a'*40,m4_revision='a'*40,evidence_scope='unit_diagnostic',
        batch=AuthoringBatch(candidates=(pair.candidate,),candidate_caps=caps,batch_caps=caps,
            calibration_evidence=(inputs.environment,)))
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
        event.update(request_id=request.request_id,response_id=request.response_id,prompt_id=request.prompt_id)
        if event['event']=='identity_validated':event['seed']=request.seed
        if event['event']=='completed':
            envelope=json.loads(event['output_text']);envelope['response_id']=request.response_id
            event['output_text']=json.dumps(envelope)
    runner.stdout=('\n'.join(json.dumps(event) for event in events)+'\n').encode()


def test_two_verifier_repairs_are_shared_across_checker_and_control_roles(tmp_path,monkeypatch):
    factory,candidate,call,runner=setup(tmp_path,monkeypatch)
    factory.author(candidate,call=call)
    for index in (1,2):
        changed=repaired(call,index);bind_events(runner,changed.generation.request)
        assert factory.author(candidate,call=changed).disposition==c.Disposition.SUCCESS
    changed=repaired(call,3)
    with pytest.raises(ValueError,match='budget'):factory.author(candidate,call=changed)
    assert len(runner.calls)==3
    from feature_rl.pipeline.authoring_models import ControlPlan,ControlSlot
    from feature_rl.verifiers import ControlFinalizationInputs,ControlProposal,SourceEdit,build_control_request
    from feature_rl.requirements import AuthoringEvidenceResolver,GenerationCandidate
    from test_checker_authoring import configured_diagnostic_provider
    inputs=ControlFinalizationInputs(control_id='FIRST_NAME',category='omission',requirement_ids=('echo',),
        expected_valid=False,expected_reason='TEST ONLY unverified omission',baseline=call.inputs.baseline,
        contract=call.inputs.contract,environment=call.inputs.environment,provenance=call.inputs.provenance,costs=call.inputs.costs)
    resolver=AuthoringEvidenceResolver(store=factory.store,**call.resolver.model_dump())
    request=build_control_request(request_id='CONTROL',response_id='CONTROL_RESPONSE',prompt_id='CONTROL_PROMPT',
        store=factory.store,resolver=resolver,inputs=inputs,sources=call.sources,limits=call.generation.request.limits,seed=0)
    proposal=ControlProposal(files=(SourceEdit(path='src/click/__init__.py',source='# TEST control only\n'),),
        deletions=(),rationale='TEST semantic validity unknown')
    _,prepared_runner=configured_diagnostic_provider(factory.store,request,proposal)
    runner.stdout=prepared_runner.stdout
    control=call.model_copy(update={'inputs':inputs,'generation':GenerationCandidate(request=request),
        'control_plan':ControlPlan(contract=inputs.contract,slots=(ControlSlot(category='omission',requirement_ids=('echo',)),))})
    result=factory.author(candidate,call=control)
    assert result.disposition==c.Disposition.SUCCESS and result.artifacts[0].kind=='m4-control-record'
    changed=repaired(control,4)
    changed=changed.model_copy(update={'inputs':inputs.model_copy(update={'control_id':'NEW_NAME'})})
    with pytest.raises(ValueError,match='budget'):factory.author(candidate,call=changed)
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
    from feature_rl.requirements.discovery import ClickDiscoveryObservation
    discovery=ClickDiscoveryObservation.model_validate_json(factory.store.get_bytes(base.resolver.runtime_discovery))
    inputs=ContractFinalizationInputs(visible_request=contract.visible_request,
        allowed_requirement_ids=tuple(r.requirement_id for r in contract.requirements+contract.compatibility_obligations),
        entry_points=discovery.entry_points,supported_observables=discovery.supported_observables,
        runtime_discovery=base.resolver.runtime_discovery,allowed_changes=contract.allowed_changes,
        public_checks=contract.public_checks,episode_limits=contract.episode_limits,provenance_label=contract.provenance_label,
        visibility=c.Visibility.AUTHORING,provenance=contract.provenance,costs=contract.costs)
    request=build_contract_request(request_id='CONTRACT',response_id='CONTRACT_RESPONSE',prompt_id='CONTRACT_PROMPT',
        sources=base.sources,allowed_requirement_ids=inputs.allowed_requirement_ids,entry_points=inputs.entry_points,
        supported_observables=inputs.supported_observables,allowed_changes=inputs.allowed_changes,
        limits=base.generation.request.limits,seed=0)
    proposed={name:contract.model_dump(mode='json')[name] for name in RequirementContractProposal.model_fields}
    proposed['entry_points']=list(discovery.entry_points)
    for requirement in proposed['requirements']+proposed['compatibility_obligations']:
        requirement['observable']='combined terminal output'
    proposal=RequirementContractProposal.model_validate_json(canonical_json(proposed))
    _,prepared=configured_diagnostic_provider(factory.store,request,proposal)
    runner.stdout=prepared.stdout
    return base.model_copy(update={'inputs':inputs,'generation':GenerationCandidate(request=request)})


def test_import_retained_rejected_journal_costs_without_another_provider_call(tmp_path,monkeypatch):
    factory,candidate,base,runner=setup(tmp_path,monkeypatch)
    call=contract_call(factory,base,runner);runner.exit_status=1
    from feature_rl.requirements import ContractAuthoringService,AuthoringEvidenceResolver,AuthoringExhausted
    from feature_rl.generation import LocalGenerationProvider
    provider=LocalGenerationProvider(backend=factory.authoring.backend,archive=factory.store.put_bytes)
    resolver=AuthoringEvidenceResolver(store=factory.store,**call.resolver.model_dump())
    service=ContractAuthoringService(provider=provider,store=factory.store,resolver=resolver,revision='a'*40,evidence_scope='unit_diagnostic')
    with pytest.raises(AuthoringExhausted) as prior:service.generate((call.generation,),call.inputs,call.sources)
    assert len(runner.calls)==1
    assert hasattr(factory,'import_rejected_authoring'),'retained M2 journal importer is missing'
    journal=prior.value.journal_refs[0]
    result=factory.import_rejected_authoring(candidate,call=call,journal_refs=(journal,))
    assert result.disposition==c.Disposition.REJECTED and len(runner.calls)==1
    assert factory.import_rejected_authoring(candidate,call=call,journal_refs=(journal,))==result
    assert len(runner.calls)==1


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


def test_retained_import_rejects_context_substitution(tmp_path,monkeypatch):
    factory,candidate,base,runner=setup(tmp_path,monkeypatch)
    call=contract_call(factory,base,runner);runner.exit_status=1
    from feature_rl.requirements import ContractAuthoringService,AuthoringEvidenceResolver,AuthoringExhausted
    from feature_rl.generation import LocalGenerationProvider
    service=ContractAuthoringService(provider=LocalGenerationProvider(backend=factory.authoring.backend,archive=factory.store.put_bytes),
        store=factory.store,resolver=AuthoringEvidenceResolver(store=factory.store,**call.resolver.model_dump()),revision='a'*40,evidence_scope='unit_diagnostic')
    with pytest.raises(AuthoringExhausted) as prior:service.generate((call.generation,),call.inputs,call.sources)
    changed=call.model_copy(update={'sources':(call.sources[0].model_copy(update={'context_id':'DIFFERENT_CONTEXT'}),*call.sources[1:])})
    with pytest.raises(ValueError,match='context'):factory.import_rejected_authoring(candidate,call=changed,journal_refs=prior.value.journal_refs)
    assert len(runner.calls)==1


def scenario_call(factory,base,runner):
    from feature_rl.scenarios import ScenarioFinalizationInputs,ScenarioPlanProposal,build_scenario_request
    from feature_rl.requirements import GenerationCandidate
    from test_checker_authoring import configured_diagnostic_provider
    plan=factory.store.get_artifact(base.inputs.scenario_plan)
    contract=factory.store.get_artifact(base.inputs.contract)
    observables=tuple(dict.fromkeys(r.observable for r in contract.requirements+contract.compatibility_obligations))
    inputs=ScenarioFinalizationInputs(contract=base.inputs.contract,supported_observables=observables,
        seed_policy=plan.seed_policy,visibility=c.Visibility.PRIVATE,
        provenance=plan.provenance.model_copy(update={'inputs':tuple(dict.fromkeys((*plan.provenance.inputs,base.inputs.contract)))}),costs=plan.costs)
    request=build_scenario_request(request_id='SCENARIOS',response_id='SCENARIOS_RESPONSE',prompt_id='SCENARIOS_PROMPT',
        contract=factory.store.get_artifact(inputs.contract),contract_ref=inputs.contract,sources=base.sources,
        limits=base.generation.request.limits,seed=0)
    proposed=plan.model_dump(mode='json')['scenarios']
    for scenario in proposed:scenario['observations']=list(observables)
    proposal=ScenarioPlanProposal.model_validate_json(canonical_json({'scenarios':proposed}))
    _,prepared=configured_diagnostic_provider(factory.store,request,proposal);runner.stdout=prepared.stdout
    return base.model_copy(update={'inputs':inputs,'generation':GenerationCandidate(request=request)})


def test_actual_scenario_service_uses_exact_frozen_contract(tmp_path,monkeypatch):
    factory,candidate,base,runner=setup(tmp_path,monkeypatch)
    call=scenario_call(factory,base,runner)
    result=factory.author(candidate,call=call)
    assert result.disposition==c.Disposition.SUCCESS and result.artifacts[0].kind=='ScenarioPlan'
    assert factory.store.get_artifact(result.artifacts[0]).contract==call.inputs.contract
    assert len(runner.calls)==1


def test_complete_history_uses_actual_terminal_contract_scenario_checker_outputs(tmp_path,monkeypatch):
    factory,candidate,base,runner=setup(tmp_path,monkeypatch)
    checker_proposal=next(json.loads(event['output_text'])['content'] for event in
        (json.loads(line) for line in runner.stdout.splitlines()) if event['event']=='completed')
    contract=factory.author(candidate,call=contract_call(factory,base,runner)).artifacts[0]
    current=base.model_copy(update={'inputs':base.inputs.model_copy(update={'contract':contract})})
    scenarios=factory.author(candidate,call=scenario_call(factory,current,runner)).artifacts[0]
    from feature_rl.verifiers import build_checker_request,CheckerProposal,ControlFinalizationInputs,ControlProposal,SourceEdit,build_control_request
    from feature_rl.requirements import GenerationCandidate,AuthoringEvidenceResolver
    from test_checker_authoring import configured_diagnostic_provider
    from feature_rl.pipeline.authoring_models import ControlPlan,ControlSlot
    control_inputs=ControlFinalizationInputs(control_id='TEST_OMISSION',category='omission',requirement_ids=('echo',),
        expected_valid=False,expected_reason='TEST unverified missing implementation',baseline=base.inputs.baseline,
        contract=contract,environment=base.inputs.environment,
        provenance=base.inputs.provenance.model_copy(update={'inputs':(*base.inputs.provenance.inputs,contract)}),costs=base.inputs.costs)
    control_request=build_control_request(request_id='HISTORY_CONTROL',response_id='HISTORY_CONTROL_RESPONSE',prompt_id='HISTORY_CONTROL_PROMPT',
        store=factory.store,resolver=AuthoringEvidenceResolver(store=factory.store,**base.resolver.model_dump()),
        inputs=control_inputs,sources=base.sources,limits=base.generation.request.limits,seed=0)
    control_proposal=ControlProposal(files=(SourceEdit(path='src/click/__init__.py',source='# TEST omission control\n'),),
        deletions=(),rationale='TEST only, not qualified semantic evidence')
    _,prepared=configured_diagnostic_provider(factory.store,control_request,control_proposal);runner.stdout=prepared.stdout
    control_call=base.model_copy(update={'inputs':control_inputs,'generation':GenerationCandidate(request=control_request),
        'control_plan':ControlPlan(contract=contract,slots=(ControlSlot(category='omission',requirement_ids=('echo',)),))})
    control_ref=factory.author(candidate,call=control_call).artifacts[0]
    from feature_rl.pipeline.packaging import read_record
    from feature_rl.verifiers.control_authoring import ControlRecord
    control=read_record(factory.store,control_ref,ControlRecord,'m4-control-record')
    inputs=current.inputs.model_copy(update={'scenario_plan':scenarios,
        'controls':(control.control,),
        'provenance':current.inputs.provenance.model_copy(update={'inputs':(*current.inputs.provenance.inputs,contract,scenarios)})})
    request=build_checker_request(request_id='FINAL_CHECKER',response_id='FINAL_CHECKER_RESPONSE',prompt_id='FINAL_CHECKER_PROMPT',
        contract=factory.store.get_artifact(contract),contract_ref=contract,plan=factory.store.get_artifact(scenarios),
        plan_ref=scenarios,sources=base.sources,limits=base.generation.request.limits,seed=0)
    _,prepared=configured_diagnostic_provider(factory.store,request,CheckerProposal.model_validate_json(canonical_json(checker_proposal)))
    runner.stdout=prepared.stdout
    current=current.model_copy(update={'inputs':inputs,'generation':GenerationCandidate(request=request)})
    verifier=factory.author(candidate,call=current).artifacts[0]
    from feature_rl.pipeline.models import BuildInputs
    from feature_rl.pipeline.construction import ConstructionRequest
    from feature_rl.pipeline.authoring_history import selected_history
    from feature_rl.pipeline.packaging import read_record
    from feature_rl.qualification import RepairHistory,validate_repairs
    build=BuildInputs(source_pair=base.source_pair,contract=contract,scenario_plan=scenarios,verifier=verifier,
        environment=base.environment,baseline_files=('src/click/__init__.py',),invocation='TEST_COMPLETE_HISTORY')
    request=ConstructionRequest(candidate=candidate,source=factory.screen_source(candidate).artifacts[0],inputs=build,builder_job=None)
    history=read_record(factory.store,selected_history(factory,request),RepairHistory,'m5-repair-history')
    assert history.complete and validate_repairs(history,candidate,())==0
    assert len(runner.calls)==4
    assert factory.store.get_artifact(verifier).controls==(control.control,)
    from feature_rl.pipeline.authoring import jobs
    assert control_ref in {ref for job,_ in jobs(factory,candidate)
        for ref in job.result.artifacts}


def test_four_candidate_repairs_and_frozen_incomplete_history_across_actual_stages(tmp_path,monkeypatch):
    factory,candidate,checker,runner=setup(tmp_path,monkeypatch)
    checker_events=runner.stdout
    contract=contract_call(factory,checker,runner)
    assert factory.author(candidate,call=contract).disposition==c.Disposition.SUCCESS
    for index in (1,2):
        changed=repaired(contract,index);bind_events(runner,changed.generation.request)
        assert factory.author(candidate,call=changed).disposition==c.Disposition.SUCCESS
    runner.stdout=checker_events
    result=factory.author(candidate,call=checker)
    for index in (1,2):
        changed=repaired(checker,index);bind_events(runner,changed.generation.request)
        result=factory.author(candidate,call=changed)
    from feature_rl.pipeline.models import BuildInputs
    from feature_rl.pipeline.construction import ConstructionRequest,read_construction_request
    from feature_rl.pipeline.authoring_history import selected_history
    from feature_rl.pipeline.packaging import read_record
    from feature_rl.qualification import RepairHistory
    inputs=BuildInputs(source_pair=checker.source_pair,contract=checker.inputs.contract,
        scenario_plan=checker.inputs.scenario_plan,verifier=result.artifacts[0],environment=checker.environment,
        baseline_files=('src/click/__init__.py',),invocation='TEST_HISTORY')
    request=ConstructionRequest(candidate=candidate,source=factory.screen_source(candidate).artifacts[0],inputs=inputs,builder_job=None)
    history_ref=selected_history(factory,request)
    history=read_record(factory.store,history_ref,RepairHistory,'m5-repair-history')
    assert not history.complete  # task contract/plan were externally authored diagnostics
    assert [item.stage for item in history.attempts]==['authoring','authoring','verifier','verifier']
    assert history.attempts[1].before==history.attempts[0].after
    assert history.attempts[3].before==history.attempts[2].after
    assert all(any(cost.input_tokens==41 for cost in item.costs) for item in history.attempts)
    assert selected_history(factory,request)==history_ref
    scenarios=scenario_call(factory,checker,runner)
    assert factory.author(candidate,call=scenarios).disposition==c.Disposition.SUCCESS
    with pytest.raises(ValueError,match='shared repair budget'):factory.author(candidate,call=repaired(scenarios,1))
    assert len(runner.calls)==7
    # V2 freezes this exact history before the actual builder decides whether
    # these diagnostic environment inputs satisfy packaging policy.
    constructed=factory.construct(candidate,inputs=inputs)
    parent=next(factory.registry.job(j) for j in factory.registry.trace(candidate).jobs
        if factory.registry.job(j).spec.invocation=='m6-construct')
    frozen=read_construction_request(factory.store,parent.spec.inputs[2])
    assert frozen.version=='m6-construction-request-v2'
    selected=read_record(factory.store,frozen.history,RepairHistory,'m5-repair-history')
    assert len(selected.attempts)==4 and not selected.complete
    assert factory.construct(candidate,inputs=inputs)==constructed
