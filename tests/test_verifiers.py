"""Diagnostic controller-language tests; no feature qualification claims."""
import json
import pytest


def test_comparison_preserves_boolean_integer_and_array_order():
    from feature_rl.verifiers import compare
    assert compare('equal', True, 1) is False
    assert compare('equal', [True], [1]) is False
    assert compare('equal', ['b', 'a'], ['a', 'b']) is False
    assert compare('member', True, [1]) is False
    assert compare('contains', 'prefix value suffix', 'value') is True
    with pytest.raises(ValueError):
        compare('python', 'x', 'x')


@pytest.mark.parametrize('data', [
    b'{"case_id":"c","case_id":"c","observations":{}}',
    b'{"case_id":"c","observations":{},"passed":true}',
    b'{"case_id":"wrong","observations":{"count":1}}',
    b'{"case_id":"c","observations":{"count":true}}',
    b'{"case_id":"c","observations":{"count":1.0}}',
    b'{"case_id":"c","observations":{"count":1,"extra":0}}',
    b'{"case_id":"c","observations":{}}', b'', b'{}\n{}',
])
def test_protocol_cannot_forge_completion_or_coerce_types(data):
    from feature_rl.verifiers import ObservationField, parse_observations
    with pytest.raises(ValueError):
        parse_observations(data, 'c', (ObservationField(name='count', type='integer'),), 1024)


def test_protocol_accepts_only_complete_bounded_declared_observations():
    from feature_rl.verifiers import ObservationField, parse_observations
    data=b'{"case_id":"c","observations":{"count":1}}'
    assert parse_observations(data,'c',(ObservationField(name='count',type='integer'),),len(data))=={'count':1}
    with pytest.raises(ValueError):
        parse_observations(data,'c',(ObservationField(name='count',type='integer'),),len(data)-1)


def test_seed_domains_are_replayable_bounded_and_case_specific():
    from feature_rl.verifiers import InputPlan, realize_inputs
    plan=InputPlan.model_validate_json(json.dumps({'version':'m4-input-v1','scenario_id':'s',
      'requirement_ids':['r'],'fields':[
        {'name':'fixed','domain':{'kind':'constant','value':'keep'}},
        {'name':'n','domain':{'kind':'integer','low':10,'high':1000000}},
        {'name':'name','domain':{'kind':'choice','values':['red','green','blue']}}]}))
    one=realize_inputs(plan,'c',10)
    assert one['fixed']=='keep' and 10<=one['n']<=1000000 and one['name'] in ('red','green','blue')
    assert one==realize_inputs(plan,'c',10)
    assert one!=realize_inputs(plan,'other',10)
    assert one!=realize_inputs(plan,'c',11)
    for seed in (True,-1,1.0):
        with pytest.raises(ValueError):realize_inputs(plan,'c',seed)


def test_closed_schema_rejects_executable_generator_and_repeated_fields():
    from feature_rl.verifiers import InputPlan
    for fields in ([{'name':'x','domain':{'kind':'python','code':'raise Exception()'}}],
        [{'name':'x','domain':{'kind':'constant','value':1}}]*2):
        with pytest.raises(ValueError):
            InputPlan.model_validate_json(json.dumps({'version':'m4-input-v1','scenario_id':'s','requirement_ids':['r'],'fields':fields}))
