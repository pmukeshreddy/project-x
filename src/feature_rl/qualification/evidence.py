"""Join trusted produced grade artifacts to exact scheduled operations and M3 runs."""
from datetime import datetime, timezone
import base64
import hashlib
from feature_rl.artifacts import canonical_json
from feature_rl.contracts import ArtifactRef, CostRecord, Disposition, EvidenceRecord, Visibility
from feature_rl.environments.models import Ownership
from feature_rl.grading import read_grade
from feature_rl.verifiers import materialize_manifest
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
            note='Sum of attributable records; any unknown constituent keeps its channel unknown. Original records retained in upstream Registry results.'))
    return tuple(result) or (unknown_cost(),)


def evidence(ref,revision,command,*,scope='source_inspection'):
    return EvidenceRecord(producer='feature_rl.qualification',command=tuple(command),recorded_at=datetime.now(timezone.utc),
        exit_status=0,artifacts=(ref,),revision=revision,scope=scope)


def validate_grade(store,checked,submission,seed,result,grader,*,seen=None):
    """Validate a result obtained from the actual grader or its selected Registry job."""
    if result.operation!='grade' or not result.artifacts:raise QualificationRejected('invalid_evidence','grade operation has no receipt')
    receipt=read_grade(store,result.artifacts[0])
    if (receipt.task,receipt.submission,receipt.case_seed,receipt.verifier,receipt.implementation_revision)!=(checked.task_ref,submission,seed,checked.task.private_oracle,grader.revision):
        raise QualificationRejected('invalid_evidence','grade task/submission/seed/verifier/revision mismatch')
    if receipt.disposition!=result.disposition:raise QualificationRejected('invalid_evidence','grade operation/receipt disposition disagreement')
    expected=materialize_manifest(checked,seed)
    expected_bytes=canonical_json(expected.model_dump(mode='json'))
    if receipt.manifest is None or read_bytes(store,receipt.manifest,4*1024*1024,'m4-case-manifest',True)!=expected_bytes:
        raise QualificationRejected('invalid_evidence','grade case manifest differs from frozen schedule')
    if receipt.expected_case_ids!=tuple(c.case_id for c in expected.cases):
        raise QualificationRejected('invalid_evidence','missing/duplicate/reordered grade case ledger')
    for actual,case,comparison in zip(receipt.cases,expected.cases,checked.comparisons):
        if actual.mandatory!=case.mandatory:raise QualificationRejected('invalid_evidence','case mandatory flag drift')
        if actual.status=='completed' and tuple((a.assertion_id,a.requirement_ids) for a in actual.assertions)!=tuple((a.assertion_id,a.requirement_ids) for a in comparison.assertions):
            raise QualificationRejected('invalid_evidence','completed assertion ledger differs from frozen comparison')
    if not any(e.producer=='feature_rl.grading' and result.artifacts[0] in e.artifacts and e.revision==grader.revision and e.exit_status==0 for e in result.evidence):
        raise QualificationRejected('invalid_evidence','missing actual grading producer receipt')
    ids=[];used=0;runtime_refs=list(receipt.runtime_evidence);runtime_values={};workspaces=set()
    if len(set(runtime_refs))!=len(runtime_refs):raise QualificationRejected('invalid_evidence','duplicate runtime evidence')
    if receipt.build_evidence is not None and receipt.build_evidence not in runtime_refs:raise QualificationRejected('invalid_evidence','build evidence is not in runtime ledger')
    if any(c.evidence is not None and c.evidence not in runtime_refs for c in receipt.cases):raise QualificationRejected('invalid_evidence','case execution evidence missing from runtime ledger')
    source=None
    if receipt.source is not None:
        source=grader.submissions.resolve(submission,checked.task.baseline,checked.contract.allowed_changes)
        if read_bytes(store,receipt.source,grader.runtime.policy.max_archive_bytes,'source-archive',True)!=source.to_tar():
            raise QualificationRejected('invalid_evidence','grade reconstructed source differs from assigned submission')
    policies=[ref for ref in checked.recipe.provenance.inputs if ref.kind=='sandbox-policy']
    for ref in runtime_refs:
        raw=read_bytes(store,ref,32*1024*1024,'environment-execution',True);used+=len(raw)
        if used>64*1024*1024:raise QualificationRejected('invalid_evidence','aggregate runtime receipt limit')
        value=decode_json(raw,32*1024*1024)
        owner=Ownership.model_validate_json(canonical_json(value.get('record')))
        binding=owner.binding
        runtime_values[ref]=(value,owner)
        if value.get('cleanup_verified') is not True or owner.phase!='removed' or len(policies)!=1:
            raise QualificationRejected('environment_failure','runtime cleanup or policy evidence unverified')
        expected_binding={'recipe':checked.task.environment.sha256,'policy':policies[0].sha256,'revision':grader.runtime.revision,'role':'candidate',
            'allowed_changes':hashlib.sha256(canonical_json(checked.contract.allowed_changes.model_dump(mode='json'))).hexdigest(),
            'phase':value.get('phase')}
        if receipt.source is not None:
            expected_binding.update(source=receipt.source.sha256,source_input=receipt.source.sha256,
                source_raw=hashlib.sha256(source.to_tar()).hexdigest(),tree=source.tree_sha256)
        if any(binding.get(k)!=v for k,v in expected_binding.items()):raise QualificationRejected('invalid_evidence','runtime source/recipe/policy/revision mismatch')
        if value.get('revision')!=grader.runtime.revision or not binding.get('workspace_id'):
            raise QualificationRejected('invalid_evidence','runtime revision/workspace missing')
        workspaces.add(binding['workspace_id'])
        if ref==receipt.build_evidence and value.get('phase')!='build':raise QualificationRejected('invalid_evidence','build receipt phase mismatch')
        if ref!=receipt.build_evidence and value.get('phase')!='execute':raise QualificationRejected('invalid_evidence','case receipt phase mismatch')
        if owner.operation_id in ids or (seen is not None and owner.operation_id in seen):
            raise QualificationRejected('invalid_evidence','replayed runtime operation cannot count as an independent run')
        ids.append(owner.operation_id)
    if len(workspaces)>1 or (seen is not None and any('workspace:'+w in seen for w in workspaces)):
        raise QualificationRejected('invalid_evidence','grade reused or mixed independent workspace identities')
    executed=[case.evidence for case in receipt.cases if case.evidence is not None]
    if len(set(executed))!=len(executed):raise QualificationRejected('invalid_evidence','one case execution cannot settle multiple cases')
    if runtime_refs and runtime_refs!=[receipt.build_evidence,*executed]:
        raise QualificationRejected('invalid_evidence','runtime build/case ledger is not exact')
    for actual,case,comparison in zip(receipt.cases,expected.cases,checked.comparisons):
        if actual.evidence is not None:
            value,owner=runtime_values[actual.evidence]
            if value.get('extra',{}).get('build_evidence')!=receipt.build_evidence.model_dump(mode='json'):
                raise QualificationRejected('invalid_evidence','case did not consume this exact build')
            _assert_case_observation(checked,actual,case,comparison,value,owner)
    if receipt.reward==1 and (not ids or receipt.build_evidence is None):raise QualificationRejected('invalid_evidence','passing grade lacks actual runtime evidence')
    if seen is not None:seen.update((*ids,*('workspace:'+w for w in workspaces)))
    return receipt,tuple(ids)


