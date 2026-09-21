"""Compact checker diagnostics: inert source only, no model or worker execution."""
import ast
import json

import pytest

from feature_rl.contracts import Visibility
from feature_rl.requirements import AuthoringExhausted, GenerationCandidate
from feature_rl.verifiers import CheckerFinalizer
from feature_rl.verifiers.fragments import (
    CheckerFragmentInputs, CheckerFragmentProposal, CheckerFragmentRecord,
    CheckerFragmentService, FragmentPublicationPending, assemble_checker,
    build_fragment_request,
)
from test_checker_authoring import checker_fixture, configured_diagnostic_provider
from test_generation import limits


ACTIONS = """import click
from click.testing import CliRunner
@click.command()
@click.argument('word')
def command(word):
    click.echo(word)
result = CliRunner().invoke(command, [inputs['word']])
return {'output': result.output, 'exit': result.exit_code}
"""


def fragment_for(case):
    return CheckerFragmentProposal(cases=({
        'actions': ACTIONS,
        'inputs': case.inputs.fields,
        'observations': case.comparison.observations,
        'assertions': tuple(assertion.model_dump(exclude={'assertion_id', 'oracle_origin'})
                            for assertion in case.comparison.assertions),
    },))


def fixture(tmp_path):
    store, task, checker, base, sources, resolver = checker_fixture(tmp_path)
    contract, plan = store.get_artifact(base.contract), store.get_artifact(base.scenario_plan)
    inputs = CheckerFragmentInputs(**base.model_dump(exclude={
        'output_limit_bytes', 'public_examples', 'controls'}), scenario_id='s0')
    proposal = fragment_for(checker.cases[0])
    request = build_fragment_request(request_id='FRAGMENT_1', response_id='RESPONSE_1',
        prompt_id='PROMPT_1', contract=contract, contract_ref=base.contract, plan=plan,
        plan_ref=base.scenario_plan, sources=sources, limits=limits(), scenario_id='s0')
    provider, runner = configured_diagnostic_provider(store, request, proposal)
    service = CheckerFragmentService(provider=provider, store=store, resolver=resolver,
        revision='a'*40, evidence_scope='unit_diagnostic')
    return store, checker, base, inputs, sources, resolver, proposal, request, runner, service


def test_assembly_freezes_joins_oracles_and_keeps_expected_data_private(tmp_path):
    store, checker, base, inputs, sources, resolver, *_ = fixture(tmp_path)
    contract, plan = store.get_artifact(base.contract), store.get_artifact(base.scenario_plan)
    fragments = {case.inputs.scenario_id: fragment_for(case) for case in checker.cases}
    assembled = assemble_checker(fragments, contract, plan, timeout_seconds=30.0)
    assert assembled == assemble_checker(dict(reversed(list(fragments.items()))), contract, plan,
                                        timeout_seconds=30.0)
    assert len(assembled.cases) == 2
    assert len({case.case_id for case in assembled.cases}) == 2
    assert assembled.worker_adapter.supported_observables == ('json',)
    for case, scenario in zip(assembled.cases, plan.scenarios):
        assert case.requirement_ids == case.inputs.requirement_ids == case.comparison.requirement_ids == ('echo',)
        assert case.mandatory and case.comparison.timeout_seconds == 30.0
        assert case.inputs.scenario_id == case.comparison.scenario_id == scenario.scenario_id
        assert all(a.oracle_origin == scenario.oracle_origin for a in case.comparison.assertions)
    constants = {node.value for node in ast.walk(ast.parse(assembled.worker_adapter.source))
                 if isinstance(node, ast.Constant) and isinstance(node.value, str)}
    assert not constants & {'indigo', 'saffron', 'cobalt', 'amber', 'DIAGNOSTIC ONLY', 'echo', 'oracle_origin'}
    # Exact frozen comparisons remain in controller data, rather than the adapter.
    assert assembled.cases[0].comparison.assertions[0].expected.suffix == '\n'
    prepared = CheckerFinalizer(store=store, resolver=resolver).prepare(assembled, base, sources)
    assert prepared.publish(store).kind == 'VerifierBundle'


@pytest.mark.parametrize('scenario_ids', [(), ('s0',), ('s0', 's1', 'invented')])
def test_assembly_rejects_missing_and_extra_scenarios(tmp_path, scenario_ids):
    store, checker, base, *_ = fixture(tmp_path)
    with pytest.raises(ValueError, match='scenario'):
        assemble_checker({key: fragment_for(checker.cases[0]) for key in scenario_ids},
                         store.get_artifact(base.contract), store.get_artifact(base.scenario_plan),
                         timeout_seconds=30.0)


