"""Controller compilation of compact behavioral specifications; no Docker/model calls."""
import ast
import json
import pytest

from test_checker_authoring import checker_fixture


def specification(checker):
    from feature_rl.verifiers.behavioral import BehavioralSpecification
    return BehavioralSpecification(scenarios=tuple({'cases': ({
        'actions': "import click\nfrom click.testing import CliRunner\n@click.command()\n@click.argument('word')\ndef command(word):\n    click.echo(word)\nresult = CliRunner().invoke(command, [inputs['word']])\nreturn {'output': result.output, 'exit': result.exit_code}\n",
        'inputs': case.inputs.fields,
        'expected': tuple({'name': a.actual, 'value': a.expected} for a in case.comparison.assertions),
    },)} for case in checker.cases))


def test_compiler_owns_identity_types_evidence_and_transport(tmp_path):
    from feature_rl.verifiers.behavioral import compile_behavioral
    from feature_rl.verifiers import CheckerFinalizer
    store, _, checker, inputs, sources, resolver = checker_fixture(tmp_path)
    spec = specification(checker)
    contract, plan = store.get_artifact(inputs.contract), store.get_artifact(inputs.scenario_plan)
    compiled = compile_behavioral(spec, contract, plan, timeout_seconds=5.0)
    assert compiled == compile_behavioral(spec, contract, plan, timeout_seconds=5.0)
    for case, scenario in zip(compiled.cases, plan.scenarios):
        assert case.inputs.scenario_id == scenario.scenario_id
        assert case.mandatory
        assert case.comparison.timeout_seconds == 5.0
        assert {a.operator for a in case.comparison.assertions} == {'equal'}
        assert all(a.oracle_origin == scenario.oracle_origin for a in case.comparison.assertions)
        assert {f.name: f.type for f in case.comparison.observations} == {'output': 'string', 'exit': 'integer'}
    constants = {n.value for n in ast.walk(ast.parse(compiled.worker_adapter.source)) if isinstance(n, ast.Constant) and isinstance(n.value, str)}
    assert not constants & {'DIAGNOSTIC ONLY', 'echo', 'indigo', 'saffron', 'oracle_origin'}
    frozen = CheckerFinalizer(store=store, resolver=resolver).prepare(compiled, inputs, sources).publish(store)
    assert frozen.visibility is __import__('feature_rl.contracts', fromlist=['Visibility']).Visibility.PRIVATE


def test_schema_fixes_scenario_count_and_excludes_controller_fields(tmp_path):
    from feature_rl.verifiers.behavioral import behavioral_schema
    store, _, checker, inputs, *_ = checker_fixture(tmp_path)
    schema = behavioral_schema(store.get_artifact(inputs.scenario_plan))
    data = specification(checker).model_dump(mode='json')
    schema.model_validate_json(json.dumps(data))
    data['scenarios'].pop()
    with pytest.raises(ValueError):
        schema.model_validate_json(json.dumps(data))
    data = specification(checker).model_dump(mode='json')
    data['scenarios'][0]['cases'][0]['case_id'] = 'invented'
    with pytest.raises(ValueError):
        schema.model_validate_json(json.dumps(data))


@pytest.mark.parametrize('defect', ['unknown_input', 'mixed_domain', 'verdict', 'assertion_code', 'empty_expected'])
def test_invalid_semantics_cannot_be_published(tmp_path, defect):
    from feature_rl.verifiers.behavioral import BehavioralSpecification, compile_behavioral
    store, _, checker, inputs, *_ = checker_fixture(tmp_path)
    data = specification(checker).model_dump(mode='json')
    case = data['scenarios'][0]['cases'][0]
    if defect == 'unknown_input': case['expected'][0]['value']['name'] = 'absent'
    elif defect == 'mixed_domain': case['inputs'][0]['domain'] = {'kind': 'choice', 'values': ['word', 1]}
    elif defect == 'verdict': case['expected'].append({'name':'reward', 'value': {'kind': 'literal', 'value': 1}})
    elif defect == 'assertion_code': case['actions'] = 'assert True\nreturn {}'
    else: case['expected'] = []
    with pytest.raises(ValueError):
        compile_behavioral(BehavioralSpecification.model_validate_json(json.dumps(data)), store.get_artifact(inputs.contract), store.get_artifact(inputs.scenario_plan), timeout_seconds=5.0)


def test_compiled_transport_round_trip_suppresses_probe_stdout():
    """Only a hand-written synthetic action executes; no repository/model source."""
    import subprocess
    import sys
    from feature_rl.verifiers.probe_adapter import build_probe_adapter
    actions = "print('candidate noise')\nimport json\njson.dumps = lambda *args, **kwargs: 'forged verdict'\nreturn {'value': inputs['value'] + 1}\n"
    source = build_probe_adapter((('check_1_1', actions, ('value',), (('value','integer'),)),))
    result = subprocess.run([sys.executable, '-I', '-c', source],
        input=json.dumps({'case_id':'check_1_1','inputs':{'value':4}}), text=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=5, check=True)
    assert json.loads(result.stdout) == {'case_id':'check_1_1','observations':{'value':5}}
    assert not result.stderr
