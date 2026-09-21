"""Small private result records and same-seed observation comparison."""
from datetime import datetime, timezone
import base64
import hashlib
from feature_rl.artifacts import canonical_json
from feature_rl.contracts import CostRecord, EvidenceRecord, Visibility
from feature_rl.grading.bootstrap import recipe_adapter_argv
from feature_rl.verifiers.language import decode_json
from feature_rl.verifiers.loader import read_bytes
from .models import QualificationRejected


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


def assert_reference_determinism(store,checked,receipt,signatures):
    """Compare realized inputs and raw gold outputs, without regrading them."""
    if receipt.manifest is None:
        raise QualificationRejected('invalid_evidence','reference lacks realized inputs')
    command=list(recipe_adapter_argv(checked))
    signature=[];used=0
    for case in receipt.cases:
        raw=read_bytes(store,case.evidence,32*1024*1024,'environment-execution',True);used+=len(raw)
        if used>64*1024*1024:
            raise QualificationRejected('invalid_evidence','aggregate reference observation receipt limit')
        value=decode_json(raw,32*1024*1024)
        rows=[row for row in value.get('commands',[]) if row.get('argv',[])[-len(command):]==command]
        if len(rows)!=1:
            raise QualificationRejected('invalid_evidence','reference requires one adapter execution per case')
        row=rows[0]
        try:
            stdout=base64.b64decode(row['stdout_b64'],validate=True)
            stderr=base64.b64decode(row['stderr_b64'],validate=True)
            if len(stdout)+len(stderr)>checked.verifier.permissions.output_limit_bytes or type(row['exit_code']) is not int:
                raise ValueError('invalid bounded output')
            observation=(row['stdin_sha256'],row['stdin_bytes'],row['exit_code'],row['reason'],
                hashlib.sha256(stdout).hexdigest(),hashlib.sha256(stderr).hexdigest())
        except (ValueError,KeyError,TypeError) as exc:
            raise QualificationRejected('invalid_evidence','invalid reference observation') from exc
        signature.append((case.case_id,case.status,case.passed,observation))
    signature=(receipt.manifest.sha256,receipt.reward,tuple(signature))
    seed=receipt.case_seed
    if seed in signatures and signatures[seed]!=signature:
        raise QualificationRejected('flaky_task','repeated reference seed '+str(seed)+' produced different candidate observations')
    signatures.setdefault(seed,signature)


RESET_PROBE_BYTES=b'\n# M5 temporary reset probe\n'


def reset_probe(source,rules,profile):
    """Choose one inert source mutation for the reset check."""
    from feature_rl.environments import SourceFile, SourceRejected
    from feature_rl.submission.source import change_path
    from feature_rl.environments.command_profiles import CommandRuntimeProfile
    if isinstance(profile, CommandRuntimeProfile):
        manifests=set(profile.manifest_paths)
        roots=profile.source_roots
    else:
        manifests={profile.manifest_path}
        roots=tuple(mapping.source for mapping in profile.source_mappings)
    candidates=[]
    for path,entry in source.files.items():
        try:change_path(path,rules)
        except SourceRejected:continue
        mapped=any(path==root or path.startswith(root+'/') for root in roots)
        candidates.append((path in manifests,not mapped,path,entry))
    if not candidates:
        raise QualificationRejected('unsupported_semantics','reset requires an existing allowed source file')
    _,_,path,entry=min(candidates,key=lambda item:item[:3])
    return path,SourceFile(entry.data+RESET_PROBE_BYTES,entry.executable)
