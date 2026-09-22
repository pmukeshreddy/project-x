"""Bounded JSON transport and deterministic, type-sensitive equality."""
import json


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
