"""Small private result records and same-seed observation comparison."""
from datetime import datetime, timezone
from feature_rl.artifacts import canonical_json
from feature_rl.contracts import CostRecord, EvidenceRecord, Visibility


def put_record(store,value,kind):
    return store.put_bytes(canonical_json(value.model_dump(mode='json') if hasattr(value,'model_dump') else value),kind,Visibility.PRIVATE)


def unknown_cost(category='verifier',note='M5 controller/publication overhead not measured'):
    return CostRecord(category=category,wall_seconds=None,cpu_seconds=None,gpu_seconds=None,input_tokens=None,
        output_tokens=None,human_minutes=None,usd=None,measurement='unknown',note=note)


def collapse_costs(costs):
    groups={}
    for item in costs:groups.setdefault(item.category,[]).append(item)
    result=[]
    for category,items in sorted(groups.items()):
        values={}
        for field in ('wall_seconds','cpu_seconds','gpu_seconds','input_tokens','output_tokens','human_minutes','usd'):
            parts=[getattr(x,field) for x in items]
            values[field]=sum(parts) if all(x is not None for x in parts) else None
        result.append(CostRecord(category=category,**values,measurement='measured' if all(x is not None for x in values.values()) else 'partial' if any(x is not None for x in values.values()) else 'unknown',
            note='Sum of attributable records; any unknown constituent keeps its channel unknown. Original records retained in grade results.'))
    return tuple(result) or (unknown_cost(),)


def evidence(ref,revision,command,*,scope='source_inspection'):
    return EvidenceRecord(producer='feature_rl.qualification',command=tuple(command),recorded_at=datetime.now(timezone.utc),
        exit_status=0,artifacts=(ref,),revision=revision,scope=scope)
