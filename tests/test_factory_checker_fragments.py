"""Real Factory persistence/assembly with explicit diagnostic Codex transport."""
import pytest

from feature_rl import contracts as c
from feature_rl.artifacts import canonical_json
from feature_rl.pipeline import Factory
from feature_rl.requirements import GenerationCandidate
from test_factory_authoring import setup


def fragment(factory, base, runner, scenario_id):
    from codex_fixtures import events, response
    from feature_rl.verifiers.fragments import CheckerFragmentInputs, build_fragment_request
    inputs = CheckerFragmentInputs(**base.inputs.model_dump(include={
        'contract', 'scenario_plan', 'baseline', 'environment', 'visibility', 'provenance', 'costs'}),
        scenario_id=scenario_id)
    request = build_fragment_request(request_id='fragment-' + scenario_id,
        response_id='response-' + scenario_id, prompt_id='prompt-' + scenario_id,
        contract=factory.store.get_artifact(inputs.contract), contract_ref=inputs.contract,
        plan=factory.store.get_artifact(inputs.scenario_plan), plan_ref=inputs.scenario_plan,
        scenario_id=scenario_id, sources=base.sources, limits=base.generation.request.limits)
    proposal = {'cases': [{'actions': "return {'output': inputs['word']}\n",
        'inputs': [{'name': 'word', 'domain': {'kind': 'choice', 'values': ['red', 'blue']}}],
        'observations': [{'name': 'output', 'type': 'string'}],
        'assertions': [{'requirement_ids': ['echo'], 'actual': 'output', 'operator': 'equal',
            'expected': {'kind': 'input', 'name': 'word', 'prefix': '', 'suffix': ''}}]}]}
    runner.stdout = events(response(request, proposal))
    call = base.model_copy(update={'inputs': inputs, 'generation': GenerationCandidate(request=request)})
    result = factory.author(candidate=factory.store.get_artifact(base.source_pair).candidate, call=call)
    assert result.disposition == c.Disposition.SUCCESS
    assert factory.author(factory.store.get_artifact(base.source_pair).candidate, call=call) == result
    return result.artifacts[0]


def test_factory_assembles_authenticated_fragments_without_another_model_call(tmp_path, monkeypatch):
    factory, candidate, base, runner = setup(tmp_path, monkeypatch)
    assert hasattr(Factory, 'assemble_checker'), 'Factory lacks deterministic checker assembly'
    refs = tuple(fragment(factory, base, runner, sid) for sid in ('s0', 's1'))
    result = factory.assemble_checker(candidate, inputs=base.inputs, fragments=refs)
    assert result.disposition == c.Disposition.SUCCESS
    assert len(runner.calls) == 2
    verifier = factory.store.get_artifact(result.artifacts[0])
    assert len(verifier.cases) == 2
    assert {case.requirement_ids for case in verifier.cases} == {('echo',)}
    assert all(case.mandatory for case in verifier.cases)
    assert set(refs) <= set(verifier.provenance.inputs)
    assert factory.assemble_checker(candidate, inputs=base.inputs, fragments=refs) == result
    from feature_rl.pipeline.checker import selected_assembly
    assert selected_assembly(factory, candidate, result.artifacts[0], set(refs)) is not None
    assert len(runner.calls) == 2


def test_assembly_rejects_missing_duplicate_and_unattributed_fragments(tmp_path, monkeypatch):
    factory, candidate, base, runner = setup(tmp_path, monkeypatch)
    assert hasattr(Factory, 'assemble_checker'), 'Factory lacks deterministic checker assembly'
    ref = fragment(factory, base, runner, 's0')
    with pytest.raises(ValueError, match='scenario|coverage'):
        factory.assemble_checker(candidate, inputs=base.inputs, fragments=(ref,))
    with pytest.raises(ValueError, match='duplicate|distinct'):
        factory.assemble_checker(candidate, inputs=base.inputs, fragments=(ref, ref))
    from feature_rl.verifiers.fragments import CheckerFragmentRecord
    record = CheckerFragmentRecord.model_validate_json(factory.store.get_bytes(ref))
    forged = record.model_copy(update={'scenario_id': 's1'})
    unbound = factory.store.put_bytes(canonical_json(forged.model_dump(mode='json')),
        'm4-checker-fragment', c.Visibility.PRIVATE)
    with pytest.raises(ValueError, match='author|producer|selected'):
        factory.assemble_checker(candidate, inputs=base.inputs, fragments=(ref, unbound))
    assert len(runner.calls) == 1


def test_assembly_recovers_publication_without_reauthoring(tmp_path, monkeypatch):
    factory, candidate, base, runner = setup(tmp_path, monkeypatch)
    assert hasattr(Factory, 'assemble_checker'), 'Factory lacks deterministic checker assembly'
    refs = tuple(fragment(factory, base, runner, sid) for sid in ('s0', 's1'))
    original = factory.registry.complete
    def lost(claim, result, **kwargs):
        if factory.registry.job(claim.job_id).spec.invocation == 'm6-assemble-checker':
            raise OSError('diagnostic assembly completion outage')
        return original(claim, result, **kwargs)
    monkeypatch.setattr(factory.registry, 'complete', lost)
    from feature_rl.pipeline import FactoryRecoveryRequired
    with pytest.raises(FactoryRecoveryRequired) as pending:
        factory.assemble_checker(candidate, inputs=base.inputs, fragments=refs)
    monkeypatch.setattr(factory.registry, 'complete', original)
    result = factory.recover(pending.value.claim)
    assert result.disposition == c.Disposition.SUCCESS
    assert factory.assemble_checker(candidate, inputs=base.inputs, fragments=refs) == result
    assert len(runner.calls) == 2
