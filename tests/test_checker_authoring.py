"""CPU diagnostic proposals/events only; no model, H, or feature qualification."""
from m4_fixtures import runtime_policy
from datetime import datetime, timezone
import json
import pytest
from feature_rl.artifacts import ArtifactStore, canonical_json
from feature_rl import contracts as c
from feature_rl.requirements import AuthoringEvidenceResolver, GroundedSource, RetrievalPolicy
from feature_rl.verifiers import CheckerProposal, CheckerFinalizationInputs, CheckerFinalizer
from m4_fixtures import diagnostic, replace_artifact
from test_authoring import bound_discovery_text


def checker_fixture(tmp_path):
    store = ArtifactStore(tmp_path / 'objects', c.ActorRole.CONTROLLER)
    task = store.get_artifact(diagnostic(store))
    from feature_rl.environments import SandboxPolicy
    policy_ref = store.put_bytes(canonical_json(runtime_policy().model_dump(mode='json')), 'sandbox-policy', c.Visibility.AUTHORING)
    recipe = store.get_artifact(task.environment)
    task = task.model_copy(update={'environment': replace_artifact(store, task.environment, visibility='authoring', provenance=recipe.provenance.model_copy(update={'inputs': recipe.provenance.inputs+(policy_ref,)}))})
    old = store.get_artifact(task.contract)
    request = store.put_bytes(b'DIAGNOSTIC ONLY', 'authoring-request', c.Visibility.AUTHORING)
    discovery_text = bound_discovery_text(baseline=task.baseline, recipe=task.environment)
    discovery = store.put_bytes(discovery_text.encode(), 'runtime-discovery', c.Visibility.AUTHORING)
    sources = (
        GroundedSource(context_id='REQUEST', role='request', source=request, locator='authoring-request:whole', text='DIAGNOSTIC ONLY', provenance_label='reconstructed_specification'),
        GroundedSource(context_id='BASELINE', role='baseline', source=task.baseline, locator='src/click/__init__.py:1-1', text='# diagnostic\n', provenance_label='existing_obligation'),
        GroundedSource(context_id='DISCOVERY', role='baseline', source=discovery, locator='m3:runtime-discovery-v1', text=discovery_text, provenance_label='existing_obligation'),
    )
    provenance = old.provenance.model_copy(update={'inputs': (request, task.baseline, discovery)})
    origin = c.EvidenceLink(source=request, locator='authoring-request:whole', quote='DIAGNOSTIC ONLY', provenance_label='reconstructed_specification')
    requirement = old.requirements[0].model_copy(update={'evidence': (origin,)})
    contract_ref = replace_artifact(store, task.contract, visibility='authoring', provenance=provenance, requirements=[requirement.model_dump(mode='json')])
    old_verifier = store.get_artifact(task.private_oracle)
    old_plan = store.get_artifact(old_verifier.scenario_plan)
    plan_ref = replace_artifact(store, old_verifier.scenario_plan, contract=contract_ref, provenance=provenance.model_copy(update={'inputs': provenance.inputs+(contract_ref,)}), scenarios=[s.model_copy(update={'oracle_origin': origin}).model_dump(mode='json') for s in old_plan.scenarios])
    cases = []
    for case in old_verifier.cases:
        inp = json.loads(store.get_bytes(case.inputs))
        cmp = json.loads(store.get_bytes(case.comparison))
        for assertion in cmp['assertions']: assertion['oracle_origin'] = origin.model_dump(mode='json')
        cases.append({'case_id': case.case_id, 'requirement_ids': list(case.requirement_ids), 'mandatory': case.mandatory, 'inputs': inp, 'comparison': cmp})
    proposal = CheckerProposal.model_validate_json(json.dumps({'worker_adapter': {'source': store.get_bytes(old_verifier.worker_adapter.code).decode(), 'supported_observables': ['json'], 'limitations': ['HAND-AUTHORED DIAGNOSTIC ONLY']}, 'cases': cases}))
    resolver = AuthoringEvidenceResolver(store=store, request=request, baseline=task.baseline, runtime_discovery=discovery, public_checks=(), retrieval_policy=RetrievalPolicy(allowed_paths=('src/click/__init__.py',), max_archive_bytes=1048576,max_files=10,max_selected_bytes=10000,max_spans=10))
    inputs = CheckerFinalizationInputs(contract=contract_ref, scenario_plan=plan_ref, baseline=task.baseline, environment=task.environment, output_limit_bytes=65536, public_examples=(), controls=(), visibility=c.Visibility.PRIVATE, provenance=provenance.model_copy(update={'inputs': provenance.inputs+(contract_ref,plan_ref,task.environment)}), costs=old.costs)
    return store, task, proposal, inputs, sources, resolver


