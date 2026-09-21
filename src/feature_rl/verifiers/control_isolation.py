"""Check whether a requested semantic negative can preserve its other obligations.

An authoring assessment can reject an impossible target. It cannot certify a
control: certification still requires the actual source rebuild and comparisons.
"""
from typing import Annotated, Literal
from pydantic import Field

from feature_rl.contracts import StrictModel
from feature_rl.requirements.service import validate_recovered_generation
from .control_authoring import build_control_request, resolve_control_inputs
from .models import Name, unique


class IsolationAssessment(StrictModel):
    disposition: Literal['separable', 'inseparable']
    targets: Annotated[tuple[Name, ...], Field(min_length=1, max_length=256)]
    preserved: Annotated[tuple[Name, ...], Field(max_length=256)]
    coupled: Annotated[tuple[Name, ...], Field(max_length=256)]
    explanation: Annotated[str, Field(min_length=1, max_length=4096)]
    mutation: Annotated[str, Field(max_length=4096)]

    def validate_obligations(self, contract, targets):
        mandatory = {r.requirement_id for r in contract.requirements +
                     contract.compatibility_obligations if r.mandatory}
        for values in (self.targets, self.preserved, self.coupled):
            unique(values, 'isolation requirement IDs')
        if set(self.targets) != set(targets) or not set(targets) <= mandatory:
            raise ValueError('isolation assessment changed the declared targets')
        if set(self.preserved) != mandatory - set(targets):
            raise ValueError('isolation assessment omits a mandatory preserved requirement')
        if not set(self.coupled) <= set(self.preserved):
            raise ValueError('coupled requirement must be a preserved obligation')
        if self.disposition == 'inseparable':
            if not self.coupled or self.mutation:
                raise ValueError('inseparable target requires a coupling explanation and no fake mutation')
        elif self.coupled or not self.mutation.strip():
            raise ValueError('separable target requires a concrete mutation and no coupled obligations')
        return self


def assess_isolation(service, *, inputs, sources, request_id, response_id, prompt_id, limits):
    """One bounded, archived Astra assessment before repairing a semantic control."""
    inputs, sources, contract, _, _, _ = resolve_control_inputs(
        service.store, service.resolver, inputs, sources)
    if inputs.category not in {'omission', 'plausible_wrong', 'hardcoded', 'regression'}:
        raise ValueError('target isolation applies only to semantic negative controls')
    request = build_control_request(request_id=request_id, response_id=response_id,
        prompt_id=prompt_id, store=service.store, resolver=service.resolver,
        inputs=inputs, sources=sources, limits=limits)
    request = request.model_copy(update={'instruction': (
        'Assess whether the declared negative control can exist under the exact contract. '
        f'Targets: {inputs.requirement_ids}. Enumerate every other mandatory requirement as preserved. '
        'Consider logical entailment, shared behavior, and relations between APIs across the entire '
        'contract domain, not just finite checker examples. A proposed mutation must fail every target '
        'while preserving every other mandatory requirement. Do not overfit probes or introduce '
        'exceptions for public examples. If preserving another requirement entails preserving a '
        'target, return inseparable, identify those coupled requirements, explain the entailment, '
        'and leave mutation empty. Otherwise return separable and a concrete semantic mutation '
        'strategy, with no coupled requirements. This is an assessment, not executable source or '
        'a claim that qualification passed. Do not redesign the contract or broaden the targets.')})
    result = service.provider.generate(request, IsolationAssessment)
    validate_recovered_generation(service.store, request, IsolationAssessment, result)
    assessment = IsolationAssessment.model_validate(result.content)
    assessment.validate_obligations(contract, inputs.requirement_ids)
    return assessment, result


def require_isolated_execution(store, checked, receipt, targets):
    """Reject an authored candidate using the unchanged qualification semantics."""
    from feature_rl.qualification.controls import assess_outcome
    outcome = assess_outcome(checked, receipt, 'semantic_negative', targets, store=store)
    if not outcome.passed:
        failed = sorted({requirement for case in receipt.cases if case.mandatory
            for assertion in case.assertions if not assertion.passed
            for requirement in assertion.requirement_ids})
        raise ValueError(f'{outcome.code}: {outcome.detail}; declared={sorted(targets)}; observed={failed}')
    return outcome


