"""Bounded ordinary JSON and deterministic built-in comparisons; no evaluation."""
import hashlib
import json
from feature_rl.artifacts import canonical_json
from .models import Constant, Choice, InputPlan


def decode_json(data, cap):
    if type(data) is not bytes or len(data)>cap:raise ValueError('JSON byte cap')
    def pairs(items):
        result={}
        for key,value in items:
            if key in result:raise ValueError('duplicate JSON key')
            result[key]=value
        return result
    try:
        value=json.loads(data.decode('utf-8'),object_pairs_hook=pairs,
            parse_constant=lambda _:(_ for _ in ()).throw(ValueError('nonfinite JSON')))
    except (UnicodeError,RecursionError) as exc:raise ValueError('invalid JSON encoding/depth') from exc
    return value


def typed_equal(a,b):
    if isinstance(a,(list,tuple)) and isinstance(b,(list,tuple)):
        return len(a)==len(b) and all(typed_equal(x,y) for x,y in zip(a,b))
    return type(a) is type(b) and a==b


def compare(operator, actual, expected):
    if operator=='equal':return typed_equal(actual,expected)
    if operator=='contains':
        if type(actual) is not str or type(expected) is not str:raise ValueError('contains requires text')
        return expected in actual
    if operator=='member':
        if not isinstance(expected,(tuple,list)) or isinstance(actual,(tuple,list,dict)):raise ValueError('member requires scalar and array')
        return any(typed_equal(actual,x) for x in expected)
    raise ValueError('unsupported comparison operator')


def check_value(value,kind):
    types={'string':str,'integer':int,'boolean':bool,'null':type(None)}
    if kind.endswith('_list'):
        return type(value) is list and len(value)<=256 and all(check_value(x,kind[:-5]) for x in value)
    if type(value) is not types[kind]:return False
    if kind=='string':return len(value)<=16384
    if kind=='integer':return -(2**63)<=value<2**63
    return True


def parse_observations(data,case_id,fields,cap):
    value=decode_json(data,cap)
    if type(value) is not dict or set(value)!={'case_id','observations'} or value['case_id']!=case_id:
        raise ValueError('probe envelope/identity mismatch')
    obs=value['observations']
    if type(obs) is not dict or set(obs)!={x.name for x in fields}:raise ValueError('incomplete or extra observations')
    if any(not check_value(obs[x.name],x.type) for x in fields):raise ValueError('observation type/size mismatch')
    return obs


def realize_inputs(plan,case_id,seed):
    plan=InputPlan.model_validate(plan)
    if type(seed) is not int or not 0<=seed<2**63:raise ValueError('invalid case seed')
    result={}
    for field in plan.fields:
        domain=field.domain
        digest=hashlib.sha256(canonical_json(['m4-sha256-v1',seed,case_id,field.name])).digest()
        number=int.from_bytes(digest,'big')
        if isinstance(domain,Constant):value=domain.value
        elif isinstance(domain,Choice):value=domain.values[number%len(domain.values)]
        else:value=domain.low+number%(domain.high-domain.low+1)
        result[field.name]=value
    return result


def operand_value(operand,inputs,observations):
    if operand.kind=='literal':return operand.value
    if operand.kind=='observation':return observations[operand.name]
    value=inputs[operand.name]
    if operand.prefix or operand.suffix:
        if type(value) is not str:raise ValueError('input text template requires a string')
        value=operand.prefix+value+operand.suffix
    return value