def test_finalizer_freezes_real_m0_and_loads_without_execution(tmp_path):
    store, task, proposal, inputs, sources, resolver = checker_fixture(tmp_path)
    prepared = CheckerFinalizer(store=store, resolver=resolver).prepare(proposal, inputs, sources)
    bundle_ref = prepared.publish(store)
    bundle = store.get_artifact(bundle_ref)
    assert type(bundle) is c.VerifierBundle
    assert bundle.completion_manifest == tuple(p.case_id for p in proposal.cases)
    assert bundle.permissions.worker_inputs == (bundle.worker_adapter.code,)
    from feature_rl.verifiers import load_verifier
    task_ref = replace_artifact(store, store.put_artifact(task), contract=inputs.contract, private_oracle=bundle_ref)
    assert load_verifier(store, task_ref).adapter == proposal.worker_adapter.source.encode()


def configured_diagnostic_provider(store, request, proposal, *, archive=None, termination='process_exit'):
    """Real provider parser/archive/cost pipeline with TEST process/backend doubles."""
    from test_generation import FakeBackend, FakeRunner, worker_events
    from feature_rl.generation import LocalGenerationProvider
    events = [json.loads(line) for line in worker_events().splitlines()]
    for event in events:
        event.update(request_id=request.request_id, response_id=request.response_id, prompt_id=request.prompt_id)
        if event['event'] == 'identity_validated': event['seed'] = request.seed
        if event['event'] == 'completed':
            event['output_text'] = json.dumps({'response_id': request.response_id, 'source_ids': [ctx.context_id for ctx in request.contexts], 'requirement_ids': list(request.allowed_requirement_ids), 'content': proposal.model_dump(mode='json')})
    runner = FakeRunner(stdout=('\n'.join(json.dumps(e) for e in events)+'\n').encode(), termination=termination)
    return LocalGenerationProvider(backend=FakeBackend(), archive=archive or store.put_bytes, runner=runner), runner


def request_for(store, inputs, sources):
    from test_generation import limits
    from feature_rl.verifiers import build_checker_request
    return build_checker_request(request_id='CHECKER_1', response_id='RESPONSE_1', prompt_id='PROMPT_1', contract=store.get_artifact(inputs.contract), contract_ref=inputs.contract, plan=store.get_artifact(inputs.scenario_plan), plan_ref=inputs.scenario_plan, sources=sources, limits=limits(), seed=0)


def authoring_fixture(tmp_path, *, archive=None):
    from feature_rl.verifiers import CheckerAuthoringService
    from feature_rl.requirements import GenerationCandidate
    store, task, proposal, inputs, sources, resolver = checker_fixture(tmp_path)
    request = request_for(store, inputs, sources)
    provider, runner = configured_diagnostic_provider(store, request, proposal, archive=archive)
    service = CheckerAuthoringService(provider=provider, store=store, resolver=resolver, revision='a'*40, evidence_scope='unit_diagnostic')
    return store, proposal, inputs, sources, request, service, runner, GenerationCandidate(request=request)


def test_actual_provider_request_schema_cost_and_private_bundle(tmp_path):
    store, proposal, inputs, sources, request, service, runner, candidate = authoring_fixture(tmp_path)
    result = service.generate((candidate,), inputs, sources)
    assert len(runner.calls) == 1
    assert result.verifier.costs[-1] == result.generation.cost
    assert result.verifier.costs[-1].input_tokens == 41
    assert result.verifier.provenance.evidence[-1].scope == 'unit_diagnostic'
    assert all(ref.visibility is c.Visibility.PRIVATE for ref in result.generation.record.archives.values())
    assert store.get_artifact(result.verifier_ref) == result.verifier
    assert json.loads(store.get_bytes(result.journal_refs[0]))['status'] == 'accepted'
    assert result.verifier.contract == inputs.contract
    assert result.verifier.scenario_plan == inputs.scenario_plan