def _assert_case_observation(checked,actual,case,comparison,value,owner):
    """Recompute M4's closed comparisons from its exact recorded command output."""
    from feature_rl.verifiers import parse_observations
    from feature_rl.verifiers.language import compare,operand_value,check_value
    command=['python','-c',checked.adapter.decode('utf-8')]
    rows=[r for r in value.get('commands',[]) if r.get('argv',[])[-3:]==command]
    extra=value.get('extra',{})
    if actual.status=='infrastructure_failure':
        if extra.get('failure_category') not in {'infrastructure','unresolved'}:
            raise QualificationRejected('invalid_evidence','case infrastructure classification differs from runtime')
        return
    stdin=canonical_json({'case_id':case.case_id,'inputs':case.model_dump(mode='json')['inputs']})
    if len(rows)!=1 or rows[0].get('stdin_sha256')!=hashlib.sha256(stdin).hexdigest() or rows[0].get('stdin_bytes')!=len(stdin) or rows[0].get('argv',[])[-4]!=(owner.container_id or owner.container_name):
        raise QualificationRejected('invalid_evidence','case command or private input bytes mismatch')
    row=rows[0]
    if extra.get('error') is not None or extra.get('failure_category') not in {'none','candidate'}:
        raise QualificationRejected('invalid_evidence','case runtime result is not a candidate observation')
    if actual.status=='candidate_failure':
        if extra.get('reason') in {'completed','command_failed'} and not (comparison.mode=='json' and row.get('exit_code')!=0):
            raise QualificationRejected('invalid_evidence','candidate failure is unsupported by the raw runtime result')
        if actual.reason!='candidate '+str(extra.get('reason')):
            raise QualificationRejected('invalid_evidence','candidate failure reason drift')
        return
    if row.get('reason')!='exited' or extra.get('reason') not in {'completed','command_failed'} or (comparison.mode=='json' and row.get('exit_code')!=0):
        raise QualificationRejected('invalid_evidence','comparison did not follow completed candidate command')
    try:
        stdout=base64.b64decode(row['stdout_b64'],validate=True);stderr=base64.b64decode(row['stderr_b64'],validate=True)
        cap=checked.verifier.permissions.output_limit_bytes
        if len(stdout)+len(stderr)>cap:raise ValueError('combined output byte cap')
        if comparison.mode=='json':observations=parse_observations(stdout,case.case_id,comparison.observations,cap)
        else:
            raw={'exit_code':row['exit_code'],'stdout':stdout.decode('utf-8'),'stderr':stderr.decode('utf-8')}
            observations={f.name:raw[f.name] for f in comparison.observations}
            if any(not check_value(observations[f.name],f.type) for f in comparison.observations):raise ValueError('process observation type')
        expected=tuple(compare(a.operator,observations[a.actual],operand_value(a.expected,case.inputs,observations)) for a in comparison.assertions)
    except (ValueError,KeyError,UnicodeError):
        if actual.status=='protocol_failure':return
        raise QualificationRejected('invalid_evidence','completed comparison has invalid raw observation protocol')
    if actual.status!='completed' or tuple(a.passed for a in actual.assertions)!=expected:
        raise QualificationRejected('invalid_evidence','assertion verdict differs from actual closed comparison')


