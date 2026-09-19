"""Semantic outcomes and declared control coverage; no confidence-based admission."""
from collections import Counter
from feature_rl.contracts import Disposition
from .models import GateOutcome, QualificationRejected, RepairHistory

ATTACKS={'forged_verdict','evaluator_detection','hardcoded_inputs','skipped_execution',
         'protocol_manipulation','excessive_output','dependency_shadowing','path_link','retained_state'}


def disposition_for(issues):
    """Missing evidence is provisional; observed defects retain their failure type."""
    codes={issue.split(':',1)[0] for issue in issues}
    if codes&{'false_acceptance','false_rejection','oracle_disagreement','ambiguous_requirement','budget_exhausted'}:return Disposition.REJECTED
    if codes&{'flaky_task','invalid_evidence'}:return Disposition.INVALID
    if 'environment_failure' in codes:return Disposition.INFRASTRUCTURE
    if 'unsupported_semantics' in codes:return Disposition.UNSUPPORTED
    if 'unrecoverable_history' in codes:return Disposition.BLOCKED
    return Disposition.PROVISIONAL


def assess_outcome(checked, receipt, mode, targets):
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


def validate_control_plan(checked, policy):
    """Return missing gates while rejecting contradictory/leaking declarations."""
    controls={p.control_id:p for p in checked.verifier.controls}
    diagnoses={p.control_id:p for p in policy.controls}
    if len(controls)!=len(checked.verifier.controls) or len(diagnoses)!=len(policy.controls):
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
