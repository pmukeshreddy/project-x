"""Content witnesses computed inside trusted FSDP policy workers, never candidates."""
import hashlib
import math
from feature_rl.artifacts import canonical_json


def attach_lora_optimizer(model, optim_config, num_training_steps):
    """AdamW and scheduler for fp32 LoRA parameters only.

    Pinned FSDP training keeps an fp32 master of every base weight. On one
    host that copy, the bf16 reference model, and vLLM's level-1 CPU backup
    exceed node RAM. The frozen base stays bf16; only adapter weights are stepped.
    """
    import torch
    from torch import optim
    params=[param for param in model.parameters() if param.requires_grad]
    if not params:
        raise ValueError('LoRA training requires trainable parameters')
    if any(param.dtype!=torch.float32 for param in params):
        raise ValueError('LoRA trainable parameters must be fp32')
    if optim_config.scheduler!='constant_with_warmup':
        raise ValueError('LoRA training uses constant_with_warmup')
    if type(num_training_steps) is not int or num_training_steps<1:
        raise ValueError('LoRA optimizer requires the trainer step count')
    warmup=optim_config.num_warmup_steps
    if type(warmup) is not int or warmup<0:
        raise ValueError('LoRA warmup steps must be a nonnegative integer')
    optimizer=optim.AdamW(params,lr=optim_config.lr,betas=tuple(optim_config.adam_betas),
                          weight_decay=optim_config.weight_decay)
    def scale(step,warmup=warmup):
        if warmup<=0 or step>=warmup:
            return 1.0
        return float(step)/float(warmup)
    scheduler=torch.optim.lr_scheduler.LambdaLR(optimizer,scale)
    return optimizer,scheduler


def trainable_binding(model, optimizer, *, lora, trim_optimizer=False):
    """Bind PEFT's actual gradient mask and the optimizer's parameter ownership."""
    parameters=dict(model.named_parameters())
    trainable={name:param for name,param in parameters.items() if param.requires_grad}
    if not trainable or optimizer is None:
        raise ValueError('Native policy requires trainable parameters and its optimizer')
    if lora:
        if len(trainable)==len(parameters) or any(
                not any(part.startswith('lora_') for part in name.split('.')) for name in trainable):
            raise ValueError('LoRA policy must freeze the base and train only adapter parameters')
    expected={id(param) for param in trainable.values()}
    if trim_optimizer:
        if optimizer.state:
            raise ValueError('Optimizer masking is allowed only before its first update')
        for group in optimizer.param_groups:
            group['params']=[param for param in group['params'] if param.requires_grad]
    actual=[id(param) for group in optimizer.param_groups for param in group['params']]
    if len(actual)!=len(set(actual)) or set(actual)!=expected:
        raise ValueError('Optimizer parameters differ from the exact native trainable mask')
    return {name:{'shape':list(param.shape),'dtype':str(param.dtype)} for name,param in trainable.items()}


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