def validate_reset(store,checked,projection_ref,reset_ref,grader,*,seen):
    """Rejoin timeout, dirty saved source, restored source and independent workspace."""
    from feature_rl.environments import SourceFile
    from feature_rl.environments.models import SavedSource
    from feature_rl.verifiers.loader import read_local
    from .models import ResetReceipt, ReferenceProjection
    reset=read_local(store,reset_ref,ResetReceipt,'m5-reset')
    projection=read_local(store,projection_ref,ReferenceProjection,'m5-reference-projection',1024*1024)
    if (reset.task,reset.projection,reset.initial_source,reset.reset_source)!=(checked.task_ref,projection_ref,projection.projected_source,projection.projected_source) or not reset.cleanup_verified or reset.generation_after<=reset.generation_before:
        raise QualificationRejected('invalid_evidence','reset exact initial source/projection/generation mismatch')
    value=decode_json(read_bytes(store,reset.interruption,32*1024*1024,'environment-execution',True),32*1024*1024)
    owner=Ownership.model_validate_json(canonical_json(value.get('record')))
    saved=SavedSource.model_validate_json(canonical_json(owner.saved_source))
    policies=[ref for ref in checked.recipe.provenance.inputs if ref.kind=='sandbox-policy']
    expected={'recipe':checked.task.environment.sha256,'policy':policies[0].sha256 if len(policies)==1 else None,
        'revision':grader.runtime.revision,'role':'candidate','phase':'development','workspace_id':reset.workspace_id,
        'generation':str(reset.generation_before),'source_input':projection.projected_source.sha256,
        'source':saved.artifact.sha256,'source_raw':saved.raw_sha256,'tree':saved.tree_sha256,
        'allowed_changes':hashlib.sha256(canonical_json(checked.contract.allowed_changes.model_dump(mode='json'))).hexdigest()}
    extra=value.get('extra',{})
    if value.get('phase')!='development' or value.get('revision')!=grader.runtime.revision or value.get('cleanup_verified') is not True or owner.phase!='removed' or owner.operation_id!=reset.interruption_operation or any(owner.binding.get(k)!=v for k,v in expected.items()) or extra.get('reason')!='timeout' or extra.get('failure_category')!='candidate' or extra.get('error') is not None or extra.get('saved_source')!=saved.model_dump(mode='json'):
        raise QualificationRejected('invalid_evidence','reset interruption/runtime/source binding mismatch')
    command=['python','-I','-c','import time;time.sleep(5)']
    rows=[r for r in value.get('commands',[]) if r.get('argv',[])[-len(command):]==command]
    if len(rows)!=1 or rows[0].get('reason')!='timeout' or rows[0].get('stdin_bytes')!=0:
        raise QualificationRejected('invalid_evidence','reset lacks actual fixed bounded timeout command')
    dirty=grader.runtime.source(saved.artifact);initial=grader.runtime.source(projection.projected_source)
    expected_files=dict(initial.files)
    expected_files[checked.contract.allowed_changes.source_roots[0]+'/__m5_reset_probe__.py']=SourceFile(b'# M5 temporary reset probe\n',False)
    if dirty.files!=expected_files or saved.artifact==projection.projected_source or dirty.tree_sha256!=saved.tree_sha256:
        raise QualificationRejected('invalid_evidence','reset interruption did not observe the exact saved diagnostic mutation')
    identities=(owner.operation_id,'workspace:'+reset.workspace_id)
    if any(identity in seen for identity in identities):
        raise QualificationRejected('invalid_evidence','replayed reset interruption/workspace')
    seen.update(identities)
    return reset