@pytest.mark.parametrize('defect', ['unknown_requirement', 'duplicate_requirement', 'missing_requirement',
    'unknown_actual', 'duplicate_observation', 'unknown_expected', 'unknown_input', 'wrong_type',
    'verdict', 'assert', 'no_return', 'syntax', 'global', 'oversized_action', 'oversized_proposal'])
def test_fragment_rejects_invalid_or_uncovered_proposals(tmp_path, defect):
    store, checker, base, inputs, sources, resolver, proposal, request, runner, service = fixture(tmp_path)
    value = proposal.model_dump(mode='json')
    case = value['cases'][0]
    if defect == 'unknown_requirement': case['assertions'][0]['requirement_ids'] = ['invented']
    elif defect == 'duplicate_requirement': case['assertions'][0]['requirement_ids'] = ['echo', 'echo']
    elif defect == 'missing_requirement': case['assertions'] = []
    elif defect == 'unknown_actual': case['assertions'][0]['actual'] = 'missing'
    elif defect == 'duplicate_observation': case['observations'].append(case['observations'][0])
    elif defect == 'unknown_expected': case['assertions'][0]['expected'] = {'kind': 'observation', 'name': 'missing'}
    elif defect == 'unknown_input': case['assertions'][0]['expected'] = {'kind': 'input', 'name': 'missing'}
    elif defect == 'wrong_type': case['assertions'][0]['expected'] = {'kind': 'literal', 'value': True}
    elif defect == 'verdict': case['observations'][0]['name'] = 'reward'
    elif defect == 'assert': case['actions'] = "assert inputs['word'] == 'amber'\nreturn {'output': 'amber', 'exit': 0}"
    elif defect == 'no_return': case['actions'] = 'import click'
    elif defect == 'syntax': case['actions'] = 'return {'
    elif defect == 'global': case['actions'] = 'global changed\nreturn {}'
    elif defect == 'oversized_action': case['actions'] = '#' + 'é'*5000 + '\nreturn {}'
    else:
        case['actions'] = '#' + 'x'*8100 + '\nreturn {}'
        value['cases'] = [case] * 4
    with pytest.raises(ValueError):
        bad = CheckerFragmentProposal.model_validate_json(json.dumps(value))
        assemble_checker({'s0': bad, 's1': proposal}, store.get_artifact(base.contract),
                         store.get_artifact(base.scenario_plan), timeout_seconds=30.0)


def test_fragment_schema_excludes_model_controlled_identity_and_boilerplate(tmp_path):
    *_, proposal, request, runner, service = fixture(tmp_path)
    assert set(proposal.model_dump()) == {'cases'}
    assert set(proposal.cases[0].model_dump()) == {'actions', 'inputs', 'observations', 'assertions'}
    assert set(proposal.cases[0].assertions[0].model_dump()) == {'requirement_ids', 'actual', 'operator', 'expected'}
    data = proposal.model_dump(mode='json')
    data['cases'][0]['case_id'] = 'worker-controlled'
    with pytest.raises(ValueError):
        CheckerFragmentProposal.model_validate_json(json.dumps(data))
    assert request.allowed_requirement_ids == ('echo',)


def test_fragment_comparison_error_identifies_exact_case_type_and_operator(tmp_path):
    store, checker, base, *_ = fixture(tmp_path)
    proposal = fragment_for(checker.cases[0]).model_dump(mode='json')
    case = proposal['cases'][0]
    case['observations'].append({'name': 'warnings', 'type': 'string_list'})
    case['assertions'].append({'requirement_ids': ['echo'], 'actual': 'warnings',
        'operator': 'contains', 'expected': {'kind': 'literal', 'value': 'DeprecationWarning'}})
    fragment = CheckerFragmentProposal.model_validate_json(json.dumps(proposal))
    with pytest.raises(ValueError, match='s0 case 1 assertion warnings.*string_list.*contains'):
        assemble_checker({'s0': fragment, 's1': fragment_for(checker.cases[1])},
            store.get_artifact(base.contract), store.get_artifact(base.scenario_plan))