class ControlIsolationExecutor:
    """Rebuild an authored negative before publishing an accepted control record."""
    def __init__(self, *, factory, task, seeds, invocation, observe, retained):
        from feature_rl.grading import GradingService
        from feature_rl.pipeline import Factory
        from .loader import load_verifier
        if type(factory) is not Factory or type(factory.grading) is not GradingService:
            raise TypeError('control isolation requires the actual Factory/GradingService')
        self.factory, self.invocation = factory, invocation
        self.grading, self.task, self.observe, self.retained = factory.grading, task, observe, retained
        self.seeds = tuple(seeds)
        self.checked = load_verifier(self.grading.store, task)

    def original(self, result):
        """Validate the selected Factory wrapper and read its exact M4 result."""
        from feature_rl.pipeline.grading import FrozenGrade, validated, verify_result
        from feature_rl.pipeline.packaging import read_record
        from feature_rl.contracts import OperationResult
        if len(result.artifacts)!=2 or result.artifacts[-1].kind!='m6-frozen-grade':
            raise ValueError('control isolation requires a selected Factory grade result')
        frozen=read_record(self.grading.store,result.artifacts[-1],FrozenGrade,'m6-frozen-grade')
        job,_,request=validated(self.factory,frozen.claim)
        if (job.state!='completed' or job.result!=result or job.spec.invocation!=self.invocation
                or request.task_version!=self.task):
            raise ValueError('control isolation grade changed its selected child job')
        original=read_record(self.grading.store,frozen.original,OperationResult,'m6-grade-original')
        verify_result(self.factory,request,original)
        return original

    def validate_inputs(self, inputs):
        checked = self.checked
        if inputs.isolation_seeds != self.seeds:
            raise ValueError('control isolation schedule differs from frozen authoring inputs')
        if (inputs.isolation_task, inputs.baseline, inputs.contract, inputs.environment,
                inputs.scenario_plan) != (self.task, checked.task.baseline,
                checked.task.contract, checked.task.environment, checked.verifier.scenario_plan):
            raise ValueError('control isolation task differs from frozen authoring inputs')

    def prepare(self, prepared):
        from feature_rl.grading import read_grade
        from feature_rl.qualification.evidence import validate_grade
        store = self.grading.store
        # Publish only candidate source bytes. A failed candidate never gets an
        # accepted control record; real grade receipts remain durable evidence.
        for data, kind, visibility, ref in prepared.writes:
            if store.put_bytes(data, kind, visibility) != ref:
                raise ValueError('isolation candidate source identity changed')
        for seed in self.seeds:
            results = [result for result in self.retained()
                if (read_grade(store, result.artifacts[0]).submission,
                    read_grade(store, result.artifacts[0]).case_seed) == (prepared.record.control.patch, seed)]
            if len(results) > 1:
                raise ValueError('ambiguous retained control isolation execution')
            if results:
                result = results[0]
            else:
                from feature_rl.pipeline import FactoryRecoveryRequired,FactoryPublicationFailed,FactoryUpstreamPending
                try:
                    result = self.factory.grade(self.task, prepared.record.control.patch, seed,
                        invocation=self.invocation)
                except (FactoryPublicationFailed,FactoryUpstreamPending):
                    # Preserve exact upstream payloads for publication-only retry.
                    raise
                except FactoryRecoveryRequired as pending:
                    # A previously dispatched child may already have a durable
                    # outcome. Recovery never issues another worker execution.
                    result = self.factory.recover(pending.claim)
                self.observe(result)
            original=self.original(result)
            validate_grade(store, self.checked, prepared.record.control.patch, seed, original, self.grading)
            receipt = read_grade(store, result.artifacts[0])
            try:
                require_isolated_execution(store, self.checked, receipt,
                    prepared.record.control.requirement_ids)
            except ValueError as error:
                raise ValueError(f'{error}; grade_receipt={result.artifacts[0].sha256}') from error
        return prepared