@pytest.mark.parametrize('defect', ['missing_family', 'unknown_requirement', 'wrong_oracle', 'downgraded', 'operand_type', 'worker_verdict'])
def test_generated_defects_rejected_before_final_publication(tmp_path, defect):
    from pydantic import ValidationError
    store, task, proposal, inputs, sources, resolver = checker_fixture(tmp_path)
    value = proposal.model_dump(mode='json')
    if defect == 'missing_family': value['cases'].pop()
    elif defect == 'unknown_requirement': value['cases'][0]['requirement_ids'] = ['invented']
    elif defect == 'wrong_oracle': value['cases'][0]['comparison']['assertions'][0]['oracle_origin']['quote'] = 'invented'
    elif defect == 'downgraded': value['cases'][0]['mandatory'] = False
    elif defect == 'operand_type': value['cases'][0]['comparison']['assertions'][0]['expected'] = {'kind': 'literal', 'value': True}
    else: value['cases'][0]['comparison']['observations'][0]['name'] = 'reward'
    with pytest.raises(ValueError):
        CheckerFinalizer(store=store, resolver=resolver).prepare(CheckerProposal.model_validate_json(json.dumps(value)), inputs, sources)


@pytest.mark.parametrize('fail_kind', ['checker-authoring-journal', 'm4-worker-adapter', 'm4-case-input', 'm4-case-comparison', 'VerifierBundle'])
def test_every_final_publication_phase_replays_without_generation(tmp_path, monkeypatch, fail_kind):
    from feature_rl.verifiers import CheckerPublicationPending
    store, proposal, inputs, sources, request, service, runner, candidate = authoring_fixture(tmp_path)
    original_bytes, original_artifact = store.put_bytes, store.put_artifact
    def put_bytes(data, kind, visibility):
        if kind == fail_kind: raise OSError('diagnostic publication outage')
        return original_bytes(data, kind, visibility)
    def put_artifact(artifact):
        if artifact.kind == fail_kind: raise OSError('diagnostic publication outage')
        return original_artifact(artifact)
    monkeypatch.setattr(store, 'put_bytes', put_bytes)
    monkeypatch.setattr(store, 'put_artifact', put_artifact)
    with pytest.raises(CheckerPublicationPending) as raised:
        service.generate((candidate,), inputs, sources)
    pending = raised.value
    assert len(runner.calls) == 1
    monkeypatch.setattr(store, 'put_bytes', original_bytes)
    monkeypatch.setattr(store, 'put_artifact', original_artifact)
    result = pending.replay(store)
    repeated = pending.replay(store)
    assert result == repeated
    assert result.verifier == pending.prepared.verifier
    assert result.generation == pending.generation
    assert len(runner.calls) == 1


def test_provider_archive_success_recovery_uses_exact_request_and_cost(tmp_path):
    from feature_rl.generation.provider import GenerationProviderError
    store, proposal, inputs, sources, request, service, runner, candidate = authoring_fixture(tmp_path)
    failed = False
    def archive(data, kind, visibility):
        nonlocal failed
        if kind == 'generation-response' and not failed:
            failed = True
            raise OSError('diagnostic provider archive outage')
        return store.put_bytes(data, kind, visibility)
    provider, runner = configured_diagnostic_provider(store, request, proposal, archive=archive)
    service.provider = provider
    with pytest.raises(GenerationProviderError) as caught:
        service.generate((candidate,), inputs, sources)
    recovered = caught.value.replay_result(store.put_bytes)
    result = service.generate((candidate,), inputs, sources, recovered_result=recovered)
    assert result.generation == recovered and len(runner.calls) == 1
    wrong = candidate.model_copy(update={'request': request.model_copy(update={'seed': 1})})
    with pytest.raises(ValueError):
        service.generate((wrong,), inputs, sources, recovered_result=recovered)
    assert len(runner.calls) == 1


def test_preparation_outage_retains_generated_response_without_dispatch(tmp_path, monkeypatch):
    from feature_rl.verifiers import AuthoringPreparationPending
    import feature_rl.verifiers.finalize as finalizer
    store, proposal, inputs, sources, request, service, runner, candidate = authoring_fixture(tmp_path)
    original = finalizer.TemporaryDirectory
    def outage(**kwargs): raise OSError('diagnostic temporary store outage')
    monkeypatch.setattr(finalizer, 'TemporaryDirectory', outage)
    with pytest.raises(AuthoringPreparationPending) as caught:
        service.generate((candidate,), inputs, sources)
    monkeypatch.setattr(finalizer, 'TemporaryDirectory', original)
    result = caught.value.replay()
    assert result.generation == caught.value.generation
    assert len(runner.calls) == 1


