"""Content witnesses computed inside trusted FSDP policy workers, never candidates."""
import hashlib
import math
from feature_rl.artifacts import canonical_json


def tensor_witness(tensor):
    """Hash exact local tensor bytes including BF16, independent of checkpoint layout."""
    value=tensor.detach()
    if hasattr(value,'to_local'):value=value.to_local()
    value=value.cpu().contiguous().clone()
    return {'dtype':str(value.dtype),'shape':list(value.shape),
        'sha256':hashlib.sha256(bytes(value.untyped_storage())).hexdigest()}


def optimizer_witness(value):
    import torch
    if isinstance(value,torch.Tensor):return {'tensor':tensor_witness(value)}
    if isinstance(value,dict):
        return {'mapping':[[str(k),optimizer_witness(v)] for k,v in sorted(value.items(),key=lambda item:str(item[0]))]}
    if isinstance(value,(list,tuple)):return [optimizer_witness(v) for v in value]
    if value is None or type(value) in (str,int,bool):return value
    if type(value) is float and math.isfinite(value):return value
    raise ValueError('Unsupported/nonfinite native optimizer state')


def snapshot_state(model,optimizer,*,rank):
    parameters={name:tensor_witness(param) for name,param in model.named_parameters() if param.requires_grad}
    if not parameters or optimizer is None:raise ValueError('Actual trainable model and optimizer required')
    optimizer_state=optimizer_witness(optimizer.state_dict())
    return {'rank':rank,'trainable_parameters':parameters,
        'optimizer_sha256':hashlib.sha256(canonical_json(optimizer_state)).hexdigest()}


def compare_states(before,after):
    """Require stable worker/tensor topology and report actual content changes."""
    if len(before)!=len(after) or not before:raise ValueError('Native worker roster changed')
    changed=0;optimizer_changed=0
    for first,second in zip(before,after):
        a,b=first['trainable_parameters'],second['trainable_parameters']
        if first['rank']!=second['rank'] or a.keys()!=b.keys():raise ValueError('Native parameter/worker roster changed')
        for name in a:
            if a[name]['dtype']!=b[name]['dtype'] or a[name]['shape']!=b[name]['shape']:raise ValueError('Native tensor topology changed')
            changed+=a[name]['sha256']!=b[name]['sha256']
        optimizer_changed+=first['optimizer_sha256']!=second['optimizer_sha256']
    return {'changed_trainable_shards':changed,'changed_optimizer_shards':optimizer_changed}