def test_fragment_generation_publishes_bound_record_and_real_cost(tmp_path):
    store, checker, base, inputs, sources, resolver, proposal, request, runner, service = fixture(tmp_path)
    result = service.generate((GenerationCandidate(request=request),), inputs, sources)
    record = CheckerFragmentRecord.model_validate_json(store.get_bytes(result.record_ref))
    assert record.proposal == proposal and record.scenario_id == 's0'
    assert (record.contract, record.scenario_plan, record.baseline, record.environment) == (
        base.contract, base.scenario_plan, base.baseline, base.environment)
    assert record.costs[-1] == result.generation.cost and record.costs[-1].input_tokens == 41
    assert result.record_ref.kind == 'm4-checker-fragment'
    assert result.record_ref.visibility is Visibility.PRIVATE
    assert len(runner.calls) == 1
    entry = json.loads(store.get_bytes(result.journal_refs[-1]))
    assert entry['binding']['scenario_id'] == 's0' and entry['status'] == 'accepted'


@pytest.mark.parametrize('kind', ['m4-checker-fragment', 'checker-fragment-authoring-journal'])
def test_fragment_publication_failure_replays_without_regeneration(tmp_path, monkeypatch, kind):
    store, checker, base, inputs, sources, resolver, proposal, request, runner, service = fixture(tmp_path)
    original = store.put_bytes
    def fail(data, name, visibility):
        if name == kind:
            raise OSError('diagnostic fragment publication outage')
        return original(data, name, visibility)
    monkeypatch.setattr(store, 'put_bytes', fail)
    with pytest.raises(FragmentPublicationPending) as caught:
        service.generate((GenerationCandidate(request=request),), inputs, sources)
    monkeypatch.setattr(store, 'put_bytes', original)
    result = caught.value.replay(store)
    assert result == caught.value.replay(store)
    assert len(runner.calls) == 1


def test_invalid_fragment_retains_rejected_attempt_cost(tmp_path):
    store, checker, base, inputs, sources, resolver, proposal, request, runner, service = fixture(tmp_path)
    assertion = proposal.cases[0].assertions[0].model_copy(update={'requirement_ids': ('invented',)})
    broken = proposal.model_copy(update={'cases': (proposal.cases[0].model_copy(update={'assertions': (assertion,)}),)})
    service.provider, runner = configured_diagnostic_provider(store, request, broken)
    with pytest.raises(AuthoringExhausted) as caught:
        service.generate((GenerationCandidate(request=request),), inputs, sources)
    entry = json.loads(store.get_bytes(caught.value.journal_refs[-1]))
    assert entry['status'] == 'rejected' and entry['cost']['input_tokens'] == 41
    assert len(runner.calls) == 1


def test_fragment_input_mismatch_is_rejected_before_dispatch(tmp_path):
    store, checker, base, inputs, sources, resolver, proposal, request, runner, service = fixture(tmp_path)
    with pytest.raises(ValueError, match='scenario'):
        service.generate((GenerationCandidate(request=request),), inputs.model_copy(update={'scenario_id': 'missing'}), sources)
    assert not runner.calls


def test_request_for_another_scenario_with_same_requirements_cannot_be_rebound(tmp_path):
    store, checker, base, inputs, sources, resolver, proposal, request, runner, service = fixture(tmp_path)
    with pytest.raises(ValueError, match='request differs'):
        service.generate((GenerationCandidate(request=request),),
                         inputs.model_copy(update={'scenario_id': 's1'}), sources)
    assert not runner.calls


def test_fragment_requires_all_selected_requirements_and_scopes_request_ids(tmp_path):
    store, checker, base, inputs, sources, resolver, proposal, request, runner, service = fixture(tmp_path)
    contract, plan = store.get_artifact(base.contract), store.get_artifact(base.scenario_plan)
    other = contract.requirements[0].model_copy(update={'requirement_id': 'other', 'mandatory': False})
    contract = contract.model_copy(update={'compatibility_obligations': (other,)})
    scenario = plan.scenarios[1].model_copy(update={'requirement_ids': ('echo', 'other')})
    plan = plan.model_copy(update={'scenarios': (plan.scenarios[0], scenario)})
    with pytest.raises(ValueError, match='coverage'):
        assemble_checker({'s0': proposal, 's1': proposal}, contract, plan)
    selected = build_fragment_request(request_id='SELECTED', response_id='RESPONSE',
        prompt_id='PROMPT', contract=contract, contract_ref=base.contract, plan=plan,
        plan_ref=base.scenario_plan, sources=sources, limits=limits(), scenario_id='s0')
    assert selected.allowed_requirement_ids == ('echo',)


