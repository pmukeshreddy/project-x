"""Semantic outcomes and declared control coverage; no confidence-based admission."""
from collections import Counter
from feature_rl.contracts import Disposition
from .models import GateOutcome, QualificationRejected, RepairHistory

ATTACKS={'forged_verdict','evaluator_detection','hardcoded_inputs','skipped_execution',
         'protocol_manipulation','excessive_output','dependency_shadowing','path_link','retained_state'}


def disposition_for(issues):
    """Missing evidence is provisional; observed defects retain their failure type."""
    if not issues:return Disposition.SUCCESS
    codes={issue.split(':',1)[0] for issue in issues}
    if codes&{'false_acceptance','false_rejection','oracle_disagreement','ambiguous_requirement','budget_exhausted'}:return Disposition.REJECTED
    if codes&{'flaky_task','invalid_evidence'}:return Disposition.INVALID
    if 'environment_failure' in codes:return Disposition.INFRASTRUCTURE
    if 'unsupported_semantics' in codes:return Disposition.UNSUPPORTED
    if 'unrecoverable_history' in codes:return Disposition.BLOCKED
    return Disposition.PROVISIONAL


def assess_outcome(checked, receipt, mode, targets, *, store=None):
    def outcome(passed,code,detail):return GateOutcome(passed=passed,code=code,detail=detail)
    if not receipt.cleanup_verified or receipt.disposition in {Disposition.INFRASTRUCTURE,Disposition.INVALID,Disposition.UNSUPPORTED} or receipt.reward is None:
        return outcome(False,'environment_failure','Unmeasured or unclean grading cannot settle this gate')
    compared=bool(receipt.cases) and receipt.build_evidence is not None and all(c.status=='completed' for c in receipt.cases)
    failed={r for case in receipt.cases if case.mandatory for a in case.assertions if not a.passed for r in a.requirement_ids}
    if mode=='positive':
        good=compared and receipt.reward==1 and not failed
        return outcome(good,'accepted' if good else 'false_rejection','Complete reference/alternative must pass every mandatory comparison')
    if mode=='baseline_health':
        preserved={r.requirement_id for r in checked.contract.compatibility_obligations if r.mandatory}
        good=compared and not (failed&preserved)
        return outcome(good,'accepted' if good else 'environment_failure','B must run every case and preserve declared compatibility obligations')
    if receipt.reward==1:return outcome(False,'false_acceptance','Known invalid control received a passing grade')
    if mode=='semantic_negative':
        good=compared and bool(targets) and failed==set(targets)
        if good:
            from .evidence import require_semantic_execution
            try:require_semantic_execution(store,checked,receipt)
            except QualificationRejected as exc:return outcome(False,exc.code,exc.detail)
        return outcome(good,'accepted' if good else 'oracle_disagreement','Runnable negative must fail exactly its declared semantic targets')
    if mode=='source_rejection':
        good=receipt.reason.startswith('source submission rejected:') and all(c.status=='not_run' for c in receipt.cases)
    elif mode=='protocol_failure':good=any(c.status=='protocol_failure' for c in receipt.cases)
    elif mode=='resource_failure':
        good=any(c.status=='candidate_failure' and c.reason in {'candidate timeout','candidate output_limit','candidate cpu_limit','candidate memory_limit'} for c in receipt.cases)
    else:raise QualificationRejected('invalid_evidence','unknown control outcome mode')
    return outcome(good,'accepted' if good else 'oracle_disagreement','Adversarial rejection must match the declared failure mechanism')


def validate_repairs(history, candidate, neutral_repairs):
    history=RepairHistory.model_validate(history)
    if history.candidate!=candidate:raise QualificationRejected('invalid_evidence','repair history belongs to a different candidate')
    if not history.complete:return None
    attempts=history.attempts
    if len(attempts)>4 or any(n>2 for n in Counter(r.stage for r in attempts).values()):
        raise QualificationRejected('budget_exhausted','at most two repairs per stage and four total per candidate')
    transitions=set();last={}
    for item in attempts:
        edge=(item.before,item.after)
        if item.before==item.after or edge in transitions:
            raise QualificationRejected('invalid_evidence','duplicate/no-change repair cannot describe a new diagnosed attempt')
        if item.stage in last and item.before!=last[item.stage]:
            raise QualificationRejected('invalid_evidence','repair stage history is not a contiguous version chain')
        transitions.add(edge);last[item.stage]=item.after
    for repair in neutral_repairs:
        if sum(r.stage=='environment' and r.after==repair.patch for r in attempts)!=1:
            raise QualificationRejected('invalid_evidence','known recipe repair missing/duplicated in global candidate history')
    return len(attempts)