def test_diagnosed_repair_binds_original_inputs_and_retains_rejected_cost(tmp_path):
    from feature_rl.requirements import AuthoringExhausted, GenerationCandidate
    store, proposal, inputs, sources, request, service, runner, candidate = authoring_fixture(tmp_path)
    broken = proposal.model_copy(update={'cases': proposal.cases[:1]})
    service.provider, first = configured_diagnostic_provider(store, request, broken)
    with pytest.raises(AuthoringExhausted) as caught:
        service.generate((candidate,), inputs, sources)
    prior = caught.value.journal_refs
    entry = json.loads(store.get_bytes(prior[0]))
    assert entry['status']=='rejected' and entry['cost']['input_tokens']==41
    unchanged = GenerationCandidate(request=request.model_copy(update={'request_id': 'REPAIR'}), diagnosis='missing family', changed_input='IDs only')
    with pytest.raises(ValueError, match='meaningful'):
        service.generate((unchanged,), inputs, sources, prior_journal_refs=prior)
    request2 = request.model_copy(update={'request_id': 'REPAIR', 'instruction': request.instruction+' Include the omitted second scenario.'})
    repair = GenerationCandidate(request=request2, diagnosis='missing second family', changed_input='explicit second scenario coverage')
    service.provider, second = configured_diagnostic_provider(store, request2, proposal)
    result = service.generate((repair,), inputs, sources, prior_journal_refs=prior)
    assert len(first.calls)==len(second.calls)==1 and len(result.journal_refs)==2
    other_inputs = inputs.model_copy(update={'environment': inputs.environment.model_copy(update={'sha256': '0'*64})})
    with pytest.raises(ValueError):
        service.generate((repair,), other_inputs, sources, prior_journal_refs=prior)


def test_rejected_journal_outage_preserves_cost_and_resumes_with_diagnosis(tmp_path, monkeypatch):
    from feature_rl.requirements import AuthoringJournalPublicationPending, GenerationCandidate
    store, proposal, inputs, sources, request, service, runner, candidate = authoring_fixture(tmp_path)
    broken=proposal.model_copy(update={'cases':proposal.cases[:1]})
    service.provider, runner = configured_diagnostic_provider(store,request,broken)
    original=store.put_bytes
    def failure(data,kind,visibility):
        if kind=='checker-authoring-journal': raise OSError('TEST journal outage')
        return original(data,kind,visibility)
    monkeypatch.setattr(store,'put_bytes',failure)
    with pytest.raises(AuthoringJournalPublicationPending) as caught:
        service.generate((candidate,),inputs,sources)
    monkeypatch.setattr(store,'put_bytes',original)
    refs=caught.value.replay(store)
    assert len(runner.calls)==1 and json.loads(store.get_bytes(refs[-1]))['cost']['input_tokens']==41
    repair_request=request.model_copy(update={'request_id':'REPAIRED','instruction':request.instruction+' Include both scenarios.'})
    service.provider, next_runner=configured_diagnostic_provider(store,repair_request,proposal)
    repair=GenerationCandidate(request=repair_request,diagnosis='missing case family',changed_input='explicit family coverage instruction')
    assert len(service.generate((repair,),inputs,sources,prior_journal_refs=refs).journal_refs)==2
    assert len(next_runner.calls)==1


def test_recovered_failed_provider_attempt_is_not_reexecuted(tmp_path):
    from feature_rl.generation.provider import GenerationProviderError
    from feature_rl.requirements import AuthoringExhausted
    store, proposal, inputs, sources, request, service, runner, candidate = authoring_fixture(tmp_path)
    service.provider, runner=configured_diagnostic_provider(store,request,proposal,termination='wall_timeout')
    with pytest.raises(GenerationProviderError) as caught:
        service.provider.generate(request,CheckerProposal)
    error=caught.value
    with pytest.raises(AuthoringExhausted) as exhausted:
        service.generate((candidate,),inputs,sources,recovered_error=error)
    assert len(runner.calls)==1
    journal=json.loads(store.get_bytes(exhausted.value.journal_refs[0]))
    assert journal['cost']==error.cost.model_dump(mode='json') and journal['generation_record']==error.record.model_dump(mode='json')