@pytest.mark.parametrize('timeout', [0.0, -1.0, 31.0, True, float('nan'), float('inf')])
def test_assembly_rejects_invalid_timeout(tmp_path, timeout):
    store, checker, base, *_ = fixture(tmp_path)
    with pytest.raises(ValueError, match='timeout'):
        assemble_checker({case.inputs.scenario_id: fragment_for(case) for case in checker.cases},
            store.get_artifact(base.contract), store.get_artifact(base.scenario_plan), timeout)


def test_assembly_preserves_lower_frozen_timeout(tmp_path):
    store, checker, base, *_ = fixture(tmp_path)
    contract = store.get_artifact(base.contract)
    contract = contract.model_copy(update={'episode_limits': contract.episode_limits.model_copy(update={'wall_seconds': 3.0})})
    assembled = assemble_checker({case.inputs.scenario_id: fragment_for(case) for case in checker.cases},
        contract, store.get_artifact(base.scenario_plan))
    assert {case.comparison.timeout_seconds for case in assembled.cases} == {3.0}


def test_action_validation_rejects_control_flow_that_cannot_be_a_function_body(tmp_path):
    *_, proposal, request, runner, service = fixture(tmp_path)
    for invalid in ('break\nreturn {}', 'continue\nreturn {}',
                    'def helper(value, value):\n    return {}\nreturn {}',
                    'exec("assert True")\nreturn {}'):
        value = proposal.model_dump(mode='json')
        value['cases'][0]['actions'] = invalid
        with pytest.raises(ValueError, match='action'):
            CheckerFragmentProposal.model_validate_json(json.dumps(value))


def test_fragment_provider_recovery_reuses_exact_archived_outcome(tmp_path):
    store, checker, base, inputs, sources, resolver, proposal, request, runner, service = fixture(tmp_path)
    generation = service.provider.generate(request, CheckerFragmentProposal)
    result = service.generate((GenerationCandidate(request=request),), inputs, sources,
                              recovered_result=generation)
    assert result.generation == generation and len(runner.calls) == 1
    with pytest.raises(ValueError):
        service.generate((GenerationCandidate(request=request.model_copy(update={'seed': 1})),),
                         inputs, sources, recovered_result=generation)
    assert len(runner.calls) == 1


def test_fragment_repair_retains_failed_cost_and_requires_changed_request(tmp_path):
    store, checker, base, inputs, sources, resolver, proposal, request, runner, service = fixture(tmp_path)
    assertion = proposal.cases[0].assertions[0].model_copy(update={'requirement_ids': ('invented',)})
    broken = proposal.model_copy(update={'cases': (proposal.cases[0].model_copy(update={'assertions': (assertion,)}),)})
    service.provider, runner = configured_diagnostic_provider(store, request, broken)
    with pytest.raises(AuthoringExhausted) as caught:
        service.generate((GenerationCandidate(request=request),), inputs, sources)
    prior = caught.value.journal_refs
    unchanged = GenerationCandidate(request=request.model_copy(update={'request_id': 'REPAIR'}),
        diagnosis='unknown requirement', changed_input='identity only')
    with pytest.raises(ValueError, match='meaningful'):
        service.generate((unchanged,), inputs, sources, prior_journal_refs=prior)
    repaired = request.model_copy(update={'request_id': 'REPAIR',
        'instruction': request.instruction + ' Only echo is a selected requirement.'})
    service.provider, repaired_runner = configured_diagnostic_provider(store, repaired, proposal)
    result = service.generate((GenerationCandidate(request=repaired, diagnosis='unknown requirement',
        changed_input='require selected echo requirement'),), inputs, sources, prior_journal_refs=prior)
    assert len(result.journal_refs) == 2
    assert len(runner.calls) == len(repaired_runner.calls) == 1


def test_assembly_rejects_aggregate_adapter_overflow(tmp_path):
    store, checker, base, inputs, sources, resolver, proposal, request, runner, service = fixture(tmp_path)
    plan = store.get_artifact(base.scenario_plan)
    scenarios = tuple(plan.scenarios[0].model_copy(update={'scenario_id': f's{index}'}) for index in range(4))
    plan = plan.model_copy(update={'scenarios': scenarios})
    fragments = {}
    for scenario in scenarios:
        cases = tuple(proposal.cases[0].model_copy(update={
            'actions': "inputs['word'] += " + repr(scenario.scenario_id + str(index) + 'x'*7000) + '\n' + ACTIONS,
        }) for index in range(3))
        fragments[scenario.scenario_id] = CheckerFragmentProposal(cases=cases)
    with pytest.raises(ValueError, match='source size'):
        assemble_checker(fragments, store.get_artifact(base.contract), plan)


