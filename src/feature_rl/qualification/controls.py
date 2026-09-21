"""Behavioral qualification gates for a small fixed set of wrong solutions."""
from feature_rl.contracts import Disposition
from .models import GateOutcome, QualificationRejected


def disposition_for(issues):
    if not issues:return Disposition.SUCCESS
    codes={issue.split(':',1)[0] for issue in issues}
    if codes&{'false_acceptance','false_rejection','oracle_disagreement','ambiguous_requirement','budget_exhausted'}:return Disposition.REJECTED
    if codes&{'flaky_task','invalid_evidence'}:return Disposition.INVALID
    if 'environment_failure' in codes:return Disposition.INFRASTRUCTURE
    if 'unsupported_semantics' in codes:return Disposition.UNSUPPORTED
    return Disposition.PROVISIONAL


def assess_outcome(checked, receipt, mode, targets):
    def outcome(passed,code,detail):return GateOutcome(passed=passed,code=code,detail=detail)
    if (not receipt.cleanup_verified or receipt.disposition not in {Disposition.SUCCESS,Disposition.REJECTED}
            or receipt.reward is None or any(case.status=='infrastructure_failure' for case in receipt.cases)):
        return outcome(False,'environment_failure','Unmeasured, infrastructure-failed or unclean grading cannot settle this gate')
    ran=bool(receipt.cases) and receipt.build_evidence is not None and all(case.evidence is not None and case.status!='not_run' for case in receipt.cases)
    compared=ran and all(case.status=='completed' for case in receipt.cases)
    failed={r for case in receipt.cases for assertion in case.assertions if not assertion.passed for r in assertion.requirement_ids}
    if mode=='positive':
        good=compared and receipt.reward==1 and all(case.passed for case in receipt.cases) and not failed
        return outcome(good,'accepted' if good else 'false_rejection','Historical gold must pass every check')
    if mode=='baseline_health':
        preserved={r.requirement_id for r in checked.contract.compatibility_obligations if r.mandatory}
        observed={r for case in receipt.cases for assertion in case.assertions if assertion.passed for r in assertion.requirement_ids}
        compatibility_ran=all(case.status=='completed' for case,comparison in zip(receipt.cases,getattr(checked,'comparisons',()))
            if any(set(assertion.requirement_ids)&preserved for assertion in comparison.assertions))
        baseline_positive=any(case.mandatory and case.status=='completed' and case.passed for case in receipt.cases)
        good=ran and baseline_positive and compatibility_ran and preserved<=observed and not (failed&preserved)
        return outcome(good,'accepted' if good else 'environment_failure','Baseline must pass a mandatory behavioral case and preserve existing compatibility obligations')
    if receipt.reward==1:return outcome(False,'false_acceptance','Known incomplete implementation received full reward')
    if mode=='negative':
        return outcome(ran,'accepted' if ran else 'environment_failure','Wrong implementation must execute and receive less than full reward')
    if mode=='baseline_absence':
        missing=failed&set(targets)
        for case,comparison in zip(receipt.cases,getattr(checked,'comparisons',())):
            if case.status in {'candidate_failure','protocol_failure'}:
                missing.update(r for assertion in comparison.assertions for r in assertion.requirement_ids if r in targets)
        good=ran and bool(missing)
        return outcome(good,'accepted' if good else 'oracle_disagreement','Baseline must lack at least one mandatory feature behavior')
    raise QualificationRejected('invalid_evidence','unknown qualification outcome mode')


def validate_control_plan(checked):
    controls=checked.verifier.controls
    mandatory={r.requirement_id for r in checked.contract.requirements+checked.contract.compatibility_obligations if r.mandatory}
    feature={r.requirement_id for r in checked.contract.requirements if r.mandatory}
    compatibility={r.requirement_id for r in checked.contract.compatibility_obligations if r.mandatory}
    required={'partial','happy_path','hardcoded'}|({'regression'} if compatibility else set())
    categories=[control.category for control in controls]
    if len({control.control_id for control in controls})!=len(controls) or len(set(categories))!=len(categories):
        raise QualificationRejected('invalid_evidence','qualification requires one wrong implementation per category')
    if len({control.patch for control in controls})!=len(controls):
        raise QualificationRejected('invalid_evidence','wrong implementations require distinct patch references')
    if set(categories)-required:
        raise QualificationRejected('invalid_evidence','unexpected wrong-implementation category')
    for control in controls:
        if not set(control.requirement_ids)<=mandatory:
            raise QualificationRejected('invalid_evidence','wrong implementation names an unknown requirement')
    if not feature:raise QualificationRejected('invalid_evidence','mandatory feature behavior required')
    return tuple('missing control category: '+category for category in sorted(required-set(categories))),tuple(sorted(feature))


def validate_wrong_sources(service,checked):
    """Compare the at most four reconstructed sources, without running code."""
    submissions=service.grader.submissions
    baseline=submissions.source(checked.task.baseline).tree_sha256
    seen=set()
    for control in checked.verifier.controls:
        source=submissions.resolve(control.patch,checked.task.baseline,checked.contract.allowed_changes)
        if source.tree_sha256==baseline:
            raise QualificationRejected('invalid_evidence','wrong implementation must change the baseline source')
        if source.tree_sha256 in seen:
            raise QualificationRejected('invalid_evidence','wrong implementations require distinct source trees')
        seen.add(source.tree_sha256)