def validate_control_plan(checked, policy, *, generated=()):
    """Return missing gates while rejecting contradictory/leaking declarations."""
    controls={p.control_id:p for p in checked.verifier.controls}
    declared=(*policy.controls,*generated)
    diagnoses={p.control_id:p for p in declared}
    if len(controls)!=len(checked.verifier.controls) or len(diagnoses)!=len(declared):
        raise QualificationRejected('invalid_evidence','duplicate control/diagnosis identity')
    if set(diagnoses)-set(controls):raise QualificationRejected('invalid_evidence','diagnosis names an unknown control')
    mandatory={r.requirement_id for r in checked.contract.requirements+checked.contract.compatibility_obligations if r.mandatory}
    feature={r.requirement_id for r in checked.contract.requirements if r.mandatory}
    compat=mandatory-feature
    missing=['control diagnosis: '+name for name in controls.keys()-diagnoses.keys()]
    present=set();omissions=set();attacks=set()
    permitted={checked.task.baseline,checked.task.contract,*checked.task.solver_view.public_checks,
        checked.task.solver_view.instruction,checked.task.solver_view.workspace,checked.task.solver_view.runtime_manifest,checked.task.solver_view.inventory}
    for name,diagnosis in diagnoses.items():
        control=controls[name]
        if len(set(diagnosis.targets))!=len(diagnosis.targets) or not set(diagnosis.targets)<=mandatory or not set(control.requirement_ids)<=mandatory:
            raise QualificationRejected('invalid_evidence','unknown/duplicate targeted requirement')
        if diagnosis.validity=='unresolved':missing.append('unresolved control validity: '+name);continue
        if diagnosis.validity=='equivalent':
            if control.category!='omission':raise QualificationRejected('invalid_evidence','equivalent exclusion is only for diagnosed omission mutants')
            continue  # An excluded mutant cannot satisfy targeted coverage.
        if diagnosis.validity=='valid':
            if control.category!='alternative_positive' or not control.expected_valid or diagnosis.mode!='positive' or diagnosis.targets:
                raise QualificationRejected('invalid_evidence','positive control validity contradiction')
            if not diagnosis.independence_evidence:missing.append('alternative independence evidence: '+name)
            if not control.author_provenance.inputs or not set(control.author_provenance.inputs)<=permitted:
                raise QualificationRejected('invalid_evidence','alternative author inputs include private H/checker or unknown context')
            if checked.task.baseline not in control.author_provenance.inputs or checked.task.contract not in control.author_provenance.inputs:
                raise QualificationRejected('invalid_evidence','alternative author must bind B and visible contract')
        else:
            if control.expected_valid or control.category=='alternative_positive':raise QualificationRejected('invalid_evidence','negative control validity contradiction')
            if control.category!='adversarial' and (diagnosis.mode!='semantic_negative' or not diagnosis.targets or set(diagnosis.targets)!=set(control.requirement_ids)):
                raise QualificationRejected('invalid_evidence','semantic negative needs exact declared requirement targets')
            if control.category=='regression' and not set(diagnosis.targets)<=compat:
                raise QualificationRejected('invalid_evidence','regression control must target preserved obligations')
            if control.category=='adversarial':
                if diagnosis.attack is None or diagnosis.mode in {'positive','baseline_health'}:raise QualificationRejected('invalid_evidence','adversarial control requires a named attack and rejection mechanism')
                attacks.add(diagnosis.attack)
            if control.category=='omission':omissions.update(diagnosis.targets)
        present.add(control.category)
    for category in {'omission','plausible_wrong','hardcoded','regression','adversarial','alternative_positive'}-present:
        missing.append('missing control category: '+category)
    missing.extend('missing targeted omission: '+name for name in mandatory-omissions)
    missing.extend('missing adversarial attack: '+name for name in ATTACKS-attacks)
    targets=set(policy.baseline_missing_requirements) or feature
    if not targets or not targets<=feature or len(set(policy.baseline_missing_requirements))!=len(policy.baseline_missing_requirements):
        raise QualificationRejected('invalid_evidence','baseline missing-feature targets must be distinct mandatory feature IDs')
    return tuple(sorted(missing)),tuple(sorted(targets))