def test_adapter_compaction_preserves_all_probe_statements_and_literals():
    from feature_rl.verifiers.probe_adapter import build_probe_adapter
    cases = []
    for index in range(25):
        actions = f"value = inputs['value'] + {index}\n" + 'value = value + 1\n'*100
        actions += "return {'value': value}\n"
        cases.append((f'case_{index}', actions, ('value',), (('value', 'integer'),)))
    source = build_probe_adapter(cases)
    assert len(source.encode()) <= 65536
    run = next(node for node in ast.parse(source).body if isinstance(node, ast.FunctionDef))
    probes = [node for node in run.body if isinstance(node, ast.FunctionDef) and node.name.startswith('_m4_probe_')]
    assert len(probes) == len(cases)
    for probe, (_, actions, _, _) in zip(probes, cases):
        assert ast.dump(ast.Module(body=probe.body, type_ignores=[])) == ast.dump(ast.parse(actions))


def test_source_compaction_preserves_python_literal_and_scope_semantics():
    from feature_rl.verifiers.probe_adapter import _compact_source
    source = '''def observe(value):
    """a multiline literal
with retained spacing\x20\x20\x20
and braces {}"""
    text = f"{value = !r}"
    number_type = 1 .__class__.__name__
    values = [x ** 2 for x in range(value) if x > 0]
    try:
        result = {"text": text, "number_type": number_type}
        result["values"] = values
    except ValueError as error:
        raise RuntimeError("failed") from error
    return result
'''
    compact = _compact_source(source)
    assert ast.dump(ast.parse(compact)) == ast.dump(ast.parse(source))
    compile(compact, '<diagnostic-only>', 'exec')


def test_dispatch_metadata_compaction_preserves_ids_and_avoids_probe_names():
    from feature_rl.verifiers.probe_adapter import _dispatch_source
    functions = {'probe': object()}
    dispatch = {f'fragment_{"a"*64}_{index}': ('probe', ('message',),
        {'message': 'string', 'details': 'string_list'}) for index in range(1, 5)}
    source = _dispatch_source(dispatch, '_0 = 1\n_1 = 2\n_2 = 3\n')
    scope = {**functions, '_0': 1, '_1': 2, '_2': 3}
    exec(source, scope)
    assert scope['_m4_dispatch'] == {key: (functions[name], inputs, fields)
        for key, (name, inputs, fields) in dispatch.items()}
    assert (scope['_0'], scope['_1'], scope['_2']) == (1, 2, 3)
    assert len(source) < len('_m4_dispatch = ' + repr(dispatch))


def test_source_compaction_joins_simple_suites_without_changing_control_flow():
    from feature_rl.verifiers.probe_adapter import _compact_source
    source = '''def probe(value):
    try:
        if value:
            value += 1
            return value
        else:
            raise ValueError('missing')
    except ValueError:
        return 0
    finally:
        value = None
'''
    compact = _compact_source(source)
    assert ast.dump(ast.parse(compact)) == ast.dump(ast.parse(source))
    assert 'if value:value+=1;return value' in compact


def test_preparation_and_assembly_never_execute_action_source(tmp_path):
    store, checker, base, inputs, sources, resolver, proposal, request, runner, service = fixture(tmp_path)
    marker = tmp_path / 'action-was-executed'
    actions = f"from pathlib import Path\nPath({str(marker)!r}).touch()\n" + ACTIONS
    proposal = proposal.model_copy(update={'cases': (proposal.cases[0].model_copy(update={'actions': actions}),)})
    service.provider, runner = configured_diagnostic_provider(store, request, proposal)
    result = service.generate((GenerationCandidate(request=request),), inputs, sources)
    assembled = assemble_checker({'s0': proposal, 's1': proposal}, store.get_artifact(base.contract),
                                 store.get_artifact(base.scenario_plan))
    CheckerFinalizer(store=store, resolver=resolver).prepare(assembled, base, sources).publish(store)
    assert result.record.proposal == proposal
    assert not marker.exists()
