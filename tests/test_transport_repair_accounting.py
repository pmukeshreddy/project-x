"""Append-only semantic accounting; actual provider/Registry with a test transport."""
from copy import deepcopy
import json

import pytest

from feature_rl import contracts as c
from feature_rl.artifacts import canonical_json
from feature_rl.pipeline import authoring as a
from feature_rl.pipeline.authoring_history import selected_history
from feature_rl.pipeline.construction import ConstructionRequest, put, references
from feature_rl.pipeline.models import BuildInputs
from feature_rl.pipeline.packaging import document, read_record
from feature_rl.pipeline.repair_accounting import (PROOF_KIND, TransportRepairProof,
    remove_unsafe_integer_bounds, transport_failure_proof)
from feature_rl.qualification import RepairHistory, validate_repairs
from feature_rl.qualification.models import QualificationRejected
from feature_rl.requirements import GenerationCandidate
from feature_rl.verifiers import build_checker_request
from codex_fixtures import events, response
from test_authoring_transport_recovery import fragment_call, failed_events, PROPOSAL
from test_factory_authoring import setup, repaired, contract_call, scenario_call
from test_factory_checker_fragments import fragment


def test_all_physical_repairs_remain_but_only_semantic_repair_consumes_allowance(tmp_path, monkeypatch):
    factory, candidate, base, runner = setup(tmp_path, monkeypatch)
    checker_proposal = next(json.loads(row['item']['text'])['content']
        for row in map(json.loads, runner.stdout.splitlines()) if row['type'] == 'item.completed')
    contract = factory.author(candidate, call=contract_call(factory, base, runner)).artifacts[0]
    current = base.model_copy(update={'inputs': base.inputs.model_copy(update={'contract': contract})})
    plan = factory.author(candidate, call=scenario_call(factory, current, runner)).artifacts[0]
    inputs = current.inputs.model_copy(update={'scenario_plan': plan,
        'provenance': current.inputs.provenance.model_copy(update={
            'inputs': (*current.inputs.provenance.inputs, contract, plan)})})
    request = build_checker_request(request_id='OLD_CHECKER', response_id='OLD_RESPONSE', prompt_id='OLD_PROMPT',
        contract=factory.store.get_artifact(contract), contract_ref=contract,
        plan=factory.store.get_artifact(plan), plan_ref=plan, sources=base.sources, limits=base.generation.request.limits)
    current = current.model_copy(update={'inputs': inputs, 'generation': GenerationCandidate(request=request)})
    runner.stdout = events(response(request, checker_proposal))
    assert factory.author(candidate, call=current).disposition == c.Disposition.SUCCESS
    import feature_rl.generation.provider as provider
    remote_schema = provider.codex_request_schema
    policy_document = a.policy_document
    def legacy_schema(request, schema):
        result = remote_schema(request, schema)
        result['$defs']['IntegerDomain']['properties']['low']['minimum'] = -(2**63)
        result['$defs']['IntegerDomain']['properties']['high']['maximum'] = 2**63-1
        return result
    old_results = []
    with monkeypatch.context() as old:
        old.setattr(provider, 'codex_request_schema', legacy_schema)
        old.setattr(a, 'policy_document', lambda revision, configured, **kw:
            policy_document(revision, configured, version=kw.get('version', 'm6-authoring-policy-v1')))
        runner.stdout, runner.exit_status = failed_events(), 1
        for index in (1, 2):
            result = factory.author(candidate, call=repaired(current, index))
            assert result.disposition == c.Disposition.REJECTED
            old_results.append(result)
    original = {result.artifacts[-1]: factory.store.get_bytes(result.artifacts[-1]) for result in old_results}
    bad_call = fragment_call(factory, current)
    bad_proposal = deepcopy(PROPOSAL)
    bad_proposal['cases'][0]['assertions'][0]['expected'] = {'kind': 'literal', 'value': True}
    runner.stdout, runner.exit_status = events(response(bad_call.generation.request, bad_proposal)), 0
    failed_semantic = factory.author(candidate, call=bad_call)
    assert failed_semantic.disposition == c.Disposition.REJECTED
    bad_job,bad_author=next((job,author) for job,author in a.jobs(factory,candidate) if job.result==failed_semantic)
    assert transport_failure_proof(factory,bad_job,bad_author) is None
    repaired_call = repaired(bad_call, 3)
    runner.stdout = events(response(repaired_call.generation.request, PROPOSAL))
    result = factory.author(candidate, call=repaired_call)
    assert result.disposition == c.Disposition.SUCCESS
    refs = (result.artifacts[0], fragment(factory, current, runner, 's1'))
    verifier = factory.assemble_checker(candidate, inputs=inputs, fragments=refs).artifacts[0]
    build = BuildInputs(source_pair=current.source_pair, contract=contract, scenario_plan=plan,
        verifier=verifier, environment=current.environment, baseline_files=('src/click/__init__.py',), invocation='TEST_ACCOUNTING')
    construction = ConstructionRequest(candidate=candidate, source=factory.screen_source(candidate).artifacts[0],
        inputs=build, builder_job=None)
    history = read_record(factory.store, selected_history(factory, construction), RepairHistory, 'm5-repair-history')
    proofs = tuple(ref for ref in history.journal_refs if ref.kind == PROOF_KIND)
    assert history.complete and len(proofs) == 2 and len(history.attempts) == 3
    assert sum(author.repair for _, author in a.jobs(factory, candidate)) == 3
    assert validate_repairs(history, candidate, (), store=factory.store, registry=factory.registry) == 1
    assert history.attempts[1].before == history.attempts[0].after
    assert history.attempts[2].before == history.attempts[1].after
    assert all(factory.store.get_bytes(ref) == raw for ref, raw in original.items())
    assert len(runner.calls) == 8
    assert failed_semantic.artifacts[-1] in history.journal_refs
    for result in old_results:
        receipt = a.read_authoring_receipt(factory.store, result.artifacts[-1])
        attempt = next(item for item in history.attempts if item.after == result.artifacts[-1])
        assert receipt.repair and attempt.costs == receipt.costs
    with pytest.raises(QualificationRejected, match='authentication|context'):
        validate_repairs(history, candidate, ())
    with pytest.raises(QualificationRejected, match='authentication|context'):
        validate_repairs(history.model_copy(update={'complete': False}), candidate, ())
    missing = history.model_copy(update={'journal_refs': tuple(ref for ref in history.journal_refs if ref not in proofs)})
    with pytest.raises(QualificationRejected, match='repairs'):
        validate_repairs(missing, candidate, (), store=factory.store, registry=factory.registry)
    proof = read_record(factory.store, proofs[0], TransportRepairProof, PROOF_KIND)
    forged = proof.model_copy(update={'receipt': failed_semantic.artifacts[-1]})
    forged_ref = put(factory, forged, PROOF_KIND, dependencies=references(document(forged)))
    forged_history = history.model_copy(update={'journal_refs': tuple(
        forged_ref if ref == proofs[0] else ref for ref in history.journal_refs)})
    with pytest.raises(QualificationRejected, match='proof|classification|transport'):
        validate_repairs(forged_history, candidate, (), store=factory.store, registry=factory.registry)
    unavailable=history.model_copy(update={'journal_refs': tuple(
        ref.model_copy(update={'sha256':'0'*64}) if ref==proofs[0] else ref for ref in history.journal_refs)})
    with pytest.raises(QualificationRejected,match='authentication'):
        validate_repairs(unavailable,candidate,(),store=factory.store,registry=factory.registry)