def control_origins(service, checked):
    """Read selected M6 control authorship, including the actual archived prompt.

    An author's intended category is not a passing diagnosis. These records only
    bind targets/attack identity and establish what the independent author saw.
    """
    from feature_rl.contracts import Visibility
    from feature_rl.generation import GenerationResult, GenerationStage
    from feature_rl.pipeline.authoring import archived_outcome, observation
    from feature_rl.pipeline.authoring_models import AuthoringReceipt, AuthoringRequest
    from feature_rl.verifiers.control_authoring import ControlRecord, ControlProposal, ControlFinalizationInputs
    from feature_rl.verifiers.loader import read_local
    if service.policy.repair_history is None:
        return {}
    history=read_local(service.store,service.policy.repair_history,RepairHistory,'m5-repair-history',1024*1024)
    if not history.complete:
        return {}
    selected={control.control_id:control for control in checked.verifier.controls}
    origins={}
    for receipt_ref in history.journal_refs:
        if receipt_ref.kind!='m6-authoring-receipt':continue
        receipt=read_local(service.store,receipt_ref,AuthoringReceipt,'m6-authoring-receipt',4*1024*1024)
        if receipt.disposition!=Disposition.SUCCESS:continue
        for record_ref in receipt.outputs:
            if record_ref.kind!='m4-control-record':continue
            record=read_local(service.store,record_ref,ControlRecord,'m4-control-record',2*1024*1024)
            control=record.control
            if selected.get(control.control_id)!=control:continue
            if control.control_id in origins:
                raise QualificationRejected('invalid_evidence','ambiguous selected control authorship')
            for ref in (receipt_ref,receipt.request,record_ref):service.registry.assert_usable(ref)
            author=read_local(service.store,receipt.request,AuthoringRequest,'m6-authoring-request',4*1024*1024)
            job=service.registry.job(receipt.claim.job_id)
            inputs=author.call.inputs
            if (job.state!='completed' or job.result is None or job.result.disposition!=Disposition.SUCCESS
                    or job.result.artifacts!=(*receipt.outputs,receipt_ref)
                    or job.spec.operation!='construct' or job.spec.invocation!='m6-author:'+author.lane
                    or job.spec.inputs!=(history.candidate,receipt.request)
                    or job.spec.implementation!=service.policy.factory_revision
                    or receipt.revision!=job.spec.implementation
                    or not any(a.claim==receipt.claim and a.state=='completed' for a in service.registry.attempts(job.job_id))
                    or author.candidate!=history.candidate or author.call.source_pair!=checked.task.source_pair
                    or not isinstance(inputs,ControlFinalizationInputs)
                    or (inputs.control_id,inputs.category,inputs.requirement_ids,inputs.expected_valid,inputs.expected_reason)
                        !=(control.control_id,control.category,control.requirement_ids,control.expected_valid,control.expected_reason)
                    or (inputs.baseline,inputs.contract,inputs.environment)!=(record.baseline,record.contract,record.environment)
                    or (record.baseline,record.contract,record.environment)!=(checked.task.baseline,checked.task.contract,checked.task.environment)
                    or record.author_contexts!=author.call.generation.request.contexts
                    or control.author_provenance.inputs!=tuple(dict.fromkeys(c.source for c in record.author_contexts))):
                raise QualificationRejected('invalid_evidence','control authorship differs from selected Factory execution')
            if author.origin!='factory_dispatch':continue
            snapshot=observation(service,receipt.claim,'m6-generation')
            if snapshot is None:continue
            generated=archived_outcome(service,author,receipt.claim,ControlProposal)
            if not isinstance(generated,GenerationResult):continue
            evidence=record.generation_provenance.evidence[-1:]
            if (len(evidence)!=1 or evidence[0].producer!='feature_rl.generation.LocalGenerationProvider'
                    or evidence[0].scope!='real_integration' or evidence[0].exit_status!=0
                    or evidence[0].command!=('generate',author.call.generation.request.request_id)
                    or evidence[0].recorded_at!=generated.record.recorded_at
                    or set(evidence[0].artifacts)!=set(generated.record.archives.values())
                    or record.rationale!=generated.content.rationale):
                continue
            contexts=record.author_contexts
            permitted={checked.task.baseline,checked.task.contract,*checked.contract.public_checks}
            independent=(author.call.generation.request.stage==GenerationStage.ALTERNATIVE_AUTHORING
                and {ctx.source for ctx in contexts}<=permitted
                and {checked.task.baseline,checked.task.contract}<={ctx.source for ctx in contexts}
                and all(ctx.source.visibility in {Visibility.PUBLIC,Visibility.AUTHORING} for ctx in contexts))
            origins[control.control_id]=(author.call.attack,(record_ref,receipt.request,receipt_ref),independent)
    return origins


