"""Join trusted produced grade artifacts to exact scheduled operations and M3 runs."""
from datetime import datetime, timezone
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
    ids=[];used=0;runtime_refs=list(receipt.runtime_evidence)
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
        if value.get('cleanup_verified') is not True or owner.phase!='removed' or len(policies)!=1:
            raise QualificationRejected('environment_failure','runtime cleanup or policy evidence unverified')
        expected_binding={'recipe':checked.task.environment.sha256,'policy':policies[0].sha256,'revision':grader.runtime.revision,'role':'candidate'}
        if receipt.source is not None:expected_binding['source']=receipt.source.sha256
        if any(binding.get(k)!=v for k,v in expected_binding.items()):raise QualificationRejected('invalid_evidence','runtime source/recipe/policy/revision mismatch')
        if ref==receipt.build_evidence and value.get('phase')!='build':raise QualificationRejected('invalid_evidence','build receipt phase mismatch')
        if ref!=receipt.build_evidence and value.get('phase')!='execute':raise QualificationRejected('invalid_evidence','case receipt phase mismatch')
        if owner.operation_id in ids or (seen is not None and owner.operation_id in seen):
            raise QualificationRejected('invalid_evidence','replayed runtime operation cannot count as an independent run')
        ids.append(owner.operation_id)
    if receipt.reward==1 and (not ids or receipt.build_evidence is None):raise QualificationRejected('invalid_evidence','passing grade lacks actual runtime evidence')
    if seen is not None:seen.update(ids)
    return receipt,tuple(ids)