def test_policy_versions_are_explicit_and_historical_v1_is_exact(tmp_path, monkeypatch):
    from types import SimpleNamespace
    factory, candidate, call, runner = setup(tmp_path, monkeypatch)
    assert a.policy_document(factory.revision, factory.authoring)['version'] == 'm6-authoring-policy-v2'
    legacy = a.policy_document(factory.revision, factory.authoring, version='m6-authoring-policy-v1')
    assert 'repair_accounting' not in legacy
    ref=factory.store.put_bytes(canonical_json(legacy),'m6-authoring-policy',c.Visibility.PRIVATE)
    job=SimpleNamespace(spec=SimpleNamespace(configuration=ref,implementation=factory.revision))
    assert a.historical_settings(factory,job)==factory.authoring
    legacy['repair_accounting']={'pretend':'legacy exception'}
    ref=factory.store.put_bytes(canonical_json(legacy),'m6-authoring-policy',c.Visibility.PRIVATE)
    with pytest.raises(ValueError,match='historical authoring policy'):
        a.historical_settings(factory,SimpleNamespace(spec=SimpleNamespace(configuration=ref,implementation=factory.revision)))
    with pytest.raises(ValueError):
        a.policy_document(factory.revision, factory.authoring, version='unknown')


def test_numeric_correction_preserves_required_order_and_every_other_constraint():
    original={'type':'object','required':['z','a'],'properties':{
        'a':{'type':'integer','minimum':-(2**63),'maximum':1024},
        'z':{'type':'string','minLength':2**54}}}
    assert remove_unsafe_integer_bounds(original)=={
        'type':'object','required':['z','a'],'properties':{
        'a':{'type':'integer','maximum':1024},'z':{'type':'string','minLength':2**54}}}
    assert original['properties']['a']['minimum']==-(2**63)


def test_classification_pointing_at_one_input_source_job_fails_closed(tmp_path, monkeypatch):
    from feature_rl.pipeline.factory import read_source_disposition
    from feature_rl.qualification import RepairAttempt
    from test_factory_authoring import bind_events
    factory,candidate,base,runner=setup(tmp_path,monkeypatch)
    factory.author(candidate,call=base)
    changed=repaired(base,1);bind_events(runner,changed.generation.request)
    result=factory.author(candidate,call=changed)
    receipt=a.read_authoring_receipt(factory.store,result.artifacts[-1])
    source=read_source_disposition(factory.store,factory.screen_source(candidate).artifacts[0])
    source_job=factory.registry.job(source.claim.job_id)
    assert len(source_job.spec.inputs)==1
    proof=TransportRepairProof(candidate=candidate,job_id=source_job.job_id,request=receipt.request,
        receipt=result.artifacts[-1],policy=source_job.spec.configuration,archives=(),
        original_transport_sha256='0'*64,corrected_transport_sha256='1'*64)
    proof_ref=put(factory,proof,PROOF_KIND,dependencies=references(document(proof)))
    history=RepairHistory(candidate=candidate,complete=True,initial_evidence=result.evidence,
        attempts=(RepairAttempt(stage='verifier',before=base.inputs.contract,after=result.artifacts[-1],
            diagnosis=changed.generation.diagnosis,change=changed.generation.changed_input,
            evidence=result.evidence,costs=receipt.costs),),
        journal_refs=(proof_ref,receipt.request,result.artifacts[-1]))
    with pytest.raises(QualificationRejected,match='classification'):
        validate_repairs(history,candidate,(),store=factory.store,registry=factory.registry)