def diagnose_control(service, checked, control, receipt, binding_ref, origins):
    """Classify observed frozen-case behavior; never infer equivalence from a pass.

    A passing negative or a crashing semantic mutant stays unresolved. These
    diagnoses establish only observed behavior, not correctness on unseen cases.
    """
    from feature_rl.contracts import EvidenceRecord
    from .models import ControlDiagnosis
    origin=origins.get(control.control_id)
    mode='positive' if control.category=='alternative_positive' else 'semantic_negative'
    targets=() if mode=='positive' else control.requirement_ids
    if control.category=='adversarial':
        if receipt.reason.startswith('source submission rejected:'):
            mode='source_rejection'
        elif any(case.status=='protocol_failure' for case in receipt.cases):
            mode='protocol_failure'
        elif any(case.status=='candidate_failure' and case.reason in {
            'candidate timeout','candidate output_limit','candidate cpu_limit','candidate memory_limit'
        } for case in receipt.cases):
            mode='resource_failure'
    outcome=assess_outcome(checked,receipt,mode,targets,store=service.store)
    attack=origin[0] if origin else None
    refs=origin[1] if origin else ()
    independent=bool(origin and origin[2])
    settled=bool(origin and outcome.passed)
    mechanism_matches=True
    if control.category=='adversarial':
        allowed={
            'forged_verdict':{'semantic_negative','protocol_failure'},
            'evaluator_detection':{'semantic_negative'},
            'hardcoded_inputs':{'semantic_negative'},
            'skipped_execution':{'semantic_negative','protocol_failure'},
            'protocol_manipulation':{'protocol_failure'},
            'excessive_output':{'resource_failure'},
            'dependency_shadowing':{'semantic_negative','source_rejection'},
            'path_link':{'semantic_negative','source_rejection','protocol_failure'},
            'retained_state':{'semantic_negative'},
        }
        mechanism_matches=mode in allowed.get(attack,set())
        if attack=='excessive_output':
            mechanism_matches=mechanism_matches and any(case.status=='candidate_failure' and case.reason=='candidate output_limit' for case in receipt.cases)
        settled=settled and mechanism_matches
    if control.category=='alternative_positive' and not independent:settled=False
    note=('Observed frozen-case behavior matches the declared control targets and rejection mechanism.' if settled else
          'Unresolved: '+('selected real Factory authoring evidence is missing' if origin is None else
          'alternative authoring independence is unverified' if mode=='positive' and not independent else
          outcome.detail if not outcome.passed else 'observed failure mechanism does not establish the named attack'))
    ev=EvidenceRecord(producer='feature_rl.qualification',revision=service.revision,
        command=('QualificationService.diagnose',control.control_id,checked.task_ref.sha256),
        recorded_at=receipt.recorded_at,exit_status=0,artifacts=(binding_ref,*refs),
        scope='real_integration' if receipt.build_evidence is not None else 'source_inspection')
    independence=() if not independent else (ev.model_copy(update={
        'command':('QualificationService.control_inputs',control.control_id,checked.task_ref.sha256),
        'artifacts':refs,'scope':'source_inspection'}),)
    diagnosis=ControlDiagnosis(control_id=control.control_id,
        validity=('valid' if mode=='positive' else 'invalid') if settled else 'unresolved',
        mode=mode,targets=targets,attack=attack,evidence=(ev,),independence_evidence=independence,note=note)
    if not settled and outcome.code!='environment_failure':
        outcome=GateOutcome(passed=False,code='provisional',detail=note)
    return diagnosis,outcome
